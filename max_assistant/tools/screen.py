"""Read my screen: "what does this error mean?", "summarise this page".

Only when asked. The window in front is captured into memory (never saved), its text read with
Windows' built-in OCR (winocr), and the text handed to the local model with the question.
"""
from __future__ import annotations

import logging
import sys

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


def ocr(image) -> str:
    import winocr

    result = winocr.recognize_pil_sync(image.convert("RGB"), "en")
    lines = [line["text"] for line in result.get("lines", [])] if isinstance(result, dict) else []
    return "\n".join(lines) if lines else (result.get("text", "") if isinstance(result, dict) else "")


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
        try:
            image, title = capture(bool(whole_screen))
            text = ocr(image).strip()
        except Exception as exc:
            return f"Error: couldn't read the screen ({exc})."
        finally:
            image = None                                          # nothing is kept
        if not text:
            return "I couldn't find any text in that window."
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n…"
        return ForLLM(f"Text read from the user's screen (window: {title}):\n{text}\n\n"
                      f"Answer briefly, in 1-3 spoken sentences: {question}")
