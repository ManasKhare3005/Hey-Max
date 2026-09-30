"""Heartbeat for voice mode: finds out *why* Max stops listening, from the log alone.

Every `interval_s` it logs what the main loop is doing and for how long, whether the mic
is still delivering audio (counted on the audio thread, so it's independent of the main
loop), the input level, and which window is in front. If the loop sits in one busy stage
for too long, it dumps every thread's stack to logs/stacks.log.
"""
from __future__ import annotations

import faulthandler
import logging
import math
import sys
import threading
import time

from .config import resolve_path

log = logging.getLogger(__name__)


def foreground_window() -> str:
    if sys.platform != "win32":
        return "-"
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value[:60] or "(untitled)"
    except Exception:
        return "?"


class Heartbeat:
    def __init__(self, mic, interval_s: float = 30.0, stuck_after_s: float = 60.0):
        self.mic = mic
        self.interval_s = interval_s
        self.stuck_after_s = stuck_after_s
        self.stage = "starting"
        self.since = time.monotonic()
        self._dumped_for = None
        self._stop = threading.Event()

    def set(self, stage: str):
        self.stage, self.since = stage, time.monotonic()

    def start(self):
        threading.Thread(target=self._run, name="heartbeat", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        last_frames, last_dropped = self.mic.frames_in, self.mic.dropped
        while not self._stop.wait(self.interval_s):
            frames, dropped = self.mic.frames_in, self.mic.dropped
            got, lost = frames - last_frames, dropped - last_dropped
            last_frames, last_dropped = frames, dropped
            expected = int(self.interval_s / 0.08)
            level = self.mic.take_level_dbfs()
            in_stage = time.monotonic() - self.since
            msg = (f"heartbeat: {self.stage} for {in_stage:.0f}s | mic {got}/{expected} frames, "
                   f"level {level:.0f} dBFS, queued {self.mic.backlog}, dropped {lost}, "
                   f"muted {self.mic.muted.is_set()} | front: {foreground_window()}")
            waiting = self.stage == "waiting for wake word"
            # Audio queues up normally while Max thinks or talks; only a backlog while
            # *waiting* means the listener is stalled
            problem = got < expected * 0.5 or lost or (waiting and self.mic.backlog > 12) or \
                (not waiting and in_stage > self.stuck_after_s)
            (log.warning if problem else log.info)(msg)
            if self.stage != "waiting for wake word" and in_stage > self.stuck_after_s \
                    and self._dumped_for != self.since:
                self._dumped_for = self.since
                self._dump(f"stuck in '{self.stage}' for {in_stage:.0f}s")

    def _dump(self, why: str):
        path = resolve_path("logs/stacks.log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {why} =====\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
            log.warning("%s; thread stacks written to %s", why, path)
        except Exception as exc:
            log.warning("stack dump failed: %s", exc)


def dbfs(mean_square: float) -> float:
    return 10 * math.log10(mean_square / 32768.0 ** 2) if mean_square > 0 else -120.0
