"""The agent: decides which tools to call, enforces the safety gate, escalates hard tasks."""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Callable

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
        user_msg = {"role": "user", "content": f"[{now}] {user_text}"}
        messages = [self._system(), *self._recent_history(), user_msg]
        turn_start = len(messages) - 1
        model = self.fast_model
        tools = self.registry.schemas() + [ESCALATE_TOOL]
        answer = None

        try:
            for step in range(self.max_steps):
                self.on_event("thinking", {"model": model, "step": step})
                reply = self.llm.chat(model, messages, tools)

                if not reply.tool_calls:
                    answer = strip_thinking(reply.content)
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
                answer = "Sorry, I got stuck on that one. Could you rephrase it?"
        except OllamaError as exc:
            log.error("LLM error: %s", exc)
            answer = str(exc)

        answer = answer or "Done."
        turn = [self._for_history(m) for m in messages[turn_start:]]
        self.history.append(turn + [{"role": "assistant", "content": answer}])
        self._last_turn = time.monotonic()
        self.on_event("answer", {"text": answer})
        return answer

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
