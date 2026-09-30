"""Canvas tools: what's due and when classes are, from the Canvas calendar feed."""
from __future__ import annotations

from ..canvas import PERIODS, CanvasFeed, describe_due, period_range
from .registry import ToolRegistry


def register(reg: ToolRegistry):
    ctx = reg.context
    url = ((ctx.cfg.get("secrets", {}) or {}).get("canvas", {}) or {}).get("ics_url") if ctx else None
    if not url:
        return
    feed = CanvasFeed(url)
    ctx.canvas = feed

    @reg.tool(
        "What's due on Canvas (ASU coursework: assignments, labs, quizzes, exams) for a period. "
        "Use for 'what's due today', 'what assignments do I have this week', 'anything due tomorrow'.",
        params={"period": {"type": "string", "enum": list(PERIODS), "description": "Default: today"}},
        required=[],
        direct=True,
    )
    def canvas_due(period: str = "today"):
        period = period if period in PERIODS else "today"
        try:
            start, end = period_range(period)
            return describe_due(feed.between(start, end, "assignment"), period)
        except Exception as exc:
            return f"Error: couldn't reach Canvas ({exc})."

    @reg.tool(
        "Class sessions from Canvas (times and Zoom links) for a period. Use for 'when is my next "
        "class', 'do I have class today'.",
        params={"period": {"type": "string", "enum": list(PERIODS), "description": "Default: today"}},
        required=[],
    )
    def canvas_classes(period: str = "today"):
        period = period if period in PERIODS else "today"
        try:
            start, end = period_range(period)
            classes = feed.between(start, end, "class")
        except Exception as exc:
            return f"Error: couldn't reach Canvas ({exc})."
        if not classes:
            return f"No classes {period.replace('_', ' ')} on the Canvas calendar."
        lines = [f"{c.course} {c.title}: {c.start.strftime('%A')} {c.when}" + (" (Zoom)" if c.link else "")
                 for c in classes[:8]]
        return "Classes:\n" + "\n".join(lines) + "\n\nAnswer in one or two short spoken sentences."
