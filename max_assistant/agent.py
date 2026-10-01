"""The agent: decides which tools to call, enforces the safety gate, escalates hard tasks."""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Callable

from .events import current_origin
from .llm import OllamaClient, OllamaError
from .tools.registry import DECLINED, ForLLM, ToolRegistry

log = logging.getLogger(__name__)

ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "think_harder",
        "description": (
            "Hand this request to a larger, smarter model. Use ONLY for tasks needing multi-step "
            "planning, careful reasoning, math, or writing more than a couple of sentences. "
            "Never use it for simple commands like opening apps, time, volume or notes."
        ),
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []},
    },
}

# Kept free of anything that changes per request (like the time): Ollama reuses the
# processed prompt prefix between requests, and the tool definitions come right after this.
SYSTEM_PROMPT = """You are {name}, a voice assistant running locally on {user}'s Windows laptop.
Each user message starts with the current date and time in [brackets]; use it when relevant.

How to behave:
- Your replies are spoken aloud. Keep them short: one or two sentences, no markdown, no lists, no emojis.
- When a request maps to a tool, call the tool instead of describing what you would do.
- Never say you did something (opened, clicked, went back, read) unless a tool did it in this turn.
- Remembering, forgetting and reminders only happen through the remember, forget, set_reminder
  and cancel_reminder tools. "(You remember: ...)" notes are just context, not actions.
- Websites and searches open in your own browser window. For anything about the page on screen
  (click, type, scroll, go back, read or summarize it, tabs), use the browser_* tools. You CAN
  click any button, including subscribe, sign in or buy: just call the tool, and it asks the
  user to confirm risky clicks itself. Don't refuse on your own.
- Call tools with exactly the arguments they define. Never invent file paths or results.
- After tools run, tell the user the outcome in plain words.
- If a tool result starts with "Error", explain the problem briefly.
- If the user declines a confirmation, acknowledge it and do nothing else.
- If you are unsure what the user wants, ask one short question.
- You may use {user}'s name now and then, but never add filler like "I'm here to help"; stop once the request is answered."""

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|confirm|confirmed|do it|go ahead|affirmative|ok|okay|please do)\b", re.I)
NO = re.compile(r"\b(no|nope|nah|cancel|stop|don't|do not|never mind|nevermind|abort)\b", re.I)
# Requests that may need several tool steps; don't short-cut these after the first tool
COMPOUND = re.compile(r"\b(and|then|also|after that|plus)\b", re.I)
# Replies that claim an action; if the matching tool wasn't called, nothing really happened
CLAIMS = {
    "forget": re.compile(r"\b(i('ve| have)? (forgotten|forgot|deleted|removed)|forgotten)\b", re.I),
    "cancel_reminder": re.compile(r"\bcancel(l?ed)?\b.*\bremind|\bremind\w*\b.*\bcancel(l?ed)?\b", re.I),
    "set_reminder": re.compile(r"\bi('ll| will) remind you\b", re.I),
    "remember": re.compile(r"\bi('ll| will) remember\b|\b(saved|noted) that\b", re.I),
    # "I'll open a search for cute dog photos" after a failed tool call: nothing was opened
    "open_website": re.compile(r"\bi('ll| will| am going to|'m going to) (open|search|pull up|bring up|show)\b|"
                               r"\b(i('ve| have) )?(opened|pulled up)\b|^\W*(opening|searching|pulling up)\b", re.I),
}
# Clear requests for those actions; if the reply neither used the tool nor asked a question, nudge
INTENTS = {
    "forget": re.compile(r"^\W*(please\s+|can you\s+|could you\s+)*forget\b", re.I),
    "cancel_reminder": re.compile(r"\b(cancel|delete|remove)\b.*\breminder\b", re.I),
    "set_reminder": re.compile(r"\bremind me\b", re.I),
    "remember": re.compile(r"^\W*(please\s+|can you\s+)*remember\s+(that|my|this)\b", re.I),
    "open_website": re.compile(r"\b(show|open|pull up|bring up|search|look up|find|google)\b.*"
                               r"\b(photos?|pictures?|pics?|images?|wallpapers?|website|site|web ?page)\b", re.I),
}
# Max's Chrome window is on the laptop: from the phone, pages are opened on the phone instead
PHONE_NOTE = ("(Sent from the user's phone. Websites, searches and videos: open_website, which opens them on the phone "
              "(e.g. 'open youtube' = open_website youtube.com; 'play lofi on youtube' = open_website target 'lofi' site 'youtube'). "
              "open_app only for apps on the laptop the user names.)")
