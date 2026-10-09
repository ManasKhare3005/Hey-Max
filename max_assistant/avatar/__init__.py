"""Max's avatar on the desktop (phase A spike): a transparent, frameless, always-on-top window in
the bottom-right corner, just above the clock, showing dashboard/dist/avatar.html.

A separate process, like the overlay, so it can never stall the voice loop. It prefers the
integrated GPU (Chromium's --force_low_power_gpu for this window's own WebView2 instance, plus
"low-power" in the page), leaving the NVIDIA GPU to the language model.

Run: .venv\\Scripts\\pythonw -m max_assistant.avatar [--fps 60] [--tex 1024] [--debug] [--gpu default]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

from ..config import ROOT

log = logging.getLogger(__name__)
PAGE = ROOT / "dashboard" / "dist" / "avatar.html"
STATS = ROOT / "data" / "avatar-stats.json"
SIZE = (300, 460)


class AvatarApi:
    """Called by the page. Attributes are underscored so pywebview doesn't walk the window."""

    def __init__(self, stats_file: Path):
        self._stats_file = stats_file

    def report(self, stats: dict):
        try:
            stats = {**stats, "pid": os.getpid(), "at": time.time()}
            self._stats_file.write_text(json.dumps(stats), encoding="utf-8")
        except OSError:
            pass


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


def main(argv: list[str] | None = None):
    p = argparse.ArgumentParser(prog="max_assistant.avatar")
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--tex", type=int, default=0, help="shrink textures above this size (0 = originals)")
    p.add_argument("--scale", type=float, default=2, help="render pixels per screen pixel (2 = supersampled)")
    p.add_argument("--gpu", choices=["low-power", "default"], default="low-power")
    p.add_argument("--debug", action="store_true", help="show fps / GPU stats on the avatar")
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
    query = f"?fps={args.fps}&tex={args.tex}&scale={args.scale:g}" + ("&debug=1" if args.debug else "")
    x, y = corner_position()
    webview.create_window(
        "Max avatar", url=str(PAGE) + query, js_api=AvatarApi(STATS), width=SIZE[0], height=SIZE[1], x=x, y=y,
        frameless=True, easy_drag=True, on_top=True, resizable=False, transparent=True, shadow=False,
        focus=False, background_color="#000000",
    )
    webview.start(private_mode=False, storage_path=str(ROOT / "data" / "avatar-webview"))


if __name__ == "__main__":
    main()
