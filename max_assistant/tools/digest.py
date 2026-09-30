"""Day overview tools: spoken summary of the day, and emailing the daily digest on demand."""
from __future__ import annotations

from ..digest import gather, send_digest, spoken
from .registry import ToolRegistry


def register(reg: ToolRegistry):
    ctx = reg.context
    if ctx is None or (ctx.canvas is None and ctx.reminders is None):
        return

    @reg.tool(
        "Summary of the user's day: what's due, classes and reminders. Use for 'what does my day "
        "look like', 'what's on today', 'brief me'.",
        params={},
        direct=True,
    )
    def day_summary():
        return spoken(gather(ctx))

    @reg.tool(
        "Email the user today's digest (due items, classes, reminders) right now. It only ever goes "
        "to the user's own address.",
        params={},
        direct=True,
    )
    def email_digest():
        try:
            subject = send_digest(ctx)
        except Exception as exc:
            return f"Error: I couldn't send it: {exc}."
        return "Sent. Check your inbox."
