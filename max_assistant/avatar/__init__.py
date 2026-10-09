"""Max's avatar on the desktop: a transparent, frameless, always-on-top window in the bottom-right
corner, just above the clock, showing dashboard/dist/avatar.html. The page follows Max over the
dashboard WebSocket (stage, wake word, speech loudness for lip sync, models asleep).

A separate process, like the overlay, so it can never stall the voice loop. To stay out of the
way of the language model and Whisper:
- it renders on the integrated GPU (Chromium's --force_low_power_gpu for this window's own
  WebView2 instance), so the NVIDIA card keeps all its memory for the model;
- it and its browser processes are pinned to the CPU's efficiency cores (measured: Whisper +32%
  slower otherwise, +3% this way);
- it drops to `busy_fps` while Whisper transcribes and to `asleep_fps` when the models sleep;
- it hides (and stops drawing) while a fullscreen game is in front.

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
SIZE = (340, 420)                  # head and upper body
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

    def __init__(self, stats_file: Path):
        self._stats_file = stats_file
        self._window = None
        self._hidden_by_user = False
        self._hidden_by_game = False
        self._lock = threading.Lock()

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


def watchers(api: AvatarApi, cpus: list[int], hide_for_games: bool, games: list[str], stop: threading.Event):
    """Every couple of seconds: keep new WebView2 processes on the E-cores; hide for fullscreen games."""
    while not stop.wait(1.5):
        if cpus:
            try:
                pin_tree(cpus)
            except Exception as exc:
                log.debug("pinning: %s", exc)
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
    p.add_argument("--busy-fps", type=int, default=a.get("busy_fps", 30), help="while Whisper transcribes")
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
    query = (f"?fps={args.fps}&asleep_fps={args.asleep_fps}&busy_fps={args.busy_fps}&frame={args.frame}"
             f"&tex={args.tex}&scale={args.scale:g}&lipsync_ms={a.get('lip_sync_delay_ms', 80)}"
             f"&ws={quote(ws, safe='')}&standalone={int(args.standalone)}"
             + ("&debug=1" if args.debug else "") + ("&snapshot=1" if args.snapshot else "")
             # encoded: a "/" in the query would confuse pywebview's file server
             + (f"&model={quote(args.model, safe='')}" if args.model else ""))
    x, y = corner_position()
    api = AvatarApi(STATS)
    api._window = webview.create_window(
        TITLE, url=str(PAGE) + query, js_api=api, width=SIZE[0], height=SIZE[1], x=x, y=y,
        frameless=True, easy_drag=True, on_top=True, resizable=False, transparent=True, shadow=False,
        focus=False, background_color="#000000",
    )
    cpus = efficiency_cores() if a.get("efficiency_cores", True) else []
    stop = threading.Event()
    threading.Thread(target=watchers, args=(api, cpus, a.get("hide_for_fullscreen_games", True),
                                            a.get("games", []) or [], stop), daemon=True).start()
    try:
        webview.start(private_mode=False, storage_path=str(ROOT / "data" / "avatar-webview"))
    finally:
        stop.set()


if __name__ == "__main__":
    main()
