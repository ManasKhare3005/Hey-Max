"""Study notes from documents: read a course file (PDF, slides, Word, text) in full and write
notes for it, saved next to the meeting and lecture notes so they show in the Notes tab.

Same map/reduce as lecture notes (the 4b model sees ~4k tokens): the text is cut into
~700-word sections by page, each section gets bullet notes, and the bullets are combined into
the final notes. Short files skip the first step. Sentences that mention a deadline and a date
are copied verbatim, never rewritten. Files are done one at a time in a background thread.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .course import pages
from .notes import DEADLINE_WORDS, MERGE_PROMPT, REDUCE_PROMPT, WHEN_WORDS, extract_summary, safe_name

log = logging.getLogger(__name__)
TEXT_FILE = "document-text.md"        # the extracted text, shown as the "Text" view (course search skips it)
DONE_KEY = "docnote:"                 # kv: file path -> {"mtime", "note"} so files aren't summarized twice

DOC_MAP_PROMPT = (
    "You are taking study notes for a student. Below is part {n} of the course document '{title}' ({where}). "
    "Write concise bullet-point notes: main ideas, definitions, formulas, examples, and any deadlines, due "
    "dates, exams or requirements (dates exactly as written). Only use what is in the text. No introduction."
)


@dataclass
class DocResult:
    title: str
    source: Path
    course: str
    notes_md: str
    text_md: str
    words: int
    pages: int
    folder: Path | None = None


def sections(items: list[tuple[str, str]], words: int = 700) -> list[tuple[str, str]]:
    """Group pages into sections of up to `words` words: [(where, text)], e.g. ("p. 3-5", ...)."""
    out, buf, first, last = [], [], "", ""

    def flush():
        if buf:
            where = first if first == last else f"{first}-{last.split()[-1]}"
            out.append((where, " ".join(buf)))

    for loc, text in items:
        w = text.split()
        while w:
            if not buf:
                first = loc
            room = words - len(buf)
            buf += w[:room]
            w = w[room:]
            last = loc
            if len(buf) >= words:
                flush()
                buf = []
    flush()
    return out


def date_quotes(items: list[tuple[str, str]], limit: int = 12) -> list[tuple[str, str]]:
    """Sentences that mention a deadline-ish word and a date/time, quoted exactly with their page."""
    out, seen = [], set()
    for loc, text in items:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            s = " ".join(sentence.split())
            if 8 <= len(s) <= 300 and DEADLINE_WORDS.search(s) and WHEN_WORDS.search(s) and s.lower() not in seen:
                seen.add(s.lower())
                out.append((loc, s))
                if len(out) == limit:
                    return out
    return out


def write_notes(path: Path, summarize: Callable[[str, str, int], str], course: str = "") -> DocResult:
    """Read the whole file and write its notes (Markdown). Raises ValueError if it has no text."""
    items = [(loc, t) for loc, t in pages(path) if t.strip()]
    words = sum(len(t.split()) for _, t in items)
    if words < 5:
        raise ValueError("no readable text (a scanned PDF or images only?)")
    title = path.stem
    parts = sections(items)
    if len(parts) == 1:                                   # short: straight to the final notes
        notes_parts = [parts[0][1]]
    else:
        notes_parts = []
        for i, (where, text) in enumerate(parts):
            try:
                notes_parts.append(summarize(DOC_MAP_PROMPT.format(n=i + 1, title=title, where=where), text, 450).strip())
            except Exception as exc:
                log.warning("section notes failed (%s, %s): %s", path.name, where, exc)
                notes_parts.append(text[:1500])
    while sum(len(p.split()) for p in notes_parts) > 1800 and len(notes_parts) > 1:
        notes_parts = [summarize(MERGE_PROMPT.format(kind="document"), "\n\n".join(notes_parts[i:i + 3]), 500).strip()
                       for i in range(0, len(notes_parts), 3)]
    notes = summarize(REDUCE_PROMPT.format(kind="course document"), "\n\n".join(notes_parts), 1100).strip()
    quotes = date_quotes(items)
    if quotes:
        notes += "\n\n## Deadlines & dates (exact quotes)\n" + "\n".join(f"- [{loc}] “{q}”" for loc, q in quotes)
    text_md = "\n\n".join(f"### {loc}\n{t.strip()}" for loc, t in items)
    return DocResult(title, path, course, notes, text_md, words, len(items))


def save(result: DocResult, folder: Path, when: dt.datetime) -> Path:
    """Write notes.md + the extracted text into '<folder>/<date> <title>/'."""
    base = folder / f"{when:%Y-%m-%d %H%M} {safe_name(result.title)}"
    target, n = base, 2
    while target.exists():
        target, n = Path(f"{base} ({n})"), n + 1
    target.mkdir(parents=True)
    course = f"{result.course} · " if result.course else ""
    header = (f"# {result.title}\n\n*Document · {course}{result.source.name} · {result.pages} pages · "
              f"{result.words} words · notes written {when:%B %d, %Y}*\n\n")
    (target / "notes.md").write_text(header + result.notes_md + "\n", encoding="utf-8")
    (target / TEXT_FILE).write_text(f"# Text: {result.source.name}\n\n{result.text_md}\n", encoding="utf-8")
    result.folder = target
    return target


class DocNotes:
    """Queue of course files to write notes for, worked through one at a time in the background.
    `on_event("docnotes", {...})` fires per file (started, saved, failed); `on_done(done, failed)` at the end."""

    def __init__(self, ctx, library, folder: Path, summarize: Callable[[str, str, int], str],
                 on_event: Callable[[str, dict], None] | None = None,
                 on_done: Callable[[list[str], list[str]], None] | None = None):
        self.ctx = ctx
        self.library = library                 # course.CourseLibrary (lists the files)
        self.folder = Path(folder)             # where notes are saved (the Max Notes folder)
        self.summarize = summarize
        self.on_event = on_event or (lambda kind, data: None)
        self.on_done = on_done or (lambda done, failed: None)
        self._queue: deque[tuple[Path, str]] = deque()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.current = ""
        self.done: list[str] = []
        self.failed: list[str] = []

    # ----- which files -----
    def files(self) -> list[tuple[Path, str]]:
        """(path, course) for every course file, not counting saved notes."""
        notes = self.folder.resolve()
        return [(p, c) for p, c in self.library.files() if notes not in p.resolve().parents]

    def match(self, course: str = "", name: str = "") -> list[tuple[Path, str]]:
        items = self.files()
        if course:
            key = course.lower().replace(" ", "")
            items = [(p, c) for p, c in items if key in c.lower().replace(" ", "") or key in str(p).lower().replace(" ", "")]
        words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in VAGUE]
        if words:
            score = lambda p: sum(w in p.stem.lower() for w in words)
            best = max((score(p) for p, _ in items), default=0)
            items = [(p, c) for p, c in items if best and score(p) == best]
        return items

    def is_done(self, path: Path) -> bool:
        mem = self.ctx.memory
        raw = mem.get(DONE_KEY + str(path.resolve())) if mem is not None else None
        if not raw:
            return False
        info = json.loads(raw)
        note = mem.note(info.get("note", -1))
        return note is not None and Path(note["folder"]).exists() and abs(info.get("mtime", 0) - path.stat().st_mtime) < 1

    # ----- the queue -----
    def enqueue(self, items: list[tuple[Path, str]], redo: bool = False) -> tuple[list[Path], list[Path]]:
        """Add files; returns (queued, skipped because their notes already exist)."""
        queued, skipped = [], []
        with self._lock:
            waiting = {p for p, _ in self._queue} | ({Path(self.current)} if self.current else set())
            for p, c in items:
                if p in waiting:
                    continue
                if not redo and self.is_done(p):
                    skipped.append(p)
                    continue
                self._queue.append((p, c))
                queued.append(p)
            if queued and (self._worker is None or not self._worker.is_alive()):
                self.done, self.failed = [], []
                self._worker = threading.Thread(target=self._work, name="doc-notes", daemon=True)
                self._worker.start()
        return queued, skipped

    def status(self) -> dict:
        with self._lock:
            return {"active": bool(self.current or self._queue), "current": Path(self.current).name if self.current else "",
                    "waiting": [p.name for p, _ in self._queue], "done": list(self.done), "failed": list(self.failed)}

    def _work(self):
        while True:
            with self._lock:
                if not self._queue:
                    self.current = ""
                    done, failed = list(self.done), list(self.failed)
                    break
                path, course = self._queue.popleft()
                self.current = str(path)
            self.on_event("docnotes", {"action": "started", "title": path.stem})
            try:
                self._one(path, course)
                with self._lock:
                    self.done.append(path.stem)
            except Exception as exc:
                log.warning("notes for %s failed: %s", path.name, exc)
                with self._lock:
                    self.failed.append(f"{path.stem} ({str(exc)[:80]})")
                self.on_event("docnotes", {"action": "failed", "title": path.stem, "error": str(exc)[:200]})
        self.on_event("docnotes", {"action": "done", "done": len(done), "failed": len(failed)})
        self.on_done(done, failed)

    def _one(self, path: Path, course: str) -> DocResult:
        now = dt.datetime.now()
        result = write_notes(path, self.summarize, course)
        save(result, self.folder, now)
        summary = extract_summary(result.notes_md)
        mem = self.ctx.memory
        if mem is not None:
            stamp = now.isoformat(timespec="seconds")
            note_id = mem.add_notes(result.title, "document", stamp, stamp, str(result.folder), summary, result.words)
            mem.set(DONE_KEY + str(path.resolve()), json.dumps({"mtime": path.stat().st_mtime, "note": note_id}))
        self.on_event("docnotes", {"action": "saved", "title": result.title,
                                "folder": str(result.folder), "summary": summary})
        return result


VAGUE = {"all", "each", "every", "everything", "file", "files", "the", "my", "of", "them", "one", "and", "pdf", "pdfs",
         "slides", "document", "documents", "docs", "course", "folder", "max", "material", "in", "a"}
