"""Record a spoken command and transcribe it with as little waiting as possible.

Two tricks on top of plain "record until 1 s of silence, then transcribe":
- Early transcription: after a short pause (`early_s`) transcription starts in the
  background while we keep listening. If you carry on talking, that result is dropped.
- Smart end of turn: if the early transcript looks finished ("Open YouTube."), we answer
  right away instead of waiting out the full silence. If it trails off ("search for..."),
  we wait longer (`max_silence_s`) so a pause mid-sentence doesn't cut you off.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

import numpy as np

from .audio import FRAME, NoiseFloor, rms

log = logging.getLogger(__name__)

# A transcript ending in one of these is probably mid-sentence
DANGLING = set("""a an and are as at but by for from if in into is like my of on or so than that the then
their to um uh was with your about can could would should please""".split())

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt")


def looks_complete(text: str) -> bool:
    t = text.strip()
    if not t or t.endswith(("...", "…", "-", ",")):
        return False
    words = re.findall(r"[a-z']+", t.lower())
    return bool(words) and words[-1] not in DANGLING


def strip_wake_word(text: str) -> str:
    """The tail of 'Hey Max' can land in the recording: 'Max, open YouTube' -> 'open YouTube'."""
    return re.sub(r"^\s*(hey\s+)?max\b[\s,.!?]*", "", text, flags=re.I).strip() or text.strip()


def listen_for_command(
    mic,
    noise: NoiseFloor,
    transcribe: Callable[[np.ndarray], str],
    sensitivity: float = 3.0,
    silence_s: float = 1.0,
    no_speech_timeout_s: float = 5.0,
    max_record_s: float = 15.0,
    early_s: float = 0.4,
    max_silence_s: float = 1.8,
) -> str:
    """Record until the speaker is done and return the transcript ('' if nothing was said)."""
    frame_s = FRAME / mic.sample_rate
    threshold = max(noise.level * sensitivity, 300.0)
    frames: list[np.ndarray] = []
    started = False
    silent_for = waited = elapsed = 0.0
    early: Future | None = None
    started_at = time.monotonic()

    def audio_so_far() -> np.ndarray:
        return np.concatenate(frames).astype(np.float32) / 32768.0

    while elapsed < max_record_s:
        frame = mic.read(timeout=1.0)
        if frame is None:
            continue
        elapsed += frame_s
        loud = rms(frame) > threshold
        if not started:
            waited += frame_s
            frames.append(frame)
            frames = frames[-8:]            # ~0.6 s lead-in: quiet first words start below the threshold
            if loud:
                started = True
            elif waited > no_speech_timeout_s:
                return ""
            continue

        frames.append(frame)
        if loud:
            silent_for = 0.0
            early = None                    # still talking: an early transcript would be stale
            continue
        silent_for += frame_s

        if early is None and silent_for >= early_s:
            early = _pool.submit(transcribe, audio_so_far())

        if early is not None and early.done():
            text = early.result()
            if looks_complete(text):
                log.info("end of turn after %.1fs of silence (early transcript)", silent_for)
                return strip_wake_word(text)

        if silent_for >= silence_s and early is not None:
            text = early.result()           # started earlier, so usually (nearly) ready
            if looks_complete(text):
                log.info("end of turn after %.1fs of silence", silent_for)
                return strip_wake_word(text)
            if silent_for >= max_silence_s:
                # Still sounds unfinished. A quiet last word can pass for silence and be
                # missing from the early transcript, so transcribe everything once more
                log.info("end of turn after %.1fs of silence (full re-transcription)", silent_for)
                return strip_wake_word(transcribe(audio_so_far()))
            # Trails off ("search for..."): give them up to max_silence_s to continue

    if not started:
        return ""
    log.info("recording hit %s (%.1fs)", "the time limit" if elapsed >= max_record_s else "its end",
             time.monotonic() - started_at)
    return strip_wake_word(transcribe(audio_so_far()))
