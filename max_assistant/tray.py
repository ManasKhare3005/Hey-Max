"""System tray icon for background mode (started by install_autostart.ps1 at login).

Menu: open the dashboard, pause/resume listening, put the models to sleep (free the GPU,
e.g. before gaming), quit. The voice loop reads `paused` and `quit` between audio frames.
"""
from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable

log = logging.getLogger(__name__)


def _icon_image(paused: bool = False):
    from PIL import Image, ImageDraw, ImageFilter

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    core = (100, 116, 139) if paused else (34, 211, 238)
    ImageDraw.Draw(glow).ellipse((6, 6, 58, 58), fill=core + (140,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), fill=core + (255,))
    d.ellipse((20, 18, 32, 30), fill=(230, 252, 255, 220))
    return img


class Tray:
    def __init__(self, name: str, dashboard_url: str, free_gpu: Callable[[], None]):
        self.name = name
        self.url = dashboard_url
        self.free_gpu = free_gpu
        self.paused = threading.Event()
        self.quit = threading.Event()
        self.icon = None

    def start(self):
        try:
            import pystray
        except ImportError:
            log.warning("pystray not installed; running without a tray icon")
            return self
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Open dashboard", lambda: webbrowser.open(self.url), default=True),
            item(lambda _: "Resume listening" if self.paused.is_set() else "Pause listening", self._toggle),
            item("Sleep models (free GPU)", lambda: self._safe(self.free_gpu)),
            pystray.Menu.SEPARATOR,
            item(f"Quit {self.name}", self._quit),
        )
        self.icon = pystray.Icon("max", _icon_image(), f"{self.name}: listening", menu)
        self.icon.run_detached()
        return self

    def set_status(self, text: str):
        if self.icon is not None:
            self.icon.title = f"{self.name}: {text}"[:120]

    def _toggle(self):
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()
        if self.icon is not None:
            self.icon.icon = _icon_image(self.paused.is_set())
            self.set_status("paused" if self.paused.is_set() else "listening")
            self.icon.update_menu()

    def _quit(self):
        self.quit.set()
        if self.icon is not None:
            self.icon.stop()

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception as exc:
            log.warning("tray action failed: %s", exc)
