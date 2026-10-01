"""Live captions while taking notes: words appear ~0.5 s after they're said.

A small streaming model (sherpa-onnx zipformer "Kroko", CPU, ~3% of a core) gives rough
text right away; Whisper still transcribes ~30 s chunks in the background and its text
replaces the live words for that stretch. Saved transcripts and notes use Whisper only.
Measured on a 136 s lecture: 3.9 s of compute, punctuation and casing included.
"""
from __future__ import annotations

import glob
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
SR = 16000


@dataclass
class LiveLine:
    start_s: float        # audio time since the recording started
    end_s: float
    text: str


class LiveModel:
    """The streaming recognizer, loaded once and shared by every recording."""

    def __init__(self, model_dir: str | Path, threads: int = 1):
        import sherpa_onnx

        d = Path(model_dir)
        pick = lambda pattern: (sorted(glob.glob(str(d / pattern))) or [None])[0]
        if not (d / "tokens.txt").exists() or pick("encoder*.onnx") is None:
            raise FileNotFoundError(f"live caption model not found in {d} (run setup.ps1)")
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"), encoder=pick("encoder*.onnx"), decoder=pick("decoder*.onnx"),
            joiner=pick("joiner*.onnx"), num_threads=threads, sample_rate=SR, feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,    # end a line after a long pause...
            rule2_min_trailing_silence=0.8,    # ...or a short one once something was said
            rule3_min_utterance_length=60)     # (a 20 s cap split words in half)


class LiveTranscriber:
    """One recording's live captions. push() from the capture thread; decoding runs in its
    own thread so audio capture never waits on it."""

    def __init__(self, model: LiveModel):
        self.rec = model.recognizer
        self.stream = self.rec.create_stream()
        self.lines: list[LiveLine] = []
        self.partial = ""
        self._fed = 0.0                 # seconds of audio decoded so far
        self._line_start: float | None = None
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="notes-live", daemon=True)
        self._thread.start()

    def push(self, x: np.ndarray):
        self._q.put(x)

    def _work(self):
        while True:
            x = self._q.get()
            if x is None:
                break
            try:
                self._decode(x)
            except Exception as exc:          # captions are a nicety: never break the recording
                log.warning("live captions stopped: %s", exc)
                break

    def _decode(self, x: np.ndarray):
        start = self._fed
        self.stream.accept_waveform(SR, x)
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
        self._fed += len(x) / SR
        text = self.rec.get_result(self.stream).strip()
        with self._lock:
            if text and self._line_start is None:
                self._line_start = start
            self.partial = text
            if self.rec.is_endpoint(self.stream):
                if text:
                    first = start if self._line_start is None else self._line_start
                    self.lines.append(LiveLine(first, self._fed, text))
                self.partial, self._line_start = "", None
                self.rec.reset(self.stream)

    def view(self, after_s: float) -> tuple[list[LiveLine], str]:
        """Lines Whisper hasn't covered yet and the words in progress. A line is dropped once
        most of it (its midpoint) is covered, so text isn't shown twice at a chunk boundary."""
        with self._lock:
            self.lines = [l for l in self.lines if (l.start_s + l.end_s) / 2 > after_s]
            return list(self.lines), self.partial

    def close(self):
        self._q.put(None)
        self._thread.join(timeout=5)