PHONE_BROWSER = ("The user is on their phone and can't see the laptop's browser. To show them a page, a search or "
                 "images, call open_website: it opens on their phone.")
# "forget ..." goes straight to the forget tool: the model was unreliable here, and forgetting
# always asks the user to confirm, so a wrong match is harmless
FORGET = re.compile(r"^\W*(?:please\s+|can you\s+|could you\s+|max,?\s+)*forget\s+"
                    r"(?:about\s+|that\s+|what i (?:told|said to) you about\s+|the\s+)?(.+?)[.?!]*$", re.I)
NOTES_STOP = re.compile(r"\b(stop|end|finish)\s+(taking\s+)?(the\s+)?(notes|recording|note[- ]taking)\b|"
                        r"\b(the\s+)?(meeting|lecture|class|call)\s+(is\s+)?(over|done|finished|ended)\b", re.I)
NOTES_START = re.compile(r"\btake\s+(some\s+)?notes\b|\b(start|begin)\s+(taking\s+|recording\s+)?(the\s+)?notes\b|"
                         r"\brecord\s+(this|the|my)\s+(lecture|class|meeting|call|session|talk)\b|"
                         r"\bnotes\s+(for|on|of|during)\s+(this|the|my)\s+\w*\s*(lecture|class|meeting|call)\b", re.I)
MEETING_WORDS = re.compile(r"\b(meeting|zoom|teams|call|google meet|webex|video|webinar|stream)\b", re.I)


def direct_route(user_text: str, tools) -> tuple[str, dict] | None:
    """Requests the small model handled unreliably (claimed actions it didn't take), mapped straight
    to their tool. Each is safe to run without the model: forget confirms, notes can be stopped."""
    text = user_text.strip()
    if "forget" in tools and (m := FORGET.match(text)):
        return "forget", {"what": m.group(1)}
    if "stop_notes" in tools and NOTES_STOP.search(text):
        return "stop_notes", {}
    if "start_notes" in tools and NOTES_START.search(text):
        return "start_notes", {"kind": "meeting" if MEETING_WORDS.search(text) else "lecture"}
    return None


def parse_yes_no(text: str | None) -> bool:
    """Only an unambiguous yes counts. Silence or mixed answers are a no."""
    if not text:
        return False
    return bool(YES.search(text)) and not NO.search(text)


def strip_thinking(text: str) -> str:
    """Remove Qwen3 reasoning. Ollama sometimes leaks it with only the closing tag
    (the opening <think> lives in the prompt template), so drop everything up to
    the last </think>, and anything after an unclosed <think>."""
    text = text or ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*", "", text, flags=re.S)
    return text.strip()


