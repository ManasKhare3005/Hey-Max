"""Phone tools: Max acts on the user's Android phone through the phone app (see phone.py).

Calls ask first (risky). Texts, WhatsApp, email and calendar events open ready to go and the
user taps Send / Save on the phone, which is the confirmation. Reading notifications, contacts
and battery stays between the phone and this laptop.
"""
from __future__ import annotations

import re

from .registry import ToolRegistry
from .system import best_match

UNITS = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10,
         "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60, "half an": 0.5}


def parse_duration(text: str) -> int | None:
    """'10 minutes', '1 hour 30 min', 'an hour', 'half an hour', '90s' -> seconds."""
    t = text.lower().replace("half an hour", "30 minutes").replace("half hour", "30 minutes")
    for word, n in WORDS.items():
        t = re.sub(rf"\b{re.escape(word)}\b(?=\s+(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b)", str(n), t)
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", t):
        total += float(num) * UNITS[unit]
    if not total and re.fullmatch(r"\s*\d+\s*", t):
        total = int(t) * 60                       # a bare number: minutes
    return int(total) if total > 0 else None


def register(reg: ToolRegistry):
    ctx = reg.context
    phone = getattr(ctx, "phone", None) if ctx else None
    if phone is None:
        return

    def ask(action: str, **params) -> str:
        r = phone.request(action, params)
        return r.get("message") or ("Done." if r.get("ok") else "That didn't work on the phone.")

    @reg.tool(
        "Open an app on the user's PHONE (Instagram, WhatsApp, Spotify, Camera, Gmail...). "
        "For laptop apps use open_app instead.",
        params={"name": {"type": "string", "description": "App name as the user said it"}},
        direct=True,
    )
    def phone_open_app(name: str):
        apps = phone.apps()
        label = best_match(name, list(apps)) if apps else None
        return ask("open_app", name=name, package=apps.get(label, "") if label else "")

    @reg.tool(
        "Phone call from the user's phone to a contact (name as saved in their contacts) or a number.",
        params={"contact": {"type": "string", "description": "Contact name or phone number, e.g. 'Mom'"}},
        risky=True,
        confirm="Call {contact} from your phone?",
        direct=True,
    )
    def phone_call(contact: str):
        return ask("call", contact=contact)

    @reg.tool(
        "Write a text message (SMS) or WhatsApp message on the user's phone. It opens ready to send and the "
        "user taps Send.",
        params={
            "contact": {"type": "string", "description": "Contact name or number"},
            "text": {"type": "string", "description": "The message, written as the user would send it"},
            "app": {"type": "string", "enum": ["sms", "whatsapp"], "description": "Default sms"},
        },
        required=["contact", "text"],
        direct=True,
    )
    def phone_message(contact: str, text: str, app: str = "sms"):
        return ask("message", contact=contact, text=text, app=app if app in ("sms", "whatsapp") else "sms")

    @reg.tool(
        "Set an alarm on the user's phone ('wake me at 7', 'alarm for 6:30 tomorrow').",
        params={"time": {"type": "string", "description": "When, e.g. '7am', '6:30', 'tomorrow 8:15'"},
                "label": {"type": "string", "description": "Optional name for the alarm"}},
        required=["time"],
        direct=True,
    )
    def phone_alarm(time: str, label: str = ""):
        from ..reminders import parse_when

        when = parse_when(time)
        if when is None:
            return f"Error: couldn't understand the time '{time}'."
        return ask("alarm", hour=when.hour, minute=when.minute, label=label)

    @reg.tool(
        "Start a countdown timer on the user's phone ('10 minute timer', 'timer for an hour and a half').",
        params={"duration": {"type": "string", "description": "e.g. '10 minutes', '1 hour 30 minutes'"},
                "label": {"type": "string", "description": "Optional, e.g. 'pasta'"}},
        required=["duration"],
        direct=True,
    )
    def phone_timer(duration: str, label: str = ""):
        seconds = parse_duration(duration)
        if not seconds:
            return f"Error: couldn't understand '{duration}' as a duration."
        return ask("timer", seconds=seconds, label=label)

    @reg.tool(
        "Start Google Maps navigation on the user's phone to a place or address.",
        params={"destination": {"type": "string"},
                "mode": {"type": "string", "enum": ["driving", "walking", "transit", "bicycling"], "description": "Default driving"}},
        required=["destination"],
        direct=True,
    )
    def phone_navigate(destination: str, mode: str = "driving"):
        return ask("navigate", destination=destination, mode=mode)

    @reg.tool(
        "Play music or a podcast in a music app on the user's phone ('play lofi on Spotify').",
        params={"query": {"type": "string", "description": "Song, artist, playlist or genre"},
                "app": {"type": "string", "enum": ["spotify", "youtube_music", "youtube"], "description": "Default spotify"}},
        required=["query"],
        direct=True,
    )
    def phone_play(query: str, app: str = "spotify"):
        return ask("play", query=query, app=app)

    @reg.tool(
        "Add an event to the calendar on the user's phone. It opens filled in and the user taps Save. "
        "(Reminders that Max should announce are set_reminder instead.)",
        params={"title": {"type": "string"},
                "when": {"type": "string", "description": "Start, e.g. 'tomorrow at 3pm'"},
                "duration_minutes": {"type": "integer", "description": "Default 60"},
                "location": {"type": "string"}},
        required=["title", "when"],
        direct=True,
    )
    def phone_calendar_add(title: str, when: str, duration_minutes: int = 60, location: str = ""):
        from ..reminders import parse_when

        start = parse_when(when)
        if start is None:
            return f"Error: couldn't understand the time '{when}'."
        begin = int(start.timestamp() * 1000)
        return ask("calendar", title=title, begin=begin, end=begin + int(duration_minutes or 60) * 60_000, location=location)

    @reg.tool(
        "Write an email in Gmail on the user's phone; it opens ready to send and the user taps Send.",
        params={"to": {"type": "string", "description": "Contact name or email address"},
                "subject": {"type": "string"}, "body": {"type": "string"}},
        required=["to"],
        direct=True,
    )
    def phone_email(to: str, subject: str = "", body: str = ""):
        return ask("email", to=to, subject=subject, body=body)

    @reg.tool(
        "Read the user's recent phone notifications (messages, WhatsApp, Instagram, email...). Use for "
        "'what did I miss', 'read my notifications', 'what did my last WhatsApp say'. Summarise briefly.",
        params={"app": {"type": "string", "description": "Only this app, e.g. 'WhatsApp' (optional)"},
                "count": {"type": "integer", "description": "How many, default 8"}},
        required=[],
    )
    def phone_notifications(app: str = "", count: int = 8):
        if app.strip().lower() in ("all", "any", "every", "everything", "all apps", "phone", "notifications"):
            app = ""                     # the model sometimes says app="all"
        return ask("notifications", app=app, count=max(1, min(int(count or 8), 20)))

    @reg.tool(
        "The user's phone status: battery level, charging, network.",
        params={},
        direct=True,
    )
    def phone_status():
        return ask("status")
