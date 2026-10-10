"""Max's avatar on the desktop: a transparent, frameless, always-on-top window in the bottom-right
corner, just above the clock, showing dashboard/dist/avatar.html. The page follows Max over the
dashboard WebSocket (stage, wake word, speech loudness for lip sync, models asleep).

A separate process, like the overlay, so it can never stall the voice loop. To stay out of the
way of the language model and Whisper:
- it renders on the integrated GPU (Chromium's --force_low_power_gpu for this window's own
  WebView2 instance), so the NVIDIA card keeps all its memory for the model;
- while Whisper transcribes, it and its browser processes are pinned to the CPU's efficiency
  cores (measured: Whisper +32% slower otherwise, +3% this way) and draw at `busy_fps`. Not all
  the time: on the E-cores alone he dropped a frame or two a second (3.3 ms a frame vs 2 ms);
- it drops to `asleep_fps` when the models sleep;
- it hides (and stops drawing) while a fullscreen game is in front.

Mouse: click him = talk to Max, double click = open the Max app, drag = move (remembered in
data/avatar-position.json; tray "Reset avatar position" puts him back above the clock).

Max starts it (config `avatar.enabled`); it closes itself if Max stays gone. Manual run:
.venv\\Scripts\\pythonw -m max_assistant.avatar [--fps 90] [--debug] [--model avatar/x.vrm]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

from ..config import ROOT, load_config

log = logging.getLogger(__name__)
PAGE = ROOT / "dashboard" / "dist" / "avatar.html"
STATS = ROOT / "data" / "avatar-stats.json"
SNAPSHOT = ROOT / "data" / "avatar-snapshot.png"
POSITION = ROOT / "data" / "avatar-position.json"     # where he was dragged to (window pixels)
SIZE = (400, 420)                  # head and upper body, with room for his hands either side
TITLE = "Max avatar"

# Fullscreen windows from these never hide the avatar (browsers, video, desktop): only games do
NOT_GAMES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
             "vlc.exe", "mpc-hc64.exe", "potplayermini64.exe", "wmplayer.exe", "video.ui.exe", "explorer.exe",
             "applicationframehost.exe", "powerpnt.exe", "code.exe", "windowsterminal.exe", "pythonw.exe", "python.exe"}
GAME_FOLDERS = ("steamapps", "riot games", "epic games", "ea games", "ubisoft", "battle.net", "xboxgames",
                "gog galaxy", "rockstar games", "minecraft", "roblox", "hoyoplay", "genshin", "valorant")


# ----- which CPU cores -----

def efficiency_cores() -> list[int]:
    """Logical processors of the lowest efficiency class (Intel E-cores); [] if all cores are alike."""
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        n = wintypes.ULONG(0)
        k.GetSystemCpuSetInformation(None, 0, ctypes.byref(n), None, 0)
        buf = (ctypes.c_byte * n.value)()
        if not k.GetSystemCpuSetInformation(buf, n, ctypes.byref(n), None, 0):
            return []
        raw, off, cores = bytes(buf), 0, []
        while off < len(raw):                    # SYSTEM_CPU_SET_INFORMATION records
            size = int.from_bytes(raw[off:off + 4], "little")
            cores.append((raw[off + 14], raw[off + 18]))         # LogicalProcessorIndex, EfficiencyClass
            off += size or len(raw)
    except Exception:
        return []
    classes = {c for _, c in cores}
    if len(classes) < 2:
        return []
    low = min(classes)
    return sorted(lp for lp, c in cores if c == low)


def pin_tree(cpus: list[int]):
    """Keep this process and every child (WebView2 starts several) on `cpus`."""
    import psutil

    me = psutil.Process()
    for p in [me, *me.children(recursive=True)]:
        try:
            if sorted(p.cpu_affinity()) != cpus:
                p.cpu_affinity(cpus)
        except psutil.Error:
            pass


# ----- fullscreen games -----

def should_hide(d3d_fullscreen: bool, covers_screen: bool, exe_path: str, extra_games=()) -> bool:
    """Hide for games only: exclusive-fullscreen Direct3D, or a borderless window covering the screen
    from a known game folder or a configured game. Maximised Chrome, videos etc. never hide it."""
    if d3d_fullscreen:
        return True
    if not covers_screen or not exe_path:
        return False
    path = exe_path.lower().replace("/", "\\")
    exe = path.rsplit("\\", 1)[-1]
    if exe in NOT_GAMES:
        return False
    if exe in {g.lower() for g in extra_games}:
        return True
    return any(folder in path for folder in GAME_FOLDERS)


def foreground_state() -> tuple[bool, bool, str]:
    """(exclusive fullscreen Direct3D app?, does the front window cover its whole monitor?, its exe)."""
    import ctypes
    from ctypes import wintypes

    import psutil

    u = ctypes.windll.user32
    state = ctypes.c_int(0)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
    except Exception:
        pass
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return state.value == 3, False, ""
    rect = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    m = info.rcMonitor
    covers = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).exe()
    except psutil.Error:
        exe = ""
    return state.value == 3, covers, exe                      # 3 = QUNS_RUNNING_D3D_FULL_SCREEN


# ----- the window -----

class AvatarApi:
    """Called by the page. Attributes are underscored so pywebview doesn't walk the window."""

    def __init__(self, stats_file: Path, base_url: str = "http://127.0.0.1:8765"):
        self._stats_file = stats_file
        self._base = base_url.rstrip("/")
        self._window = None
        self._hidden_by_user = False
        self._hidden_by_game = False
        self._lock = threading.Lock()
        self._e_cores: list[int] = []     # set by main(); [] = don't pin
        self._busy = False                # Whisper is transcribing: keep off the cores it uses

    def report(self, stats: dict):
        try:
            stats = {**stats, "pid": os.getpid(), "at": time.time()}
            self._stats_file.write_text(json.dumps(stats), encoding="utf-8")
        except OSError:
            pass

    def snapshot(self, data_url: str, mode: str = ""):
        """A rendered frame of the avatar canvas (only the page's own drawing), for checks: one per mode."""
        import base64

        data = base64.b64decode(data_url.split(",", 1)[1])
        SNAPSHOT.write_bytes(data)
        if mode:
            SNAPSHOT.with_name(f"avatar-snapshot-{mode}.png").write_bytes(data)

    def shape(self, cols: int, rows: int, runs: list):
        """Max's outline from the page: clip the window to it so clicks around him reach what's behind."""
        try:
            import ctypes
            from ctypes import wintypes

            u, g = ctypes.windll.user32, ctypes.windll.gdi32
            hwnd = u.FindWindowW(None, TITLE)
            if not hwnd:
                return
            rect = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rect))
            g.CreateRectRgn.restype = wintypes.HRGN
            g.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
            g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
            if not runs:                        # nothing drawn (yet): keep the whole window, never lose it
                u.SetWindowRgn(hwnd, None, True)
                return
            region = g.CreateRectRgn(0, 0, 0, 0)
            for r in outline_rects(cols, rows, runs, rect.right - rect.left, rect.bottom - rect.top):
                part = g.CreateRectRgn(*r)
                g.CombineRgn(region, region, part, 2)                   # RGN_OR
                g.DeleteObject(part)
            if not u.SetWindowRgn(hwnd, region, False):     # no forced repaint; Windows owns the region after this
                g.DeleteObject(region)
        except Exception as exc:
            log.debug("avatar outline: %s", exc)

    def busy(self, flag: bool):
        """From the page: Whisper started / finished transcribing. Only then is the avatar held on the
        E-cores (Whisper uses the others); the rest of the time it may use every core, because the
        E-cores alone drop frames."""
        self._busy = bool(flag)
        threading.Thread(target=self._pin, daemon=True).start()

    def _affinity(self) -> list[int]:
        import psutil

        if self._busy and self._e_cores:
            return self._e_cores
        return list(range(psutil.cpu_count() or 1))

    def _pin(self):
        if not self._e_cores:
            return
        try:
            pin_tree(self._affinity())
        except Exception as exc:
            log.debug("pinning: %s", exc)

    def cursor(self):
        """The mouse relative to the window, and the window's size (screen pixels), or None if hidden."""
        if self._hidden_by_user or self._hidden_by_game:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            hwnd = u.FindWindowW(None, TITLE)
            pt, rect = wintypes.POINT(), wintypes.RECT()
            if not hwnd or not u.GetCursorPos(ctypes.byref(pt)) or not u.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            return [pt.x - rect.left, pt.y - rect.top, rect.right - rect.left, rect.bottom - rect.top]
        except Exception:
            return None

    def listen(self):
        """Clicked: Max listens for a command, as after the wake word."""
        import requests

        def go():
            try:
                requests.post(self._base + "/api/control/listen", timeout=3)
            except Exception as exc:
                log.debug("avatar listen: %s", exc)

        threading.Thread(target=go, daemon=True).start()

    def open_app(self):
        """Double-clicked: the Max window."""
        from ..app import open_app

        open_app()

    def reset_position(self):
        """Tray "Reset avatar position": back above the clock, and forget the dragged-to spot."""
        POSITION.unlink(missing_ok=True)
        if self._window is not None:
            self._window.move(*corner_position())

    def toggle(self):
        """Tray "Show / hide avatar" (arrives from Max as an event the page forwards here)."""
        self._hidden_by_user = not self._hidden_by_user
        self._apply()

    def close(self):
        """Max has been gone for a while: go away too (Max starts the avatar again when it starts)."""
        if self._window is not None:
            threading.Timer(0.1, self._window.destroy).start()

    def _set_game(self, hidden: bool):
        if hidden != self._hidden_by_game:
            self._hidden_by_game = hidden
            self._apply()

    def _apply(self):
        """Hide = window hidden and drawing stopped (frees the iGPU too); show without stealing focus."""
        w = self._window
        if w is None:
            return
        hidden = self._hidden_by_user or self._hidden_by_game
        with self._lock:
            try:
                w.evaluate_js(f"window.maxAvatar && window.maxAvatar.setHidden({'true' if hidden else 'false'})")
                import ctypes

                hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 0 if hidden else 4)       # SW_HIDE / SW_SHOWNOACTIVATE
            except Exception as exc:
                log.debug("avatar show/hide: %s", exc)


