"""Desktop overlay: a small always-on-top widget to start/stop Max, push-to-talk, and glance at
chats, memories and what's due today.

It's a separate lightweight process (pywebview, using Windows' built-in Edge WebView) that
talks to Max over the local API, so it keeps working while Max is stopped and can start it.
Run: .venv\\Scripts\\pythonw -m max_assistant.overlay  (install_autostart.ps1 sets this up)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import requests

from ..config import ROOT, load_config, resolve_path

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
COLLAPSED = (468, 64)
EXPANDED = (468, 520)


class OverlayApi:
    """Methods callable from the widget's JavaScript (window.pywebview.api.*). Attributes are
    underscored: pywebview exposes public attributes to JS and would try to walk the window object."""

    def __init__(self, base_url: str, state_file: Path):
        self._base = base_url.rstrip("/")
        self._state_file = state_file
        self._window = None
        self._starting_until = 0.0

    # ----- talking to Max -----
    def _get(self, path: str, timeout: float = 2.0):
        r = requests.get(self._base + path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, timeout: float = 3.0):
        r = requests.post(self._base + path, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def status(self):
        try:
            s = self._get("/api/state", 1.5)
            return {"online": True, "name": s.get("name", "Max"), "stage": (s.get("stage") or {}).get("stage", ""),
                    "mode": s.get("mode", ""), "approvals": len(s.get("approvals") or []),
                    "next_reminder": s.get("next_reminder")}
        except Exception:
            return {"online": False, "starting": time.monotonic() < self._starting_until}

    def start(self):
        """Launch Max in background (tray) mode if it isn't running."""
        if self.status()["online"]:
            return {"ok": True, "already": True}
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pythonw if pythonw.exists() else sys.executable)
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0     # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen([exe, "-m", "max_assistant", "--tray"], cwd=str(ROOT), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._starting_until = time.monotonic() + 90        # models take a while to load
        return {"ok": True}

    def stop(self):
        return self._control("quit")

    def listen(self):
        return self._control("listen")

    def pause(self, paused: bool):
        return self._control("pause" if paused else "resume")

    def _control(self, action: str):
        try:
            return self._post(f"/api/control/{action}")
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def turns(self):
        try:
            return {"ok": True, "items": self._get("/api/turns?limit=25")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def facts(self):
        try:
            return {"ok": True, "items": self._get("/api/facts")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def today(self):
        try:
            return {"ok": True, **self._get("/api/today", 25)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    # ----- meeting / lecture notes -----
    def notes_status(self):
        try:
            return {"ok": True, **self._get("/api/notes/status")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def notes_start(self, kind: str):
        try:
            r = requests.post(self._base + "/api/notes/start", json={"kind": kind}, timeout=30)
            return r.json() if r.ok else {"ok": False, "error": r.json().get("detail", r.text)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def notes_stop(self):
        try:
            return self._post("/api/notes/stop")
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def notes_list(self):
        try:
            return {"ok": True, "items": self._get("/api/notes")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def notes_open(self, note_id: int):
        try:
            return self._post(f"/api/notes/{int(note_id)}/open")
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def open_dashboard(self):
        webbrowser.open(self._base)

    # ----- the window itself -----
    def expand(self, expanded: bool):
        if self._window is not None:
            w, h = EXPANDED if expanded else COLLAPSED
            self._window.resize(w, h)

    def close_overlay(self):
        if self._window is not None:
            self._window.destroy()

    def save_position(self, x: int, y: int):
        try:
            self._state_file.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")
        except OSError:
            pass


def default_position() -> tuple[int, int]:
    """Bottom-right corner, above the taskbar."""
    try:
        import ctypes

        u = ctypes.windll.user32
        return u.GetSystemMetrics(0) - COLLAPSED[0] - 24, u.GetSystemMetrics(1) - COLLAPSED[1] - 72
    except Exception:
        return 100, 100


def main():
    import webview

    from ..winutil import single_instance

    instance = single_instance("MaxAssistantOverlay")      # noqa: F841 (held while running)
    if instance is None:
        return                                             # the overlay is already on screen
    cfg = load_config()
    d = cfg.get("dashboard", {}) or {}
    base = f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}"
    state_file = resolve_path("data/overlay.json")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        pos = json.loads(state_file.read_text(encoding="utf-8"))
        x, y = int(pos["x"]), int(pos["y"])
    except Exception:
        x, y = default_position()

    api = OverlayApi(base, state_file)
    window = webview.create_window(
        f"{cfg.assistant.name} overlay", url=str(HERE / "overlay.html"), js_api=api,
        width=COLLAPSED[0], height=COLLAPSED[1], x=x, y=y, frameless=True, easy_drag=False,
        on_top=True, resizable=False, transparent=True, shadow=False, background_color="#060A13",
        focus=False,
    )
    api._window = window
    window.events.moved += lambda x, y: api.save_position(x, y)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
