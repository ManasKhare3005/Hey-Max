"""Lectures and meetings recorded on the phone (while the laptop was off or away).

The phone records on its own (AAC), keeps the file, and uploads it the next time it reaches
the laptop: POST /api/notes/upload?uid=... The laptop saves it to data/phone-recordings and
writes the notes one recording at a time, with the same pipeline as a live session
(notes.NoteSession: Whisper in ~30 s chunks, section notes, final write-up, exact deadline
quotes), dated when it was recorded. When the notes are saved the audio is deleted ("no audio
is kept"), and the phone, told by the "notes" event or by asking the upload's status, deletes
its copy too.

State per upload (kv "phonerec:<uid>"): queued -> processing -> done (note_id) / failed.
Uploading the same uid again is harmless: a finished one is not redone.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .config import resolve_path
from .notes import SR, NoteSession, extract_summary, save

log = logging.getLogger(__name__)
CHUNK_S = 30
MAX_BYTES = 600 * 2**20        # ~40 hours of 32 kbps AAC: anything bigger isn't a recording


def decode(path: Path) -> np.ndarray:
    """Any audio file (AAC, M4A, WAV...) -> 16 kHz mono float32, via PyAV (faster-whisper's decoder)."""
    from faster_whisper.audio import decode_audio

    return decode_audio(str(path), sampling_rate=SR)


class PhoneRecordings:
    def __init__(self, ctx, folder: str | Path = "data/phone-recordings",
                 on_event: Callable[[str, dict], None] | None = None,
                 decoder: Callable[[Path], np.ndarray] = decode):
        self.ctx = ctx
        self.folder = resolve_path(folder) if isinstance(folder, str) else folder
        self.on_event = on_event or (lambda kind, data: None)
        self.decoder = decoder
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ----- state -----
    def state(self, uid: str) -> dict | None:
        raw = self.ctx.memory.get(f"phonerec:{uid}")
        return json.loads(raw) if raw else None

    def _set(self, uid: str, **changes) -> dict:
        with self._lock:
            st = {**(self.state(uid) or {}), **changes, "uid": uid}
            self.ctx.memory.set(f"phonerec:{uid}", json.dumps(st))
        return st

    @staticmethod
    def safe_uid(uid: str) -> str:
        uid = "".join(c for c in str(uid) if c.isalnum() or c in "-_")[:64]
        if not uid:
            raise ValueError("bad recording id")
        return uid

    # ----- upload -----
    def receive(self, uid: str, chunks: Iterable[bytes], kind: str = "lecture", title: str = "",
                started_ms: int = 0) -> dict:
        """Store an uploaded recording and queue it. Returns its state."""
        uid = self.safe_uid(uid)
        st = self.state(uid)
        if st and st.get("state") in ("queued", "processing", "done"):
            for _ in chunks:                       # already have it: drain the body, change nothing
                pass
            return st
        self.folder.mkdir(parents=True, exist_ok=True)
        part = self.folder / f"{uid}.part"
        size = 0
        with part.open("wb") as f:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES:
                    f.close()
                    part.unlink(missing_ok=True)
                    raise ValueError("recording too large")
                f.write(chunk)
        if size == 0:
            part.unlink(missing_ok=True)
            raise ValueError("empty recording")
        part.replace(self.folder / f"{uid}.audio")
        kind = "meeting" if kind == "meeting" else "lecture"
        started = dt.datetime.fromtimestamp(started_ms / 1000) if started_ms else dt.datetime.now()
        title = title.strip() or f"{kind.title()} {started:%b %d, %I:%M %p}".replace(" 0", " ")
        st = self._set(uid, state="queued", kind=kind, title=title, started=started.isoformat(timespec="seconds"),
                       bytes=size, error="", note_id=None)
        self.on_event("notes", {"action": "queued", "uid": uid, "title": title, "source": "phone"})
        self.start()
        self._wake.set()
        return st

    # ----- the worker -----
    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="phone-notes", daemon=True)
            self._thread.start()
        return self

    def pending(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("*.audio")) if self.folder.is_dir() else []

    def _run(self):
        while True:
            uids = self.pending()
            if not uids:
                self._wake.wait(60)
                self._wake.clear()
                continue
            notes = self.ctx.notes
            if notes is not None and (notes.active or notes.finishing):
                self._wake.wait(30)                # a live session has Whisper and the model: wait
                self._wake.clear()
                continue
            self.process(uids[0])

    def process(self, uid: str) -> dict:
        """Transcribe and write up one uploaded recording (blocking)."""
        path = self.folder / f"{uid}.audio"
        st = self.state(uid) or {}
        title, kind = st.get("title") or "Recording", st.get("kind") or "lecture"
        self._set(uid, state="processing")
        self.on_event("notes", {"action": "finishing", "uid": uid, "title": title, "source": "phone"})
        try:
            audio = self.decoder(path)
            mgr = self.ctx.notes
            session = NoteSession(kind, mgr._transcriber(), mgr.summarize, title)
            if st.get("started"):
                session.started = dt.datetime.fromisoformat(st["started"])
            step = CHUNK_S * SR
            for i in range(0, len(audio), step):
                session.push(audio[i:i + step])
            result = session.finish()
            save(result, mgr.folder)
            summary = extract_summary(result.notes_md)
            note_id = self.ctx.memory.add_notes(result.title, result.kind, result.started.isoformat(timespec="seconds"),
                                                result.ended.isoformat(timespec="seconds"), str(result.folder), summary,
                                                result.word_count)
            path.unlink(missing_ok=True)           # no audio is kept once the notes exist
            st = self._set(uid, state="done", note_id=note_id, error="")
            self.on_event("notes", {"action": "saved", "uid": uid, "note_id": note_id, "title": result.title,
                                    "folder": str(result.folder), "summary": summary, "source": "phone"})
        except Exception as exc:
            log.exception("phone recording %s failed", uid)
            # keep the audio (renamed) so nothing is lost; the phone can upload again to retry
            path.replace(self.folder / f"{uid}.failed") if path.exists() else None
            st = self._set(uid, state="failed", error=str(exc)[:200])
            self.on_event("notes", {"action": "failed", "uid": uid, "title": title, "error": str(exc)[:200],
                                    "source": "phone"})
        return st
