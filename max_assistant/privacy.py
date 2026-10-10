"""Privacy page: everything Max keeps, where it lives, and how to export or delete it.

All of it is on this laptop. Deleting is only offered from the laptop itself (never over the
phone link), and each category must be named again to confirm.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import zipfile
from pathlib import Path

from .config import resolve_path


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return 0


class Privacy:
    def __init__(self, ctx):
        self.ctx = ctx
        cfg = ctx.cfg
        self.quick_notes = resolve_path((cfg.get("notes", {}) or {}).get("file", "data/notes.md"))
        self.notes_dir = Path(os.path.expanduser((cfg.get("notes", {}) or {}).get("folder", "~/Documents/Max Notes")))
        self.browser_dir = resolve_path((cfg.get("browser", {}) or {}).get("profile_dir", "data/browser-profile"))
        self.logs_dir = resolve_path((cfg.get("logging", {}) or {}).get("file", "logs/max.log")).parent
        self.token_file = resolve_path((cfg.get("phone", {}) or {}).get("token_file", "data/phone_token.txt"))
        self.voiceprint = resolve_path((cfg.get("voice_id", {}) or {}).get("profile", "data/voice_profile.npy"))
        course = getattr(ctx, "course", None)
        self.course_index = getattr(course, "index_path", None)

    def categories(self) -> list[dict]:
        m = self.ctx.memory
        n = (lambda t: m.count(t)) if m is not None else (lambda t: 0)
        cats = [
            ("facts", "Facts", "Things you asked Max to remember", "data/max.db", f"{n('facts')} facts"),
            ("conversations", "Conversations", "What you said and Max's replies (searchable history)", "data/max.db", f"{n('turns')} turns"),
            ("actions", "Action log", "Tools Max used and approvals", "data/max.db", f"{n('actions')} entries"),
            ("reminders", "Reminders", "Pending, done and cancelled reminders", "data/max.db", f"{n('reminders')} reminders"),
            ("events", "Calendar events", "Events in Max's calendar (the phone's Max calendar follows)", "data/max.db",
             f"{n('events')} events"),
            ("notes", "Meeting & lecture notes", "Notes, transcripts and summaries (no audio is ever kept)", str(self.notes_dir),
             f"{n('notes')} sessions · {_size(self.notes_dir) // 1024} KB"),
            ("quick_notes", "Quick notes", "“Take a note: …”", str(self.quick_notes), f"{_size(self.quick_notes) // 1024} KB"),
            ("browser", "Max's browser", "Cookies, sign-ins and history of Max's own Chrome window", str(self.browser_dir),
             f"{_size(self.browser_dir) // 2**20} MB"),
            ("logs", "Logs", "Diagnostics; can include what you said", str(self.logs_dir), f"{_size(self.logs_dir) // 1024} KB"),
            ("state", "Small settings", "When the digest and deadline alerts last went out", "data/max.db", f"{n('kv')} values"),
            ("voice", "Voiceprint", "Numbers describing your voice for voice ID (no recordings)", str(self.voiceprint),
             "enrolled" if self.voiceprint.exists() else "not enrolled"),
            ("phone", "Phone pairing", "Deleting makes a new token: the phone must pair again", str(self.token_file),
             "paired" if self.token_file.exists() else "not set up"),
        ]
        if self.course_index is not None:
            cats.insert(5, ("course", "Course material index", "Text extracted from your course files (the files stay where they are)",
                            str(self.course_index), f"{_size(Path(self.course_index)) // 1024} KB"))
        return [{"id": i, "label": l, "what": w, "where": p, "amount": a} for i, l, w, p, a in cats]

    def export(self) -> bytes:
        """One zip: the database tables as JSON, quick notes, and notes folders. Not the browser
        profile or the phone token (secrets), nor secrets.yaml."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            m = self.ctx.memory
            if m is not None:
                for table in m.TABLES:
                    z.writestr(f"{table}.json", json.dumps(m.dump(table), indent=2, ensure_ascii=False))
                for row in m.dump("notes"):
                    folder = Path(row["folder"])
                    if folder.is_dir():
                        for f in folder.iterdir():
                            if f.is_file():
                                z.write(f, f"notes/{folder.name}/{f.name}")
            if self.quick_notes.exists():
                z.write(self.quick_notes, "quick_notes.md")
            z.writestr("README.txt", "Everything Max stored, exported from the privacy page. Tables are JSON; "
                                     "notes are Markdown. Browser data, the phone token and secrets are not included.\n")
        return buf.getvalue()

    def delete(self, category: str) -> str:
        m = self.ctx.memory
        tables = {"facts": "facts", "conversations": "turns", "actions": "actions", "reminders": "reminders",
                  "events": "events", "state": "kv"}
        if category in tables:
            if m is None:
                return "Memory is off."
            m.clear(tables[category])
            return f"Deleted all {category}."
        if category == "notes":
            removed = 0
            if m is not None:
                base = self.notes_dir.resolve()
                for row in m.dump("notes"):
                    folder = Path(row["folder"]).resolve()
                    if folder.is_dir() and base in folder.parents:      # only folders Max made, inside its notes folder
                        shutil.rmtree(folder, ignore_errors=True)
                        removed += 1
                m.clear("notes")
            return f"Deleted {removed} notes folders."
        if category == "quick_notes":
            self.quick_notes.unlink(missing_ok=True)
            return "Deleted quick notes."
        if category == "browser":
            browser = getattr(self.ctx, "browser", None)
            if browser is not None and getattr(browser, "running", False):
                browser.close()
            shutil.rmtree(self.browser_dir, ignore_errors=True)
            return "Deleted Max's browser data."
        if category == "logs":
            for f in self.logs_dir.glob("*.log"):
                try:
                    f.write_text("", encoding="utf-8")           # truncate: the logger keeps the file open
                except OSError:
                    pass
            return "Cleared the logs."
        if category == "course":
            course = getattr(self.ctx, "course", None)
            if course is not None:
                course.clear()
            return "Deleted the course material index (your files are untouched)."
        if category == "voice":
            self.voiceprint.unlink(missing_ok=True)
            return "Deleted your voiceprint. Voice ID is off until you enrol again (run.bat --enroll-voice)."
        if category == "phone":
            self.token_file.unlink(missing_ok=True)
            return "Removed the phone token. Restart Max to make a new one, then pair the phone again."
        raise KeyError(category)
