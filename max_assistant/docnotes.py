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


def combined_title(items: list[tuple[Path, str]], course: str = "") -> str:
    """'CSE 579 combined summary': named after the course the files are in."""
    courses = {c for _, c in items if c}
    label = courses.pop() if len(courses) == 1 else course.strip() or "Course files"
    return f"{label} combined summary"


def body_of(notes_md: str) -> str:
    """A file's saved notes without the title line, the header and the exact-quote section
    (the combined summary quotes the files again itself)."""
    text = re.sub(r"\A# .*\n+(\*.*\*\n+)?", "", notes_md)
    return text.split("\n## Deadlines & dates (exact quotes)")[0].strip()


def write_combined(parts: list[tuple[str, str]], summarize: Callable[[str, str, int], str],
                   quotes: list[tuple[str, str]] = ()) -> str:
    """One set of notes from several files' notes: parts = [(file name, notes)]. Same merge/reduce
    as a single file, so it fits the small model however many files there are."""
    chunks = [f"### {name}\n{notes}" for name, notes in parts]
    while sum(len(c.split()) for c in chunks) > 1800 and len(chunks) > 1:
        chunks = [summarize(MERGE_PROMPT.format(kind="set of course documents"), "\n\n".join(chunks[i:i + 3]), 600).strip()
                  for i in range(0, len(chunks), 3)]
    notes = summarize(REDUCE_PROMPT.format(kind="set of course documents"), "\n\n".join(chunks), 1100).strip()
    if quotes:
        notes += "\n\n## Deadlines & dates (exact quotes)\n" + "\n".join(f"- [{loc}] “{q}”" for loc, q in quotes)
    return notes


def new_folder(folder: Path, title: str, when: dt.datetime) -> Path:
    base = folder / f"{when:%Y-%m-%d %H%M} {safe_name(title)}"
    target, n = base, 2
    while target.exists():
        target, n = Path(f"{base} ({n})"), n + 1
    target.mkdir(parents=True)
    return target


def save(result: DocResult, folder: Path, when: dt.datetime) -> Path:
    """Write notes.md + the extracted text into '<folder>/<date> <title>/'."""
    target = new_folder(folder, result.title, when)
    course = f"{result.course} · " if result.course else ""
    header = (f"# {result.title}\n\n*Document · {course}{result.source.name} · {result.pages} pages · "
              f"{result.words} words · notes written {when:%B %d, %Y}*\n\n")
    (target / "notes.md").write_text(header + result.notes_md + "\n", encoding="utf-8")
    (target / TEXT_FILE).write_text(f"# Text: {result.source.name}\n\n{result.text_md}\n", encoding="utf-8")
    result.folder = target
    return target


class DocNotes:
    """Queue of course files to write notes for, worked through one at a time in the background.
    `on_event("docnotes", {...})` fires per file (started, saved, failed); `on_done(done, failed)` at the end.
    A combined summary (one note for several files) waits until their own notes are written."""

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
        self._combine: deque[tuple[str, str, list[Path]]] = deque()     # (title, course, files)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.current = ""
        self.done: list[str] = []
        self.failed: list[str] = []
        self.combined: list[str] = []          # combined summaries written in the last run

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

    def file_notes(self, path: Path) -> str:
        """The saved notes for a file ('' if it has none yet)."""
        if not self.is_done(path):
            return ""
        note = self.ctx.memory.note(json.loads(self.ctx.memory.get(DONE_KEY + str(path.resolve())))["note"])
        f = Path(note["folder"]) / "notes.md"
        return body_of(f.read_text(encoding="utf-8")) if f.exists() else ""

    # ----- the queue -----
    def enqueue(self, items: list[tuple[Path, str]], redo: bool = False,
                combine: str = "") -> tuple[list[Path], list[Path]]:
        """Add files; returns (queued, skipped because their notes already exist). With `combine`
        (a title), one combined summary of all the files is written after their own notes."""
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
            if combine and combine not in {t for t, _, _ in self._combine}:
                self._combine.append((combine, items[0][1] if items else "", [p for p, _ in items]))
            if (queued or self._combine) and (self._worker is None or not self._worker.is_alive()):
                self.done, self.failed, self.combined = [], [], []
                self._worker = threading.Thread(target=self._work, name="doc-notes", daemon=True)
                self._worker.start()
        return queued, skipped

    def status(self) -> dict:
        with self._lock:
            return {"active": bool(self.current or self._queue or self._combine),
                    "current": Path(self.current).name if self.current else "",
                    "waiting": [p.name for p, _ in self._queue] + [t for t, _, _ in self._combine],
                    "done": list(self.done), "failed": list(self.failed)}

    def _work(self):
        while True:
            with self._lock:
                if self._queue:
                    path, course = self._queue.popleft()
                    self.current, job = str(path), None
                elif self._combine:
                    job = self._combine.popleft()
                    self.current = job[0]
                else:
                    self.current = ""
                    done, failed = list(self.done), list(self.failed)
                    break
            title = job[0] if job else path.stem
            self.on_event("docnotes", {"action": "started", "title": title})
            try:
                if job:
                    self._combined(*job)
                    with self._lock:
                        self.combined.append(title)
                else:
                    self._one(path, course)
                    with self._lock:
                        self.done.append(title)
            except Exception as exc:
                log.warning("notes for %s failed: %s", title, exc)
                with self._lock:
                    self.failed.append(f"{title} ({str(exc)[:80]})")
                self.on_event("docnotes", {"action": "failed", "title": title, "error": str(exc)[:200]})
        self.on_event("docnotes", {"action": "done", "done": len(done), "failed": len(failed)})
        self.on_done(done, failed)

    def _combined(self, title: str, course: str, paths: list[Path]):
        parts = [(p.stem, n) for p in paths if (n := self.file_notes(p))]
        if not parts:
            raise ValueError("none of the files have notes to combine")
        quotes = []
        for p in paths:
            try:
                items = [(loc, t) for loc, t in pages(p) if t.strip()]
            except Exception:
                continue
            quotes += [(f"{p.stem}, {loc}", q) for loc, q in date_quotes(items)]
        notes = write_combined(parts, self.summarize, quotes[:25])
        now = dt.datetime.now()
        target = new_folder(self.folder, title, now)
        where = f"{course} · " if course else ""
        names = ", ".join(name for name, _ in parts)
        header = (f"# {title}\n\n*Combined summary · {where}{len(parts)} documents · notes written "
                  f"{now:%B %d, %Y}*\n\n*Files: {names}*\n\n")
        (target / "notes.md").write_text(header + notes + "\n", encoding="utf-8")
        (target / TEXT_FILE).write_text("# Notes for each file\n\n" + "\n\n".join(f"## {name}\n\n{n}" for name, n in parts)
                                        + "\n", encoding="utf-8")
        summary = extract_summary(notes)
        mem = self.ctx.memory
        if mem is not None:
            stamp = now.isoformat(timespec="seconds")
            mem.add_notes(title, "document", stamp, stamp, str(target), summary, sum(len(n.split()) for _, n in parts))
        self.on_event("docnotes", {"action": "saved", "title": title, "folder": str(target), "summary": summary})

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
