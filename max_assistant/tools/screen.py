"""Read my screen: "what does this error mean?", "summarise this page".

Only when asked. The window in front is captured into memory (never saved), its text read with
Windows' built-in OCR (winocr), and the text handed to the local model with the question.
"""
from __future__ import annotations

import logging
import re
import sys
import time

from .registry import ForLLM, ToolRegistry

log = logging.getLogger(__name__)
MAX_CHARS = 3500        # about 900 tokens: leaves room in the model's window


def front_window():
    """(title, (left, top, right, bottom)) of the window in front, or None (not Windows)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return buf.value, (rect.left, rect.top, rect.right, rect.bottom)


def ocr_lines(image) -> list[str]:
    import winocr

    result = winocr.recognize_pil_sync(image.convert("RGB"), "en")
    if not isinstance(result, dict):
        return []
    return [line["text"] for line in result.get("lines", [])] or ([result["text"]] if result.get("text") else [])


def ocr(image) -> str:
    return "\n".join(ocr_lines(image))


# A blinking text cursor right after a word is read as "l", "I" or "|" ("hey how are youl").
CURSOR_CHARS = "lI|!1"
# Window furniture that isn't the content: status bars and menu bars (Notepad, VS Code, Office...)
STATUS_BAR = re.compile(r"\bLn \d+, ?Col \d+\b|\b(UTF-8|UTF-16|Windows \(CRLF\)|Unix \(LF\)|Plain text|Spaces: \d+)\b", re.I)
MENU_WORDS = {"file", "edit", "view", "selection", "go", "run", "terminal", "help", "format", "insert", "tools",
              "window", "home", "layout", "references", "review", "draw", "design", "history", "bookmarks"}


def merge_reads(first: list[str], second: list[str]) -> list[str]:
    """Two reads ~0.6 s apart: where one line is the other plus a stray cursor-like last
    character, keep the shorter (the cursor blinks, so it's only in one of them)."""
    out = []
    for i, a in enumerate(first):
        b = second[i] if i < len(second) else a
        if len(a) == len(b) + 1 and a.startswith(b) and a[-1] in CURSOR_CHARS:
            a = b
        out.append(a)
    return out


def clean(lines: list[str]) -> list[str]:
    keep = []
    for line in lines:
        words = line.lower().split()
        if words and all(w in MENU_WORDS for w in words):
            continue                                          # "File Edit View"
        if STATUS_BAR.search(line) and len(line) < 120:
            continue                                          # "Ln 1, Col 16  100%  Windows (CRLF)  UTF-8"
        keep.append(line)
    return keep


def capture(whole_screen: bool = False):
    """The front window (or the whole screen) as a PIL image, plus the window title."""
    from PIL import ImageGrab

    win = None if whole_screen else front_window()
    if win and win[1][2] - win[1][0] > 50 and win[1][3] - win[1][1] > 50:
        title, box = win
        return ImageGrab.grab(bbox=box, all_screens=True), title
    return ImageGrab.grab(all_screens=True), "whole screen"


def register(reg: ToolRegistry):
    if sys.platform != "win32":
        return

    @reg.tool(
        "Read the text in the window the user is looking at on the laptop (or the whole screen) and answer "
        "about it: 'what does this error mean', 'summarise what's on my screen', 'what does this say'. "
        "Only when they ask about their screen.",
        params={"question": {"type": "string", "description": "What they want to know about the screen"},
                "whole_screen": {"type": "boolean", "description": "True only if they say the whole screen"}},
        required=["question"],
    )
    def read_screen(question: str, whole_screen: bool = False):
        image = None
        try:
            image, title = capture(bool(whole_screen))
            first = ocr_lines(image)
            time.sleep(0.6)                                      # a blink later: the text cursor flips
            image, _ = capture(bool(whole_screen))
            second = ocr_lines(image)
            text = "\n".join(clean(merge_reads(first, second))).strip()
        except Exception as exc:
            return f"Error: couldn't read the screen ({exc})."
        finally:
            image = None                                          # nothing is kept
        if not text:
            return "I couldn't find any text in that window."
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n…"
        return ForLLM(f"Text read from the user's screen by OCR (window: {title}). OCR can add or misread a letter, "
                      f"so don't point out typos unless asked; ignore leftover menu or toolbar words.\n{text}\n\n"
                      f"Answer briefly, in 1-3 spoken sentences: {question}")
