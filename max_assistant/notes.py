"""Meeting & lecture notes: listen to a meeting (laptop audio + mic) or an in-person lecture
(mic), transcribe it as it goes, and write structured notes when it ends.

Pipeline:  capture -> segmenter (cuts at pauses, ~25 s chunks) -> Whisper -> transcript
           every ~5 minutes of transcript -> section notes (LLM, in the background)
           at the end -> section notes combined into final notes (Markdown)

Summarising in sections is necessary: the 4b model sees ~4k tokens, a 1-hour lecture is
~13k. No audio is stored, only the transcript and the notes.
"""
from __future__ import annotations

import datetime as dt
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)
SR = 16000

MAP_PROMPT = (
    "You are taking notes for a student. Below is part {n} of the transcript of a {kind}. Write concise "
    "bullet-point notes: main points, definitions, examples, and any deadlines, assignments, exams or "
    "action items (with dates if said). Only use what is in the transcript. No introduction."
)
REDUCE_PROMPT = (
    "Combine these section notes from a {kind} into clean final notes in Markdown with exactly these "
    "headings: '## Summary' (3-5 sentences), '## Key points' (bullets), '## Details' (bullets grouped by "
    "topic), '## Action items & deadlines' (bullets, or 'None mentioned'), '## Open questions' (bullets, or "
    "'None'). Only use what is in the notes. No title, no introduction."
)
MERGE_PROMPT = "Merge these bullet-point notes from consecutive parts of a {kind} into one concise bullet list. Keep every deadline and action item."


# ---------------------------------------------------------------- audio ----------

def to_mono_16k(block: np.ndarray, sr: int) -> np.ndarray:
    """Float block (frames x channels) at any rate -> 16 kHz mono float32."""
    x = block.mean(axis=1) if block.ndim == 2 else block
    x = x.astype(np.float32)
    if sr == SR:
        return x
    if sr % SR == 0:                                   # 48 kHz: average groups of 3 (cheap low-pass)
        k = sr // SR
        n = len(x) // k * k
        return x[:n].reshape(-1, k).mean(axis=1)
    idx = np.arange(0, len(x), sr / SR)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


class Segmenter:
    """Collects 16 kHz audio and cuts it into chunks of ~min_s-max_s seconds, at the
    quietest moment near the end so words aren't split."""

    def __init__(self, min_s: float = 25.0, max_s: float = 32.0):
        self.min = int(min_s * SR)
        self.max = int(max_s * SR)
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0                                  # samples already emitted (for timestamps)

    def push(self, x: np.ndarray) -> list[tuple[float, np.ndarray]]:
        self.buf = np.concatenate([self.buf, x.astype(np.float32)])
        out = []
        while len(self.buf) >= self.max or (len(self.buf) >= self.min and self._quiet_tail()):
            cut = self._cut_point()
            out.append((self.offset / SR, self.buf[:cut]))
            self.offset += cut
            self.buf = self.buf[cut:]
        return out

    def flush(self) -> list[tuple[float, np.ndarray]]:
        if len(self.buf) < SR // 2:
            return []
        chunk = (self.offset / SR, self.buf)
        self.offset += len(self.buf)
        self.buf = np.zeros(0, dtype=np.float32)
        return [chunk]

    def _energy(self, start: int, end: int) -> np.ndarray:
        win = 1600                                       # 100 ms
        seg = self.buf[start:end]
        n = len(seg) // win
        return np.sqrt((seg[: n * win].reshape(n, win) ** 2).mean(axis=1) + 1e-12)

    def _quiet_tail(self) -> bool:
        e = self._energy(len(self.buf) - 3200, len(self.buf))
        return len(e) > 0 and e.max() < 0.3 * (np.median(self._energy(0, len(self.buf))) + 1e-6)

    def _cut_point(self) -> int:
        lo = max(self.min - 6 * SR, SR)
        hi = min(len(self.buf), self.max)
        e = self._energy(lo, hi)
        return lo + int(np.argmin(e)) * 1600 + 800 if len(e) else hi