class Agent:
    def __init__(
        self,
        llm: OllamaClient,
        registry: ToolRegistry,
        fast_model: str,
        planner_model: str,
        name: str = "Max",
        user_name: str = "there",
        max_steps: int = 6,
        confirm_risky: bool = True,
        confirm: Callable[[str], bool] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        history_turns: int = 6,
        history_ttl_s: float = 300,
        history_chars: int = 4000,
        recall: Callable[[str], str | None] | None = None,
        on_turn: Callable[[str, str, list[str]], None] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.fast_model = fast_model
        self.planner_model = planner_model
        self.name = name
        self.user_name = user_name
        self.max_steps = max_steps
        self.confirm_risky = confirm_risky
        self.confirm = confirm or (lambda prompt: False)
        self.on_event = on_event or (lambda kind, data: None)
        # One entry per turn: the user message, any tool calls/results, and the reply. Keeping
        # the tool calls matters: with text-only history the model starts imitating "just
        # answer" turns and claims actions it never performed.
        self.history: list[list[dict]] = []
        self.history_turns = history_turns
        self.history_ttl_s = history_ttl_s
        self.history_chars = history_chars    # ~1k tokens: system + tools already use ~2.3k of 4k
        self._last_turn = 0.0
        self.recall = recall          # relevant saved facts for a request (memory), or None
        self.on_turn = on_turn        # called after each turn: (user_text, answer, tools used)

    def _system(self) -> dict:
        content = SYSTEM_PROMPT.format(name=self.name, user=self.user_name)
        if not getattr(self.llm, "think", False):
            # Qwen3 soft switch; Ollama's think=false alone still leaks reasoning after tool calls
            content += "\n/no_think"
        return {"role": "system", "content": content}

    def _recent_history(self) -> list[dict]:
        if time.monotonic() - self._last_turn > self.history_ttl_s:
            self.history.clear()  # a new conversation after a long pause
        turns = self.history[-self.history_turns:]
        # Drop the oldest turns until it fits the budget; an overflowing context would make
        # Ollama cut the start of the prompt, i.e. the system instructions
        while len(turns) > 1 and sum(len(str(m.get("content", ""))) + 80 * len(m.get("tool_calls", []))
                                     for t in turns for m in t) > self.history_chars:
            turns = turns[1:]
        return [m for t in turns for m in t]

    @staticmethod
    def _for_history(msg: dict, limit: int = 240) -> dict:
        """Tool results can be long (web pages); history only needs the gist."""
        content = str(msg.get("content", ""))
        if msg.get("role") == "tool" and len(content) > limit:
            return {**msg, "content": content[:limit].rsplit(" ", 1)[0] + " …"}
        return msg

    def handle(self, user_text: str) -> str:
        d = dt.datetime.now()
        now = f"{d:%a %b} {d.day} {d.year}, {d.hour % 12 or 12}:{d:%M %p}"   # no leading zeros: read aloud
        content = f"[{now}] {user_text}"
        memories = self.recall(user_text) if self.recall else None
        if memories:
            # In the user message, not the system prompt, so the cached prompt prefix still matches
            content += f"\n(You remember: {memories})"
        if current_origin() == "phone":
            content += f"\n{PHONE_NOTE}"
        user_msg = {"role": "user", "content": content}
        messages = [self._system(), *self._recent_history(), user_msg]
        turn_start = len(messages) - 1
        nudged = False
        model = self.fast_model
        tools = self.registry.schemas() + [ESCALATE_TOOL]
        answer = None

        try:
            route = direct_route(user_text, self.registry.tools)
            if route:
                name, args = route
                self.on_event("thinking", {"model": "direct", "step": 0})
                messages.append({"role": "assistant", "content": "",
                                 "tool_calls": [{"function": {"name": name, "arguments": args}}]})
                result = self._run_call(name, args, model)
                messages.append({"role": "tool", "content": result, "tool_name": name})
                answer = "Okay, I won't." if result == DECLINED else result
            for step in range(0 if route else self.max_steps):
                self.on_event("thinking", {"model": model, "step": step})
                reply = self.llm.chat(model, messages, tools)

                if not reply.tool_calls:
                    answer = strip_thinking(reply.content)
                    claimed = self._false_claim(answer, messages[turn_start:], user_text)
                    if claimed and not nudged:
                        # The small model sometimes says "I've forgotten that" without calling
                        # the tool, so nothing happened. Tell it so, once.
                        nudged = True
                        log.info("reply claims %s without calling it; nudging", claimed)
                        messages += [{"role": "assistant", "content": answer},
                                     {"role": "user", "content": f"(System check: you said that, but you didn't call "
                                      f"the {claimed} tool, so nothing actually happened. Call {claimed} now; it looks things up "
                                      f"itself, even if you don't see the item in this conversation.)"}]
                        continue
                    break

                messages.append({
                    "role": "assistant",
                    "content": strip_thinking(reply.content),
                    "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}}
                                   for c in reply.tool_calls],
                })

                spoken: list[str] | None = []   # finished replies, while every call is a direct tool
                for call in reply.tool_calls:
                    result = self._run_call(call.name, call.arguments, model)
                    if result == "__ESCALATE__":
                        model = self.planner_model
                        tools = self.registry.schemas()
                        result = "You are now the larger model. Solve the user's request directly."
                    messages.append({"role": "tool", "content": result, "tool_name": call.name})
                    if spoken is not None:
                        spoken = self._direct_reply(call.name, result, spoken)

                if spoken and not COMPOUND.search(user_text):
                    # Tool results are already the answer ("Opened Spotify."): skip the LLM round trip
                    answer = " ".join(spoken)
                    self.on_event("direct_reply", {"text": answer})
                    break
            else:
                if not route:
                    answer = "Sorry, I got stuck on that one. Could you rephrase it?"
        except OllamaError as exc:
            log.error("LLM error: %s", exc)
            answer = str(exc)

        answer = answer or "Done."
        turn = [self._for_history(m) for m in messages[turn_start:]]
        self.history.append(turn + [{"role": "assistant", "content": answer}])
        self._last_turn = time.monotonic()
        self.on_event("answer", {"text": answer})
        if self.on_turn:
            tools_used = [c["function"]["name"] for m in turn for c in m.get("tool_calls", [])]
            try:
                self.on_turn(user_text, answer, tools_used)
            except Exception as exc:          # logging a turn must never break the reply
                log.warning("turn logging failed: %s", exc)
        return answer

    def _false_claim(self, answer: str, turn: list[dict], user_text: str = "") -> str | None:
        """A tool the reply claims to have used, or the user clearly asked for, that wasn't
        called this turn (so nothing actually happened). None if all is well."""
        called = {c["function"]["name"] for m in turn for c in m.get("tool_calls", [])}
        for tool, pattern in CLAIMS.items():
            if tool not in called and tool in self.registry.tools and pattern.search(answer):
                return tool
        if answer.rstrip().endswith("?"):       # asking for details is fine
            return None
        for tool, pattern in INTENTS.items():
            if tool not in called and tool in self.registry.tools and pattern.search(user_text):
                return tool
        return None

    def _direct_reply(self, name: str, result: str, spoken: list[str]) -> list[str] | None:
        """Add a tool's result to the spoken reply, or return None if the LLM must answer."""
        if result == DECLINED:
            return [*spoken, "Okay, I won't."]
        tool = self.registry.get(name)
        if tool is None or not tool.direct or result.startswith("Error") or isinstance(result, ForLLM):
            return None
        return [*spoken, result]

    def _run_call(self, name: str, args: dict, model: str) -> str:
        if name == "think_harder":
            if model == self.planner_model:
                return "You are already the larger model. Answer directly."
            self.on_event("escalate", {"to": self.planner_model})
            return "__ESCALATE__"

        tool = self.registry.get(name)
        if tool is None:
            return f"Error: there is no tool named {name}."

        if name.startswith("browser_") and current_origin() == "phone":
            log.info("tool %s skipped: phone command", name)
            return ForLLM(PHONE_BROWSER)

        if tool.risky and self.confirm_risky:
            prompt = tool.confirmation_prompt(args or {})
            self.on_event("confirm", {"tool": name, "args": args, "prompt": prompt})
            approved = self.confirm(prompt)
            self.on_event("confirm_result", {"tool": name, "approved": approved})
            if not approved:
                return DECLINED

        self.on_event("tool_call", {"tool": name, "args": args})
        result = self.registry.run(name, args or {})
        self.on_event("tool_result", {"tool": name, "result": result})
        log.info("tool %s(%s) -> %s", name, args, result[:200])
        return result
