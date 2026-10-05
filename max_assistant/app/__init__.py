"""Max as a desktop app: the dashboard in its own window (no browser tab, no console).

Opening it starts Max in background (tray) mode if it isn't running, shows a "starting" screen
while the models load, then the full dashboard. Closing the window leaves Max running in the
tray (tray → "Open Max" brings the window back). Opening it again while it's open brings the
existing window to the front. The Desktop / Start Menu shortcuts come from install_shortcuts.ps1.

Run: .venv\\Scripts\\pythonw -m max_assistant.app
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from ..config import ROOT, load_config

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
ICON = HERE / "max.ico"
TITLE = "Max"
APP_ID = "ManasKhare.Max"            # groups the window under its own taskbar icon, not Python's


def pythonw() -> str:
    exe = Path(sys.executable).with_name("pythonw.exe")
    return str(exe if exe.exists() else sys.executable)


def launch_detached(*args: str):
    """Start `pythonw <args>` independent of this process (and of any console)."""
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([pythonw(), *args], cwd=str(ROOT), creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_app():
    """Show the Max window (starts it, or brings the open one to the front). Used by the tray and overlay."""
    launch_detached("-m", "max_assistant.app")


class AppApi:
    """Called from the starting screen's JavaScript. Attributes are underscored so pywebview
    doesn't try to expose (and walk) the window object."""

    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._window = None
        self._starting_until = 0.0

    def status(self):
        try:
            requests.get(self._base + "/api/ping", timeout=1.5).raise_for_status()
            return {"online": True}
        except Exception:
            return {"online": False, "starting": time.monotonic() < self._starting_until}

    def start(self):
        if self.status()["online"]:
            return {"ok": True}
        launch_detached("-m", "max_assistant", "--tray")
        self._starting_until = time.monotonic() + 120         # Whisper + models take a while
        return {"ok": True}

    def show_dashboard(self):
        # Navigate after this call has returned to the page, or pywebview can't deliver the result
        if self._window is not None:
            threading.Timer(0.1, self._window.load_url, args=(self._base + "/",)).start()


# ----- Windows details: one window, Max's own taskbar icon -----

def make_icon(path: Path = ICON):
    """Max's orb (same design as the tray icon) as a multi-size .ico."""
    from PIL import Image, ImageDraw, ImageFilter

    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    core = (34, 211, 238)
    ImageDraw.Draw(glow).ellipse((24, 24, 232, 232), fill=core + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    d.ellipse((56, 56, 200, 200), fill=core + (255,))
    d.ellipse((80, 72, 128, 120), fill=(230, 252, 255, 220))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def _focus_existing() -> bool:
    try:
        import ctypes

        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, TITLE)
        if not hwnd:
            return False
        u.ShowWindow(hwnd, 9)                                   # SW_RESTORE (if minimized)
        u.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _set_app_id():
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _set_window_icon():
    """pywebview on Windows shows Python's icon; swap in Max's."""
    try:
        import ctypes

        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, TITLE)
        if not hwnd or not ICON.exists():
            return
        for size, which in ((16, 0), (32, 1)):                  # ICON_SMALL, ICON_BIG
            h = u.LoadImageW(None, str(ICON), 1, size * 2 if which else size, size * 2 if which else size, 0x10)
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)          # WM_SETICON
    except Exception as exc:
        log.debug("window icon: %s", exc)


def main():
    import webview

    from ..winutil import single_instance

    instance = single_instance("MaxAssistantApp")               # noqa: F841 (held while open)
    if instance is None:
        _focus_existing()
        return
    _set_app_id()
    if not ICON.exists():
        try:
            make_icon()
        except Exception as exc:
            log.warning("couldn't make the icon: %s", exc)
    cfg = load_config()
    d = cfg.get("dashboard", {}) or {}
    base = f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}"
    api = AppApi(base)
    online = api.status()["online"]
    if not online:
        api.start()
    window = webview.create_window(
        TITLE, url=base + "/" if online else str(HERE / "starting.html"), js_api=api,
        width=1360, height=860, min_size=(420, 560), background_color="#060A13", text_select=True,
    )
    api._window = window
    window.events.shown += _set_window_icon
    webview.start(private_mode=False, storage_path=str(ROOT / "data" / "app-webview"))


if __name__ == "__main__":
    main()