class LiveCapture:
    """Mic and/or laptop audio (WASAPI loopback) mixed into one 16 kHz stream."""

    def __init__(self, sources: list[str], on_audio: Callable[[np.ndarray], None], mic_gain: float = 1.0):
        self.sources = sources               # e.g. ["mic"] or ["mic", "system"]
        self.on_audio = on_audio
        self.mic_gain = mic_gain
        self._stop = threading.Event()
        self._queues = {s: queue.Queue(maxsize=400) for s in sources}
        self.errors: list[str] = []

    def start(self):
        for s in self.sources:
            threading.Thread(target=self._record, args=(s,), name=f"notes-{s}", daemon=True).start()
        threading.Thread(target=self._mix, name="notes-mix", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _record(self, source: str):
        import warnings

        import soundcard as sc

        warnings.filterwarnings("ignore", module="soundcard")
        try:
            if source == "system":
                dev = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
                rate = 48000
            else:
                dev = sc.default_microphone()
                rate = SR
            with dev.recorder(samplerate=rate, channels=1, blocksize=rate // 10) as rec:
                while not self._stop.is_set():
                    block = rec.record(numframes=rate // 5)          # 200 ms
                    try:
                        self._queues[source].put_nowait(to_mono_16k(block, rate))
                    except queue.Full:
                        pass
        except Exception as exc:
            log.warning("notes capture (%s) failed: %s", source, exc)
            self.errors.append(f"{source}: {exc}")

    def _mix(self):
        while not self._stop.is_set():
            parts = []
            for s, q in self._queues.items():
                try:
                    x = q.get(timeout=0.5)
                    parts.append(x * (self.mic_gain if s == "mic" else 1.0))
                except queue.Empty:
                    continue
            if not parts:
                continue
            n = min(len(p) for p in parts)
            mixed = np.clip(sum(p[:n] for p in parts), -1.0, 1.0).astype(np.float32)
            self.on_audio(mixed)


# ---------------------------------------------------------------- session --------

@dataclass
class Segment:
    start_s: float
    text: str


@dataclass
class NoteResult:
    title: str
    kind: str
    started: dt.datetime
    ended: dt.datetime
    notes_md: str
    transcript_md: str
    folder: Path | None = None
    word_count: int = 0


DEADLINE_WORDS = re.compile(r"\b(due|deadline|submit|submission|quiz|exam|midterm|final|test|presentation|"
                            r"project|homework|assignment|office hours|lab)\b", re.I)
WHEN_WORDS = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|today|"
                        r"next week|this week|january|february|march|april|may|june|july|august|september|october|"
                        r"november|december|noon|midnight)\b|\b\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)|"
                        r"\b\d{1,2}(?:st|nd|rd|th)\b", re.I)


def deadline_quotes(segments: list["Segment"]) -> list[tuple[str, str]]:
    """Sentences from the transcript that mention a deadline-ish word *and* a date/time,
    quoted exactly. The model's summary can garble a time; these can't."""
    out, seen = [], set()
    for seg in segments:
        for sentence in re.split(r"(?<=[.!?])\s+", seg.text):
            s = sentence.strip()
            if DEADLINE_WORDS.search(s) and WHEN_WORDS.search(s) and s.lower() not in seen:
                seen.add(s.lower())
                out.append((clock(seg.start_s), s))
    return out


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class NoteSession:
    """One recording. Feed audio with push() (LiveCapture does this), then finish()."""

    def __init__(self, kind: str, transcribe: Callable[[np.ndarray, str], str], summarize: Callable[[str, str, int], str],
                 title: str = "", section_words: int = 700, silence_rms: float = 0.003):
        self.kind = kind                      # "lecture" or "meeting"
        self.title = title or f"{kind.title()} {dt.datetime.now():%b %d, %I:%M %p}".replace(" 0", " ")
        self.transcribe = transcribe
        self.summarize = summarize            # (system_prompt, text, max_tokens) -> text
        self.section_words = section_words
        self.silence_rms = silence_rms
        self.started = dt.datetime.now()
        self.segments: list[Segment] = []
        self.section_notes: list[str] = []
        self._pending_words: list[str] = []
        self._seg = Segmenter()
        self._chunks: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._work, name="notes-transcribe", daemon=True)
        self._worker.start()
        self.audio_seconds = 0.0

    # audio in (any thread)
    def push(self, x: np.ndarray):
        self.audio_seconds += len(x) / SR
        for chunk in self._seg.push(x):
            self._chunks.put(chunk)

    @property
    def elapsed(self) -> float:
        return (dt.datetime.now() - self.started).total_seconds()

    def status(self) -> dict:
        with self._lock:
            words = sum(len(s.text.split()) for s in self.segments)
            last = self.segments[-1].text if self.segments else ""
        return {"title": self.title, "kind": self.kind, "elapsed_s": round(self.elapsed), "words": words,
                "sections": len(self.section_notes), "last": last[-160:], "queued": self._chunks.qsize()}

    def _work(self):
        while True:
            item = self._chunks.get()
            if item is None:
                break
            start_s, audio = item
            if float(np.sqrt(np.mean(audio ** 2))) < self.silence_rms:
                continue                                     # nothing said in this chunk
            with self._lock:
                recent = " ".join(" ".join(s.text for s in self.segments[-2:]).split()[-40:])
            # Whisper follows the vocabulary of its prompt: the topic and what was just said
            prompt = f"{self.title}. {recent}".strip()
            try:
                text = self.transcribe(audio, prompt).strip()
            except Exception as exc:
                log.warning("notes transcription failed: %s", exc)
                continue
            if not text:
                continue
            with self._lock:
                self.segments.append(Segment(start_s, text))
                self._pending_words += text.split()
                ready = len(self._pending_words) >= self.section_words
                if ready:
                    section, self._pending_words = " ".join(self._pending_words), []
            if ready:
                self._summarize_section(section)

    def _summarize_section(self, text: str):
        try:
            notes = self.summarize(MAP_PROMPT.format(n=len(self.section_notes) + 1, kind=self.kind), text, 450)
            self.section_notes.append(notes.strip())
        except Exception as exc:
            log.warning("section summary failed: %s", exc)
            self.section_notes.append(text[:1500])

    def finish(self) -> NoteResult:
        for chunk in self._seg.flush():
            self._chunks.put(chunk)
        self._chunks.put(None)
        self._worker.join()
        if self._pending_words:
            self._summarize_section(" ".join(self._pending_words))
            self._pending_words = []
        ended = dt.datetime.now()
        transcript = "\n".join(f"[{clock(s.start_s)}] {s.text}" for s in self.segments)
        words = sum(len(s.text.split()) for s in self.segments)
        if not self.segments:
            notes = "## Summary\nNothing was transcribed: the recording was silent or too quiet."
        else:
            notes = self._combine(self.section_notes)
            quotes = deadline_quotes(self.segments)
            if quotes:
                notes += "\n\n## Deadlines & dates (exact quotes)\n" + "\n".join(f"- [{t}] \u201c{q}\u201d" for t, q in quotes)
        # Recorded audio length, not wall-clock time (they differ when audio is fed from a file)
        ended = max(ended, self.started + dt.timedelta(seconds=self.audio_seconds))
        return NoteResult(self.title, self.kind, self.started, ended, notes, transcript, word_count=words)

    def _combine(self, parts: list[str]) -> str:
        # Merge neighbours until everything fits in one final call (~1800 words of notes)
        while sum(len(p.split()) for p in parts) > 1800 and len(parts) > 1:
            merged = []
            for i in range(0, len(parts), 3):
                group = "\n\n".join(parts[i:i + 3])
                merged.append(self.summarize(MERGE_PROMPT.format(kind=self.kind), group, 500).strip())
            parts = merged
        try:
            return self.summarize(REDUCE_PROMPT.format(kind=self.kind), "\n\n".join(parts), 1100).strip()
        except Exception as exc:
            log.warning("final notes failed: %s", exc)
            return "## Notes\n" + "\n\n".join(parts)


# ---------------------------------------------------------------- saving ---------

def safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "", text).strip()[:60] or "Notes"


