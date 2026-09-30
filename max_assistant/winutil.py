"""Keep Max listening no matter which window is in front.

The big one: a program that prints to a console *blocks* when the console stops reading,
which happens when you click into a Command Prompt window (QuickEdit "Select" mode) and
when an editor's terminal (VS Code) isn't the active window. Max froze on its next print,
e.g. right after hearing "Hey Max". So console output goes through a background writer,
and QuickEdit is turned off.
"""
from __future__ import annotations

import atexit
import ctypes
import io
import logging
import queue
import sys
import threading
import time

log = logging.getLogger(__name__)

PROCESS_POWER_THROTTLING = 4                   # PROCESS_INFORMATION_CLASS.ProcessPowerThrottling
THROTTLE_EXECUTION_SPEED = 0x1                 # "efficiency mode" / EcoQoS
THROTTLE_IGNORE_TIMER_RESOLUTION = 0x4


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


def keep_awake_in_background() -> list[str]:
    """Opt out of Windows 11 background throttling and raise priority a notch.

    Windows slows down processes whose window isn't in front (EcoQoS). For an always-on
    listener that means falling behind on mic audio and missing the wake word.
    Returns what was changed, for logging. No-op off Windows.
    """
    if sys.platform != "win32":
        return []
    done = []
    try:
        state = _PowerThrottlingState(1, THROTTLE_EXECUTION_SPEED | THROTTLE_IGNORE_TIMER_RESOLUTION, 0)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ok = kernel32.SetProcessInformation(ctypes.c_void_p(kernel32.GetCurrentProcess()), PROCESS_POWER_THROTTLING,
                                            ctypes.byref(state), ctypes.sizeof(state))
        if ok:
            done.append("power throttling off")
        else:
            log.warning("couldn't turn off power throttling (error %s)", ctypes.GetLastError())
    except Exception as exc:
        log.warning("power throttling opt-out failed: %s", exc)
    try:
        import psutil

        psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        done.append("above-normal priority")
    except Exception as exc:
        log.warning("couldn't raise priority: %s", exc)
    return done


class BackgroundWriter(io.TextIOBase):
    """Stand-in for sys.stdout/stderr that never blocks the caller: text is queued and a
    daemon thread writes it. If the console stalls, only that thread waits (and if the
    queue fills up meanwhile, the overflow is dropped rather than freezing Max)."""

    def __init__(self, stream, max_items: int = 2000):
        self.stream = stream
        self._q: queue.Queue[str] = queue.Queue(maxsize=max_items)
        threading.Thread(target=self._run, name="console-writer", daemon=True).start()
        atexit.register(self.drain)

    def drain(self, timeout_s: float = 1.0):
        """Give queued text a moment to reach the console at exit (never waits forever)."""
        deadline = time.monotonic() + timeout_s
        while self._q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)

    def write(self, text: str) -> int:
        try:
            self._q.put_nowait(text)
        except queue.Full:
            pass
        return len(text)

    def flush(self):
        pass

    def writable(self) -> bool:
        return True

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")

    def isatty(self) -> bool:
        return self.stream.isatty()

    def fileno(self):
        return self.stream.fileno()

    def _run(self):
        while True:
            text = self._q.get()
            try:
                self.stream.write(text)
                self.stream.flush()
            except Exception:
                pass
            finally:
                self._q.task_done()


def unblock_console():
    """Route stdout/stderr through BackgroundWriter and turn off QuickEdit (Windows)."""
    if not isinstance(sys.stdout, BackgroundWriter):
        sys.stdout = BackgroundWriter(sys.stdout)
        sys.stderr = BackgroundWriter(sys.stderr)
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)            # STD_INPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            kernel32.SetConsoleMode(handle, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception as exc:
        log.debug("couldn't change console mode: %s", exc)
