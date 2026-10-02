"""Speaking while thinking, and being interrupted.

SentenceSpeaker: the agent streams the model's words in; each finished sentence is spoken
right away on a worker thread while the rest is still being written (saves ~1-2 s per
answer). A sentence that claims an action ("I'll remind you...") halts the stream, because
the agent may still correct that reply; finish() then speaks the final answer instead.

watch_for_interrupt: while Max talks, a small keyword spotter listens for "stop" or
"Hey Max". Keywords that appear in the sentence being spoken are ignored, since on laptop
speakers the microphone hears Max's own voice.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)
SENTENCE = re.compile(r"(.+?[.!?…])(?:\s+)", re.S)


class SentenceSpeaker:
    def __init__(self, say: Callable[[str], str | None]):
        self.say = say                        # speaks one sentence; returns "stop"/"wake" if interrupted
        self.interrupted: str | None = None
        self._buf = ""
        self._segment = ""                     # text spoken during the current model call
        self._halted = False
        self._q: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="speak-stream", daemon=True)
        self._worker.start()

    def feed(self, piece: str | None):
        """A piece of the model's reply; None = a new model call starts."""
        if piece is None:
            self._buf, self._segment, self._halted = "", "", False
            return
        if self._halted or self.interrupted:
            return
        self._buf += piece
        if "<think" in self._buf:
            self._halted = True
            return
        while m := SENTENCE.match(self._buf):
            sentence = m.group(1).strip()
            self._buf = self._buf[m.end():]
            if _claims(sentence):              # the agent may still correct this reply
                self._halted = True
                return
            self._segment += sentence + " "
            self._q.put(sentence)

    def _work(self):
        while True:
            sentence = self._q.get()
            try:
                if sentence is None or self.interrupted:
                    continue
                result = self.say(sentence)
                if result in ("stop", "wake"):
                    self.interrupted = result
            finally:
                self._q.task_done()

    def finish(self, answer: str) -> str | None:
        """Speak what's left of the final answer. Returns "stop"/"wake" if the user interrupted."""
        self._q.join()
        if self.interrupted:
            return self.interrupted
        spoken = self._segment.strip()
        rest = answer.strip()
        if spoken and not self._halted and _norm(rest).startswith(_norm(spoken)):
            rest = rest[_offset(rest, spoken):].strip()
        if rest:
            result = self.say(rest)
            if result in ("stop", "wake"):
                self.interrupted = result
        return self.interrupted


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _offset(answer: str, spoken: str) -> int:
    """Index in `answer` just after the already-spoken prefix (whitespace-insensitive)."""
    target = len(_norm(spoken))
    seen, i = 0, 0
    prev_space = False
    while i < len(answer) and seen < target:
        ch = answer[i]
        if ch.isspace():
            if not prev_space:
                seen += 1
            prev_space = True
        else:
            seen += 1
            prev_space = False
        i += 1
    return i


def _claims(sentence: str) -> bool:
    from .agent import CLAIMS

    return any(p.search(sentence) for p in CLAIMS.values())


# ---------------- interrupting Max while it talks ----------------

INTERRUPT_PHRASES = {"HEY MAX": "wake", "MAX STOP": "stop", "STOP IT": "stop", "STOP": "stop", "OKAY STOP": "stop"}


def interrupt_label(keyword: str) -> str | None:
    return INTERRUPT_PHRASES.get(keyword.replace("_", " ").upper().strip())


def echo_of_speech(keyword: str, sentence: str) -> bool:
    """True if the keyword's words are in what Max is saying (the mic hears the speakers)."""
    words = re.findall(r"[a-z']+", sentence.lower())
    keys = [k.lower() for k in keyword.replace("_", " ").split() if k.lower() not in ("hey", "okay")]
    return any(w.startswith(k) for k in keys for w in words)       # "stop", "stopped", "maximum"...


def watch_for_interrupt(read_frame: Callable[[float], object], spot: Callable[[object], str | None],
                        seconds: float, sentence: str, should_stop: Callable[[], bool] = lambda: False) -> str | None:
    """Feed mic frames to the keyword spotter for `seconds` (the length of the audio being
    played). Returns "stop" or "wake" as soon as the user says one, else None."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not should_stop():
        frame = read_frame(0.1)
        if frame is None:
            continue
        keyword = spot(frame)
        if not keyword:
            continue
        if echo_of_speech(keyword, sentence):
            log.info("ignored interrupt %r: Max itself is saying it", keyword)
            continue
        label = interrupt_label(keyword)
        if label:
            log.info("interrupted by %r", keyword)
            return label
    return None
