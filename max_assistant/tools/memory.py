"""Memory, reminder and calendar tools: remember / recall / forget, set / list / cancel reminders,
add / list / cancel events (Max's calendar, which the phone shows as its "Max" calendar)."""
from __future__ import annotations

import datetime as dt
import re

from ..reminders import Reminders, has_time, parse_when, spoken_time
from .registry import DECLINED, ToolRegistry

DAY_WORDS = {"today": 0, "yesterday": 1}


def register(reg: ToolRegistry):
    ctx = reg.context
    store = getattr(ctx, "memory", None)
    if store is None:
        return
    reminders: Reminders = ctx.reminders

    def tell_phone(kind: str, **data):            # the phone copies the change on its next sync
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            bus.publish(kind, data)

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
        r = reminders.add(text, due)
        tell_phone("reminder", action="added", uid=r.uid, text=r.text, due=due.isoformat())
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
        tell_phone("reminder", action="cancelled", uid=r.uid)
        return f"Cancelled the reminder: {r.text}."

    events = getattr(ctx, "events", None)
    if events is None:
        return

    @reg.tool(
        "Add an event to the user's calendar (Max's calendar, also on their phone), e.g. 'add a study "
        "group tomorrow at 3pm', 'put the dentist on my calendar Friday at 10'. No time of day = all day. "
        "(Something Max should say out loud at a time is set_reminder instead.)",
        params={"title": {"type": "string"},
                "when": {"type": "string", "description": "Start, e.g. 'tomorrow at 3pm', 'Friday', '2026-10-02T15:00'"},
                "duration_minutes": {"type": "integer", "description": "Default 60"},
                "location": {"type": "string"}},
        required=["title", "when"],
        direct=True,
    )
    def add_event(title: str, when: str, duration_minutes: int = 60, location: str = ""):
        start = parse_when(when)
        if start is None:
            return f"Error: I couldn't understand the time '{when}'. Ask the user when exactly."
        all_day = not has_time(when)
        if (start.date() if all_day else start) < (dt.date.today() if all_day else dt.datetime.now()):
            return f"Error: {when} is in the past. Ask the user for a future time."
        e = events.add(title, start, duration_minutes or 60, location or "", all_day=all_day)
        tell_phone("event", action="added", uid=e.uid)
        return f"Added to your calendar: {e.spoken()}."

    @reg.tool(
        "List calendar events: on a day ('today', 'tomorrow', 'Friday') or, without a day, the next week.",
        params={"day": {"type": "string", "description": "Optional, e.g. 'tomorrow'"}},
        required=[],
        direct=True,
    )
    def list_events(day: str = ""):
        if day.strip():
            when = parse_when(day)
            if when is None:
                return f"Error: I couldn't understand the day '{day}'."
            items, span = events.on(when.date()), ("today" if when.date() == dt.date.today() else
                                                    "tomorrow" if when.date() == dt.date.today() + dt.timedelta(days=1)
                                                    else f"on {when:%A}")
        else:
            items, span = events.upcoming(7), "this coming week"
        if not items:
            return f"Nothing on your calendar {span}."
        return f"On your calendar {span}: " + "; ".join(e.spoken() for e in items[:6]) + (
            f", and {len(items) - 6} more" if len(items) > 6 else "") + "."

    @reg.tool(
        "Remove an event from the user's calendar.",
        params={"which": {"type": "string", "description": "Which event, e.g. 'the dentist'"}},
        direct=True,
    )
    def cancel_event(which: str):
        e = events.find(which)
        if e is None:
            return "There's nothing coming up on your calendar."
        events.cancel(e.uid)
        tell_phone("event", action="cancelled", uid=e.uid)
        return f"Removed from your calendar: {e.spoken()}."