def outline_rects(cols: int, rows: int, runs: list, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Grid runs [row, first col, end col) -> window-pixel rectangles; rows with the same runs merge."""
    cw, ch = width / max(1, cols), height / max(1, rows)
    by_row: dict[int, list[tuple[int, int]]] = {}
    for y, x0, x1 in runs:
        by_row.setdefault(int(y), []).append((int(x0), int(x1)))
    rects: list[tuple[int, int, int, int]] = []
    open_: dict[tuple[int, int], int] = {}                        # (x0, x1) -> first row of its block
    for y in range(rows + 1):
        here = set(by_row.get(y, []))
        for span in [s for s in open_ if s not in here]:
            y0 = open_.pop(span)
            rects.append((round(span[0] * cw), round(y0 * ch), round(span[1] * cw), round(y * ch)))
        for span in here:
            open_.setdefault(span, y)
    return rects


def see_through(window):
    """Make everything the page doesn't draw on transparent (call once the window exists).

    pywebview only makes the web page transparent: the form behind it keeps its default light
    grey, which showed as a white box around Max. Painting the form black and giving the window a
    "transparent gradient" accent with a fully transparent colour (SetWindowCompositionAttribute,
    as taskbar tools use) makes DWM show that area as see-through. Tried and dropped: a colour key
    (whole window click-through, so Max couldn't be dragged), DwmExtendFrameIntoClientArea and
    blur-behind with an empty region (both left a black box on this frameless window).
    """
    try:
        import ctypes

        from System import Action
        from System.Drawing import Color

        form = window.native

        class ACCENT(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int), ("color", ctypes.c_uint), ("anim", ctypes.c_int)]

        class WCA_DATA(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

        def apply():
            form.BackColor = Color.Black
            accent = ACCENT(2, 2, 0x00000000, 0)       # ACCENT_ENABLE_TRANSPARENTGRADIENT, colour with alpha 0
            data = WCA_DATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))  # WCA_ACCENT_POLICY
            ctypes.windll.user32.SetWindowCompositionAttribute(ctypes.c_void_p(form.Handle.ToInt64()), ctypes.byref(data))
            form.Invalidate()

        form.Invoke(Action(apply))
    except Exception as exc:
        log.warning("avatar: couldn't make the background transparent: %s", exc)


def saved_position(path: Path | None = None, size: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Where he was dragged to last time, if that spot is still on a screen (monitors change).
    With the window's `size` now: shifted so he stays where he was if the window grew or shrank
    (same centre, same bottom edge)."""
    try:
        pos = json.loads((path or POSITION).read_text(encoding="utf-8"))
        x, y, w, h = int(pos["x"]), int(pos["y"]), int(pos.get("w", SIZE[0])), int(pos.get("h", SIZE[1]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        centre = wintypes.POINT(x + w // 2, y + h // 3)          # his head must be on a screen
        if not ctypes.windll.user32.MonitorFromPoint(centre, 0):  # MONITOR_DEFAULTTONULL
            return None
    except Exception:
        pass
    if size:
        return x + (w - size[0]) // 2, y + h - size[1]
    return x, y


def restore_position():
    """Move the window to the remembered spot (window pixels, as saved)."""
    rect = window_rect()
    pos = saved_position(size=(rect[2] - rect[0], rect[3] - rect[1]) if rect else None)
    if pos is None:
        return
    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, TITLE)
        if hwnd:   # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            ctypes.windll.user32.SetWindowPos(hwnd, 0, pos[0], pos[1], 0, 0, 0x0001 | 0x0004 | 0x0010)
    except Exception as exc:
        log.debug("avatar restore position: %s", exc)


class PositionKeeper:
    """Saves the window's spot once a drag has settled (two checks in a row at the same place)."""

    def __init__(self, path: Path | None = None):
        self.path = path or POSITION
        self.last = None
        self.pending = False

    def check(self, rect: tuple[int, int, int, int] | None):
        if rect is None:
            return
        if self.last is not None and rect != self.last:
            self.pending = True
        elif self.pending:
            self.pending = False
            x, y, r, b = rect
            try:
                self.path.write_text(json.dumps({"x": x, "y": y, "w": r - x, "h": b - y}), encoding="utf-8")
            except OSError:
                pass
        self.last = rect


def window_rect() -> tuple[int, int, int, int] | None:
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, TITLE)
        rect = wintypes.RECT()
        if hwnd and u.IsWindowVisible(hwnd) and u.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def corner_position(size=SIZE, margin: int = 8) -> tuple[int, int]:
    """Bottom-right of the work area (the screen minus the taskbar), i.e. just above the clock."""
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)      # SPI_GETWORKAREA
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
        return int(rect.right / scale) - size[0] - margin, int(rect.bottom / scale) - size[1] - margin
    except Exception:
        return 100, 100


def watchers(api: AvatarApi, hide_for_games: bool, games: list[str], stop: threading.Event):
    """Every couple of seconds: keep new WebView2 processes on the right cores; hide for fullscreen
    games; remember where he was dragged to."""
    keeper = PositionKeeper()
    while not stop.wait(1.5):
        keeper.check(window_rect())
        api._pin()
        if hide_for_games:
            try:
                api._set_game(should_hide(*foreground_state(), extra_games=games))
            except Exception as exc:
                log.debug("fullscreen check: %s", exc)


def launch_with_max(cfg):
    """Called by Max at start: open the avatar if it's enabled (a second copy just exits)."""
    if not (cfg.get("avatar", {}) or {}).get("enabled", False):
        return
    if not PAGE.exists():
        log.warning("avatar: dashboard not built (cd dashboard && npm run build)")
        return
    from ..app import launch_detached

    launch_detached("-m", "max_assistant.avatar")


def main(argv: list[str] | None = None):
    cfg = load_config()
    a = cfg.get("avatar", {}) or {}
    d = cfg.get("dashboard", {}) or {}
    p = argparse.ArgumentParser(prog="max_assistant.avatar")
    p.add_argument("--fps", type=int, default=a.get("fps", 90), help="frame cap; 90 = even pacing on the 180 Hz screen, 0 = every refresh")
    p.add_argument("--asleep-fps", type=int, default=a.get("asleep_fps", 30))
    p.add_argument("--busy-fps", type=int, default=a.get("busy_fps", 60), help="while Whisper transcribes")
    p.add_argument("--frame", choices=["upper", "full"], default=a.get("frame", "upper"))
    p.add_argument("--tex", type=int, default=0, help="shrink textures above this size (0 = originals)")
    p.add_argument("--scale", type=float, default=a.get("scale", 2), help="render pixels per screen pixel (2 = supersampled)")
    p.add_argument("--gpu", choices=["low-power", "default"], default="low-power")
    p.add_argument("--debug", action="store_true", help="show fps / GPU stats on the avatar")
    p.add_argument("--snapshot", action="store_true", help="save one rendered frame to data/avatar-snapshot.png")
    p.add_argument("--model", default=a.get("model", ""), help="VRM to show instead of avatar/model.vrm (path under dashboard/dist)")
    p.add_argument("--standalone", action="store_true", help="don't close when Max isn't running")
    p.add_argument("--ws", default="", help="Max's event WebSocket (default: from config.yaml dashboard host/port)")
    args = p.parse_args(argv)

    import webview

    from ..winutil import single_instance

    instance = single_instance("MaxAssistantAvatar")       # noqa: F841 (held while running)
    if instance is None:
        return
    if not PAGE.exists():
        raise SystemExit("Build the dashboard first: cd dashboard && npm run build")
    if args.gpu == "low-power":
        # Only this window's WebView2 instance (its own data folder below) gets the flag
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--force_low_power_gpu"
    STATS.parent.mkdir(parents=True, exist_ok=True)
    STATS.unlink(missing_ok=True)
    ws = args.ws or f"ws://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}/api/ws"
    # v= the build's time: WebView2 caches avatar.html, and would otherwise keep showing an old build
    query = (f"?v={int(PAGE.stat().st_mtime)}&fps={args.fps}&asleep_fps={args.asleep_fps}&busy_fps={args.busy_fps}&frame={args.frame}"
             f"&tex={args.tex}&scale={args.scale:g}&lipsync_ms={a.get('lip_sync_delay_ms', 80)}"
             f"&ws={quote(ws, safe='')}&standalone={int(args.standalone)}"
             + ("&debug=1" if args.debug else "") + ("&snapshot=1" if args.snapshot else "")
             # encoded: a "/" in the query would confuse pywebview's file server
             + (f"&model={quote(args.model, safe='')}" if args.model else ""))
    x, y = corner_position()
    api = AvatarApi(STATS, f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}")
    api._window = webview.create_window(
        TITLE, url=str(PAGE) + query, js_api=api, width=SIZE[0], height=SIZE[1], x=x, y=y,
        frameless=True, easy_drag=True, on_top=True, resizable=False, transparent=True, shadow=False,
        focus=False, background_color="#000000",
    )
    api._window.events.shown += lambda: (see_through(api._window), restore_position())
    api._e_cores = efficiency_cores() if a.get("efficiency_cores", True) else []
    stop = threading.Event()
    threading.Thread(target=watchers, args=(api, a.get("hide_for_fullscreen_games", True),
                                            a.get("games", []) or [], stop), daemon=True).start()
    try:
        webview.start(private_mode=False, storage_path=str(ROOT / "data" / "avatar-webview"))
    finally:
        stop.set()


if __name__ == "__main__":
    main()
