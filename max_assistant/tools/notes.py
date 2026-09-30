"""Meeting / lecture notes tools: start and stop recording, check status, ask about past notes."""
from __future__ import annotations

from pathlib import Path

from ..notes import clock
from .registry import ToolRegistry


def register(reg: ToolRegistry):
    ctx = reg.context
    mgr = getattr(ctx, "notes", None)
    if mgr is None:
        return

    @reg.tool(
        "Start listening to a meeting or an in-person lecture and write notes when it ends. 'meeting' "
        "records the laptop's audio (Zoom, Teams, YouTube) plus the mic; 'lecture' records the mic. Use for "
        "'take notes', 'record this lecture', 'start meeting notes'.",
        params={
            "kind": {"type": "string", "enum": ["lecture", "meeting"],
                     "description": "'meeting' if it's on the laptop (Zoom, Teams, a video), else 'lecture'"},
            "title": {"type": "string", "description": "Optional, e.g. 'CSE 573 lecture'"},
        },
        required=["kind"],
        direct=True,
    )
    def start_notes(kind: str = "lecture", title: str = ""):
        if mgr.active:
            return "I'm already taking notes. Say 'stop taking notes' when it's over."
        try:
            s = mgr.start(kind, title)
        except Exception as exc:
            return f"Error: couldn't start recording: {exc}."
        where = "the laptop audio and your mic" if s.kind == "meeting" else "the room through the mic"
        return f"Taking notes on {where}. Say 'stop taking notes' when it's over."

    @reg.tool(
        "Stop recording the meeting or lecture and write up the notes. Use for 'stop taking notes', "
        "'the lecture is over', 'stop recording'.",
        params={},
        direct=True,
    )
    def stop_notes():
        if not mgr.active:
            return "I'm not taking notes right now."
        st = mgr.status()
        mgr.stop()
        return f"Stopped after {clock(st['elapsed_s'])}. I'm writing up the notes and will tell you when they're ready."

    @reg.tool(
        "Look up notes from recorded meetings or lectures: summary, key points, deadlines. Use for 'what "
        "were the key points of today's lecture', 'what did we decide in the meeting', 'any deadlines from class'.",
        params={"which": {"type": "string", "description": "'latest', or words from the title/topic"}},
        required=[],
    )
    def meeting_notes(which: str = "latest"):
        if ctx.memory is None:
            return "Error: memory is off, so notes aren't indexed."
        items = ctx.memory.notes(30)
        if mgr.active:
            st = mgr.status()
            live = f"(Currently recording '{st['title']}', {clock(st['elapsed_s'])} so far.)\n"
        else:
            live = ""
        if not items:
            return live + "No notes have been recorded yet."
        pick = items[0]
        words = [w for w in which.lower().split() if w not in ("latest", "last", "the", "notes")]
        if words:
            scored = sorted(items, key=lambda n: -sum(w in (n["title"] + " " + n["summary"]).lower() for w in words))
            pick = scored[0]
        text = Path(pick["folder"], "notes.md").read_text(encoding="utf-8") if Path(pick["folder"], "notes.md").exists() \
            else pick["summary"]
        return (f"{live}Notes '{pick['title']}' ({pick['started'][:16].replace('T', ' ')}):\n{text[:2600]}\n\n"
                "Answer the user's question in one to three short spoken sentences from these notes.")
