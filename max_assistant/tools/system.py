"""Laptop-control tools: apps, files, media, volume, power, notes."""
from __future__ import annotations

import datetime as dt
import difflib
import os
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from ..config import resolve_path
from .registry import ToolRegistry

IS_WINDOWS = sys.platform == "win32"

# ---------- Windows helpers ----------

VK = {"volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
      "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "play_pause": 0xB3}


def _press(key: str, times: int = 1):
    if not IS_WINDOWS:
        raise RuntimeError("Only supported on Windows")
    import ctypes

    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(VK[key], 0, 0, 0)
        user32.keybd_event(VK[key], 0, 2, 0)  # KEYEVENTF_KEYUP
        time.sleep(0.01)


def start_menu_entries() -> dict[str, Path]:
    """Map lowercase app names to their Start Menu shortcuts."""
    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    entries: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() in (".lnk", ".url"):
                entries.setdefault(p.stem.lower(), p)
    return entries


def best_match(query: str, names: list[str]) -> str | None:
    q = query.lower().strip()
    if q in names:
        return q
    starts = [n for n in names if n.startswith(q)]
    if starts:
        return min(starts, key=len)
    contains = [n for n in names if q in n]
    if contains:
        return min(contains, key=len)
    close = difflib.get_close_matches(q, names, n=1, cutoff=0.6)
    return close[0] if close else None


def _launch(target: str):
    if IS_WINDOWS:
        subprocess.Popen(f'start "" "{target}"', shell=True)
    else:
        subprocess.Popen(["xdg-open", target])


def _clean_app_name(name: str) -> str:
    name = name.lower().strip().rstrip(".")
    name = re.sub(r"^(the|my)\s+", "", name)
    return re.sub(r"\s+(app|application|program)$", "", name)


# ---------- registration ----------

# Google results tabs -> URL parameters (udm/tbm are what the tab links themselves use)
GOOGLE_SECTIONS = {
    "all": "",
    "images": "&udm=2",
    "videos": "&tbm=vid",
    "news": "&tbm=nws",
    "shopping": "&tbm=shop",
    "maps": None,          # separate site
}


def google_url(query: str, section: str = "all") -> str:
    from urllib.parse import quote_plus

    q = quote_plus(query)
    if section == "maps":
        return f"https://www.google.com/maps/search/{q}"
    return f"https://www.google.com/search?q={q}{GOOGLE_SECTIONS.get(section) or ''}"


def register(reg: ToolRegistry):
    ctx = reg.context

    @reg.tool("Get the current local date and time.", params={}, direct=True)
    def get_datetime():
        now = dt.datetime.now()
        return now.strftime("It's %I:%M %p on %A, %B %d, %Y.").replace(" 0", " ")

    @reg.tool(
        "Open or launch an application on the laptop, e.g. 'spotify', 'notepad', 'vs code', 'chrome'.",
        params={"name": {"type": "string", "description": "App name as the user said it"}},
        direct=True,
    )
    def open_app(name: str):
        spoken = _clean_app_name(name)
        aliases = {k.lower(): v for k, v in (ctx.cfg.get("apps") or {}).items()}
        if spoken in aliases:
            _launch(aliases[spoken])
            return f"Opened {spoken}."
        entries = start_menu_entries() if IS_WINDOWS else {}
        match = best_match(spoken, list(entries))
        if match:
            os.startfile(str(entries[match]))  # type: ignore[attr-defined]
            return f"Opened {match}."
        alias_match = best_match(spoken, list(aliases))
        if alias_match:
            _launch(aliases[alias_match])
            return f"Opened {alias_match}."
        return f"I couldn't find an app called {name} on this laptop."

    @reg.tool(
        "Close (force-quit) a running application. Unsaved work in it may be lost.",
        params={"name": {"type": "string", "description": "App name, e.g. 'chrome' or 'spotify'"}},
        risky=True,
        confirm="Close {name}? Unsaved work there could be lost.",
        direct=True,
    )
    def close_app(name: str):
        import psutil

        spoken = _clean_app_name(name).replace(" ", "")
        aliases = {k.lower().replace(" ", ""): v for k, v in (ctx.cfg.get("apps") or {}).items()}
        target = aliases.get(spoken, spoken)
        target = Path(str(target)).stem.lower().rstrip(":")
        killed = set()
        for proc in psutil.process_iter(["name"]):
            pname = (proc.info.get("name") or "").lower()
            if pname and (Path(pname).stem == target or target in pname):
                try:
                    proc.kill()
                    killed.add(pname)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        return f"Closed {', '.join(sorted(killed))}." if killed else f"{name} doesn't seem to be running."

    @reg.tool(
        "Open a website in the default browser, or search Google. Accepts a URL/domain, or a search "
        "phrase. To switch an earlier search to another Google tab (e.g. 'show images instead', "
        "'open the videos tab'), call again with the same search phrase and that section.",
        params={
            "target": {"type": "string",
                       "description": "A domain like 'youtube.com', or the search phrase, e.g. 'cute dog pictures'"},
            "section": {"type": "string", "enum": list(GOOGLE_SECTIONS),
                        "description": "Google tab. Leave as 'all' unless the user explicitly says "
                                       "images/pictures/photos, videos, news, shopping or maps."},
        },
        required=["target"],
        direct=True,
    )
    def open_website(target: str, section: str = "all"):
        t = target.strip()
        if re.match(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$", t):
            webbrowser.open(t if t.startswith("http") else f"https://{t}")
            site = re.sub(r"^https?://(www\.)?", "", t).rstrip("/")
            return f"Opened {site}."   # spoken, so no "https colon slash slash"
        section = section if section in GOOGLE_SECTIONS else "all"
        webbrowser.open(google_url(t, section))
        if section == "all":
            return f"Searching Google for {t}."
        return f"Here are Google {section.title()} results for {t}."

    @reg.tool(
        "Control media playback (Spotify, YouTube, etc.).",
        params={"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}},
        direct=True,
    )
    def media_control(action: str):
        _press(action)
        return {"play_pause": "Toggled playback.", "next": "Skipped.",
                "previous": "Went back.", "stop": "Stopped."}.get(action, "Done.")

    @reg.tool(
        "Change system volume. Use action 'set' with a level 0-100, or up/down/mute.",
        params={
            "action": {"type": "string", "enum": ["set", "up", "down", "mute"]},
            "level": {"type": "integer", "description": "0-100, only for 'set'"},
        },
        required=["action"],
        direct=True,
    )
    def volume(action: str, level: int | None = None):
        if action == "mute":
            _press("volume_mute")
            return "Toggled mute."
        if action == "up":
            _press("volume_up", 5)
            return "Volume up."
        if action == "down":
            _press("volume_down", 5)
            return "Volume down."
        if level is None:
            return "Error: tell me what level to set, 0 to 100."
        level = max(0, min(100, int(level)))
        _press("volume_down", 50)          # each key press is 2%, so go to 0 first
        _press("volume_up", round(level / 2))
        return f"Volume set to about {level} percent."

    @reg.tool("Report battery, CPU and memory usage of the laptop.", params={})
    def system_status():
        import psutil

        parts = [f"CPU at {psutil.cpu_percent(interval=0.5):.0f} percent",
                 f"memory at {psutil.virtual_memory().percent:.0f} percent"]
        bat = psutil.sensors_battery()
        if bat:
            plug = "charging" if bat.power_plugged else "on battery"
            parts.insert(0, f"battery at {bat.percent:.0f} percent, {plug}")
        return "Laptop status: " + ", ".join(parts) + "."

    @reg.tool(
        "Save a note or reminder text to the user's notes file.",
        params={"text": {"type": "string"}},
        direct=True,
    )
    def take_note(text: str):
        path = resolve_path(ctx.cfg.notes.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- [{dt.datetime.now():%Y-%m-%d %H:%M}] {text.strip()}\n")
        return "Noted."

    @reg.tool(
        "Read back the most recent saved notes.",
        params={"count": {"type": "integer", "description": "How many notes, default 5"}},
        required=[],
    )
    def read_notes(count: int = 5):
        path = resolve_path(ctx.cfg.notes.file)
        if not path.exists():
            return "You don't have any notes yet."
        lines = [l.strip("- \n") for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return "Your latest notes: " + "; ".join(lines[-int(count or 5):])

    @reg.tool(
        "Find files by name in Desktop, Documents, Downloads, Pictures, Music and Videos.",
        params={"query": {"type": "string", "description": "Part of the file name"}},
    )
    def find_files(query: str):
        home = Path.home()
        folders = [home / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")]
        q = query.lower().strip()
        hits: list[Path] = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", ".git")]
                for fn in files:
                    if q in fn.lower():
                        hits.append(Path(root) / fn)
                if len(hits) >= 25:
                    break
        if not hits:
            return f"No files matching '{query}'."
        hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        ctx.last_files = [str(p) for p in hits[:10]]
        listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(hits[:10]))
        return f"Found {len(hits)} file(s), newest first:\n{listing}"

    @reg.tool(
        "Open a file with its default app. Accepts a full path or a number from the last find_files result.",
        params={"path": {"type": "string"}},
        direct=True,
    )
    def open_file(path: str):
        p = path.strip().strip('"')
        if p.isdigit() and getattr(ctx, "last_files", None):
            idx = int(p) - 1
            if 0 <= idx < len(ctx.last_files):
                p = ctx.last_files[idx]
        fp = Path(p).expanduser()
        if not fp.exists():
            return f"Error: {p} doesn't exist."
        if IS_WINDOWS:
            os.startfile(str(fp))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(fp)])
        return f"Opened {fp.name}."

    @reg.tool("Take a screenshot and save it to Pictures/Max.", params={}, direct=True)
    def screenshot():
        from PIL import ImageGrab

        folder = Path.home() / "Pictures" / "Max"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"screenshot_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
        ImageGrab.grab(all_screens=True).save(path)
        return f"Saved screenshot to {path}."

    @reg.tool("Lock the laptop screen.", params={}, direct=True)
    def lock_screen():
        if IS_WINDOWS:
            import ctypes

            ctypes.windll.user32.LockWorkStation()
        return "Locked."

    @reg.tool(
        "Shut down, restart, or sleep the laptop.",
        params={"action": {"type": "string", "enum": ["shutdown", "restart", "sleep"]}},
        risky=True,
        confirm="Do you really want me to {action} the laptop?",
        direct=True,
    )
    def power(action: str):
        if not IS_WINDOWS:
            return "Error: only supported on Windows."
        if action == "shutdown":
            subprocess.Popen("shutdown /s /t 10", shell=True)
            return "Shutting down in 10 seconds. Say 'cancel shutdown' or run 'shutdown /a' to stop it."
        if action == "restart":
            subprocess.Popen("shutdown /r /t 10", shell=True)
            return "Restarting in 10 seconds."
        subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
        return "Going to sleep."

    @reg.tool("Cancel a pending shutdown or restart.", params={}, direct=True)
    def cancel_shutdown():
        if IS_WINDOWS:
            subprocess.Popen("shutdown /a", shell=True)
        return "Cancelled."

    @reg.tool(
        "Put Max's AI models to sleep to free the GPU (e.g. before gaming). "
        "Use when the user says 'go to sleep', 'free the GPU' or similar.",
        params={},
        direct=True,
    )
    def free_gpu():
        for m in {ctx.cfg.llm.fast_model, ctx.cfg.llm.planner_model}:
            ctx.llm.unload(m)
        ctx.models_asleep = True
        return "Models unloaded; the GPU is free. I'll still hear my wake word."