def save(result: NoteResult, folder: Path) -> Path:
    """Write notes.md + transcript.md into '<folder>/<date> <title>/'."""
    target = folder / f"{result.started:%Y-%m-%d %H%M} {safe_name(result.title)}"
    target.mkdir(parents=True, exist_ok=True)
    minutes = max(1, round((result.ended - result.started).total_seconds() / 60))
    header = (f"# {result.title}\n\n*{result.kind.title()} · {result.started:%A, %B %d, %Y · %I:%M %p} · "
              f"{minutes} min · {result.word_count} words transcribed*\n\n")
    (target / "notes.md").write_text(header + result.notes_md + "\n", encoding="utf-8")
    (target / "transcript.md").write_text(f"# Transcript: {result.title}\n\n{result.transcript_md}\n", encoding="utf-8")
    result.folder = target
    return target


def extract_summary(notes_md: str) -> str:
    m = re.search(r"## Summary\s*\n(.+?)(\n## |\Z)", notes_md, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else notes_md[:300]


# ---------------------------------------------------------------- manager --------

class NotesManager:
    """Runs at most one recording at a time; finishing (the final write-up) happens in the
    background and `on_done(result, error)` is called when the notes are saved."""

    def __init__(self, ctx, transcriber: Callable[[], Callable[[np.ndarray, str], str]], folder: Path,
                 on_event: Callable[[str, dict], None] | None = None,
                 on_done: Callable[[NoteResult | None, str | None], None] | None = None,
                 include_mic_in_meetings: bool = True):
        self.ctx = ctx
        self._transcriber = transcriber        # factory: loads Whisper lazily (text mode has none yet)
        self.folder = folder
        self.on_event = on_event or (lambda kind, data: None)
        self.on_done = on_done or (lambda result, error: None)
        self.include_mic = include_mic_in_meetings
        self.session: NoteSession | None = None
        self.capture: LiveCapture | None = None
        self.finishing = False
        self._lock = threading.Lock()

    def summarize(self, system: str, text: str, max_tokens: int) -> str:
        from .agent import strip_thinking

        llm, cfg = self.ctx.llm, self.ctx.cfg
        self.ctx.models_asleep = False
        reply = llm.chat(cfg.llm.fast_model, [{"role": "system", "content": system}, {"role": "user", "content": text}],
                         options={"num_predict": max_tokens, "temperature": 0.2})
        return strip_thinking(reply.content)

    @property
    def active(self) -> bool:
        return self.session is not None

    def start(self, kind: str = "lecture", title: str = "", capture: bool = True) -> NoteSession:
        kind = "meeting" if kind == "meeting" else "lecture"
        with self._lock:
            if self.session is not None:
                raise RuntimeError("already taking notes")
            session = NoteSession(kind, self._transcriber(), self.summarize, title)
            self.session = session
            if capture:
                sources = ["system", "mic"] if kind == "meeting" and self.include_mic else \
                          ["system"] if kind == "meeting" else ["mic"]
                self.capture = LiveCapture(sources, session.push).start()
        self.on_event("notes", {"action": "started", "kind": kind, "title": session.title})
        return session

    def status(self) -> dict:
        s = self.session
        if s is None:
            return {"active": False, "finishing": self.finishing}
        return {"active": True, "finishing": self.finishing, **s.status(),
                "capture_errors": self.capture.errors if self.capture else []}

    def stop(self, background: bool = True) -> NoteResult | None:
        with self._lock:
            session, self.session = self.session, None
            if session is None:
                raise RuntimeError("not taking notes")
            if self.capture is not None:
                self.capture.stop()
                self.capture = None
            self.finishing = True
        self.on_event("notes", {"action": "finishing", "title": session.title})
        if background:
            threading.Thread(target=self._finish, args=(session,), name="notes-finish", daemon=True).start()
            return None
        return self._finish(session)

    def _finish(self, session: NoteSession) -> NoteResult | None:
        try:
            result = session.finish()
            save(result, self.folder)
            summary = extract_summary(result.notes_md)
            if self.ctx.memory is not None:
                self.ctx.memory.add_notes(result.title, result.kind, result.started.isoformat(timespec="seconds"),
                                          result.ended.isoformat(timespec="seconds"), str(result.folder), summary,
                                          result.word_count)
            self.on_event("notes", {"action": "saved", "title": result.title, "folder": str(result.folder),
                                    "summary": summary})
            self.on_done(result, None)
            return result
        except Exception as exc:
            log.exception("finishing notes failed")
            self.on_event("notes", {"action": "failed", "title": session.title, "error": str(exc)[:200]})
            self.on_done(None, str(exc))
            return None
        finally:
            self.finishing = False
