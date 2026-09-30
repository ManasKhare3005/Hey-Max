"""Memory and reminder tools: remember / recall / forget, set / list / cancel reminders."""
from __future__ import annotations

import datetime as dt
import re

from ..reminders import Reminders, parse_when, spoken_time
from .registry import DECLINED, ToolRegistry

DAY_WORDS = {"today": 0, "yesterday": 1}


def register(reg: ToolRegistry):
    ctx = reg.context
    store = getattr(ctx, "memory", None)
    if store is None:
        return
    reminders: Reminders = ctx.reminders

    @reg.tool(
        "Save a fact about the user for later, when they say 'remember ...' (e.g. 'my exam is on "
        "Friday', 'my sister's name is Priya'). Write it as a short standalone sentence.",
        params={"fact": {"type": "string", "description": "e.g. 'Manas's CSE 572 exam is on Friday, October 2'"}},
        direct=True,
    )
    def remember(fact: str):
        _, old = store.add_fact(fact)
        return "Got it, I've updated that." if old else "Got it, I'll remember that."

    @reg.tool(
        "Look up what you know or what was said before: saved facts, past conversations and "
        "reminders. Use for 'when is my exam', 'what did I ask you yesterday', 'what's my sister's name'.",
        params={"query": {"type": "string", "description": "What to look up"}},
    )
    def recall(query: str):
        lines = []
        facts = store.search_facts(query, k=5, min_score=0.5)
        if facts:
            lines.append("Saved facts: " + "; ".join(f.text for f in facts))
        day = next((d for w, d in DAY_WORDS.items() if re.search(rf"\b{w}\b", query, re.I)), None)
        if day is not None:
            start = (dt.date.today() - dt.timedelta(days=day)).isoformat()
            end = (dt.date.today() - dt.timedelta(days=day - 1)).isoformat()
            turns = store.turns_between(start, end)[-8:]
            if turns:
                lines.append("Conversations that day: " + " | ".join(f"{t['ts'][11:16]} you: {t['user']}" for t in turns))
        else:
            past = store.search_turns(query, k=4, min_score=0.6)
            if past:
                lines.append("Past conversations: " + " | ".join(f"{h.ts[:16].replace('T', ' ')} {h.text}" for h in past))
        rems = [r for r in reminders.pending()]
        if rems:
            lines.append("Pending reminders: " + "; ".join(f"{r.text} ({spoken_time(r.due)})" for r in rems[:5]))
        if not lines:
            return "Nothing saved about that."
        return "\n".join(lines) + "\n\nAnswer in one or two short sentences from this."

    @reg.tool(
        "Delete a saved fact when the user asks you to forget something. This tool is the only way "
        "to forget; it asks the user to confirm first.",
        params={"what": {"type": "string", "description": "Which fact, e.g. 'my exam date'"}},
        direct=True,
    )
    def forget(what: str):
        hits = store.search_facts(what, k=1, min_score=0.5)
        if not hits:
            return "I don't have anything saved about that."
        fact = hits[0]
        if not ctx.confirm(f"Forget that {fact.text}?"):
            return DECLINED
        store.delete_fact(fact.id)
        return "Forgotten."

    @reg.tool(
        "Set a reminder. Max will say it out loud and show a notification at that time. For "
        "'the day before X' or similar, work out the actual date yourself.",
        params={
            "text": {"type": "string", "description": "What to remind about, e.g. 'CSE 572 exam'"},
            "when": {"type": "string", "description": "e.g. 'Friday 9am', 'tomorrow morning', 'in 2 hours', or '2026-10-02T09:00'"},
        },
        direct=True,
    )
    def set_reminder(text: str, when: str):
        due = parse_when(when)
        if due is None:
            return f"Error: I couldn't understand the time '{when}'. Ask the user when exactly."
        if due <= dt.datetime.now():
            return f"Error: {spoken_time(due)} is in the past. Ask the user for a future time."
        reminders.add(text, due)
        return f"Okay, I'll remind you {spoken_time(due)}: {text}."

    @reg.tool("List the pending reminders.", params={}, direct=True)
    def list_reminders():
        items = reminders.pending()
        if not items:
            return "You don't have any reminders."
        parts = [f"{r.text}, {spoken_time(r.due)}" for r in items[:5]]
        more = f", and {len(items) - 5} more" if len(items) > 5 else ""
        return f"You have {len(items)} reminder{'s' if len(items) != 1 else ''}: " + "; ".join(parts) + more + "."

    @reg.tool(
        "Cancel a pending reminder.",
        params={"which": {"type": "string", "description": "Which reminder, e.g. 'the exam one'"}},
        direct=True,
    )
    def cancel_reminder(which: str):
        r = reminders.find(which)
        if r is None:
            return "You don't have any reminders."
        reminders.set_status(r.id, "cancelled")
        return f"Cancelled the reminder: {r.text}."
