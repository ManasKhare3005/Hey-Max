"""Microphone input, voice-activity recording, playback and chimes."""
from __future__ import annotations

import logging
import queue
import threading

import numpy as np

log = logging.getLogger(__name__)

FRAME = 1280  # 80 ms at 16 kHz, the chunk size openWakeWord expects


class Microphone:
    """Continuous 16 kHz mono int16 stream delivered as 80 ms frames."""

    def __init__(self, sample_rate: int = 16000, device=None):
        import sounddevice as sd  # imported lazily so text mode works without audio libs

        self.sample_rate = sample_rate
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.muted = threading.Event()
        self.dropped = 0     # frames lost because the listener fell behind
        self.frames_in = 0   # frames delivered by the driver, muted or not (for diagnostics)
        self._sq_sum = 0.0   # running sum of mean-square levels, for the heartbeat
        self._sq_n = 0
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=device,
            callback=self._callback,
        )

    def _callback(self, indata, frames, t, status):
        if status:
            log.debug("mic status: %s", status)
        self.frames_in += 1
        x = indata[:, 0].astype(np.float32)
        self._sq_sum += float(np.mean(x * x))
        self._sq_n += 1
        if self.muted.is_set():
            return
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.dropped += 1  # drop frames rather than block the audio thread

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def take_level_dbfs(self) -> float:
        """Average input level since the last call (dBFS; about -60 is a quiet room)."""
        from .diagnostics import dbfs

        n, total = self._sq_n, self._sq_sum
        self._sq_n, self._sq_sum = 0, 0.0
        return dbfs(total / n) if n else -120.0

    @property
    def backlog(self) -> int:
        """80 ms frames waiting to be processed; more than a few means we're falling behind."""
        return self._q.qsize()

    def flush(self):
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) + 1e-9)


class NoiseFloor:
    """Tracks background noise so the speech threshold adapts to the room."""

    def __init__(self, initial: float = 200.0, alpha: float = 0.05):
        self.level = initial
        self.alpha = alpha

    def update(self, frame: np.ndarray):
        self.level = (1 - self.alpha) * self.level + self.alpha * rms(frame)


def record_utterance(
    mic: Microphone,
    noise: NoiseFloor,
    sensitivity: float = 3.0,
    silence_s: float = 1.0,
    no_speech_timeout_s: float = 5.0,
    max_record_s: float = 15.0,
) -> np.ndarray | None:
    """Record until the speaker stops talking. Returns float32 audio in [-1, 1] or None."""
    frame_s = FRAME / mic.sample_rate
    threshold = max(noise.level * sensitivity, 300.0)
    frames: list[np.ndarray] = []
    started = False
    silent_for = 0.0
    waited = 0.0
    elapsed = 0.0

    while elapsed < max_record_s:
        frame = mic.read(timeout=1.0)
        if frame is None:
            continue
        elapsed += frame_s
        loud = rms(frame) > threshold
        if not started:
            waited += frame_s
            frames.append(frame)
            frames = frames[-4:]  # keep ~300 ms of lead-in so the first word isn't clipped
            if loud:
                started = True
            elif waited > no_speech_timeout_s:
                return None
            continue
        frames.append(frame)
        silent_for = 0.0 if loud else silent_for + frame_s
        if silent_for >= silence_s:
            break

    if not started:
        return None
    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    return audio


def play(audio: np.ndarray, sample_rate: int, device=None):
    import sounddevice as sd

    sd.play(audio, sample_rate, device=device)
    sd.wait()


def chime(kind: str = "wake", device=None):
    """Short tones: rising for wake, falling for done/cancel."""
    sr = 22050
    notes = {"wake": (660, 880), "done": (880, 660), "error": (300, 220)}[kind]
    parts = []
    for f in notes:
        t = np.linspace(0, 0.09, int(sr * 0.09), endpoint=False)
        tone = 0.25 * np.sin(2 * np.pi * f * t)
        fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)
        parts.append((tone * fade).astype(np.float32))
    try:
        play(np.concatenate(parts), sr, device)
    except Exception as exc:  # never let a chime crash the assistant
        log.debug("chime failed: %s", exc)


def list_devices() -> str:
    import sounddevice as sd

    return str(sd.query_devices())
