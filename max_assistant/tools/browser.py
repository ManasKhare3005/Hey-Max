"""Browser tools: click, type, scroll/navigate, read the page, manage tabs in Max's Chrome window.
Opening sites and Google searches goes through open_website (tools/system.py), which uses
this browser when it's enabled."""
from __future__ import annotations

import logging

from ..browser import RISKY_CLICK, BrowserSession, Element, pick
from ..web import clean, select_passages
from .registry import DECLINED, ForLLM, ToolRegistry

log = logging.getLogger(__name__)


def element_list(elements: list[Element], limit: int = 10) -> str:
    return "\n".join(e.describe() for e in elements[:limit])


def register(reg: ToolRegistry):
    ctx = reg.context
    cfg = ctx.cfg.get("browser", {}) if ctx is not None else {}
    if not cfg.get("enabled", True):
        return
    session = BrowserSession(cfg.get("profile_dir", "data/browser-profile"), cfg.get("channel", "chrome"),
                             cfg.get("headless", False))
    if ctx is not None:
        ctx.browser = session

    def confirm(prompt: str) -> bool:
        return bool(ctx and ctx.confirm(prompt))

    def need_browser():
        if not session.running:
            return "Error: no browser page is open yet. Open a website or search first."
        return None

    @reg.tool(
        "Click something on the current web page in Max's browser: a link, button or tab, by its "
        "visible text ('Images', 'Sign in'), by position ('first result', 'second video'), or by "
        "number from an earlier list ('#12'). Use this to open or play a result or video; it "
        "finds items below the fold itself, so don't scroll first.",
        params={"target": {"type": "string", "description": "What to click, e.g. 'Images' or 'first result'"}},
        direct=True,
    )
    def browser_click(target: str):
        if err := need_browser():
            return err
        el, candidates = pick(session.scan(), target)
        for _ in range(2):
            if el is not None:
                break
            # Page may still be drawing (single-page apps); give it a moment and look again
            session.settle(1500)
            el, candidates = pick(session.scan(), target)
        if el is None:
            if not candidates:
                return f"Error: I couldn't find '{target}' on this page."
            return ForLLM(f"Several things on the page could match '{target}'. Pick one and call "
                          f"browser_click with its number, or ask the user:\n{element_list(candidates)}")
        label = clean(el.name, 60).rstrip(".!? ")
        if RISKY_CLICK.search(el.name) and not confirm(f"This will click '{label}'. Should I?"):
            return DECLINED
        session.click(el)
        return f"Clicked {label or 'it'}."

    @reg.tool(
        "Type text into a field on the current web page (search box, form field). Optionally press "
        "Enter to submit. Never use for passwords.",
        params={
            "text": {"type": "string"},
            "field": {"type": "string", "description": "Which field, e.g. 'search' or 'email'. Empty = the main search box or focused field"},
            "submit": {"type": "boolean", "description": "Press Enter afterwards (default: yes for search boxes, no otherwise)"},
        },
        required=["text"],
        direct=True,
    )
    def browser_type(text: str, field: str = "", submit: bool | None = None):
        if err := need_browser():
            return err
        fields = [e for e in session.scan() if e.typeable]
        if not fields:
            return "Error: there's no text field on this page."
        if field:
            el, candidates = pick(fields, field)
            if el is None:
                return ForLLM(f"Which field? Call browser_type again with field set to its number:\n"
                              f"{element_list(candidates or fields)}")
        else:
            el = next((e for e in fields if e.search and e.in_view), None) or \
                 next((e for e in fields if e.in_view), fields[0])
        if el.type == "password":
            return "Error: I don't type passwords. Please enter it yourself."
        if submit is None:
            submit = el.search        # typing into a search box means "search for this"
        if submit and not el.search and not confirm(f"This will submit '{clean(text, 40)}' in the "
                                                     f"{el.name or 'form'} field. Should I?"):
            return DECLINED
        session.type_into(el, text, submit)
        return f"Typed it{' and submitted' if submit else ''}."

    @reg.tool(
        "Scroll or navigate the current web page: scroll down/up, jump to top/bottom, go back or "
        "forward, or reload.",
        params={"action": {"type": "string",
                           "enum": ["scroll_down", "scroll_up", "top", "bottom", "back", "forward", "reload"]}},
        direct=True,
    )
    def browser_navigate(action: str):
        if err := need_browser():
            return err
        page = session.page
        if action.startswith("scroll_") or action in ("top", "bottom"):
            session.scroll(action.replace("scroll_", ""))
            return {"scroll_down": "Scrolled down.", "scroll_up": "Scrolled up.",
                    "top": "At the top.", "bottom": "At the bottom."}[action]
        if action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=15000)
            return "Went back."
        if action == "forward":
            page.go_forward(wait_until="domcontentloaded", timeout=15000)
            return "Went forward."
        if action == "reload":
            page.reload(wait_until="domcontentloaded", timeout=15000)
            return "Reloaded."
        return f"Error: unknown action {action}."

    @reg.tool(
        "Control the video on the current web page in Max's browser (YouTube or any site): play, "
        "pause, mute, unmute, or restart it. Use for 'play this video', 'pause the video', 'mute it'.",
        params={"action": {"type": "string", "enum": ["play", "pause", "mute", "unmute", "restart"]}},
        direct=True,
    )
    def browser_media(action: str):
        if err := need_browser():
            return err
        state = session.media(action)
        if state is None:
            session.settle(1500)          # players can load a moment after the page
            state = session.media(action)
        if state is None:
            return "Error: there's no video on this page."
        if state.startswith("error:"):
            return f"Error: the browser blocked that ({state[6:]})."
        if action == "mute":
            return "Muted."
        return {"playing": "Playing.", "paused": "Paused.", "playing muted": "Playing, but it's muted."}.get(state, "Done.")

    @reg.tool(
        "Read the current web page in Max's browser to answer a question about it or summarize it.",
        params={"question": {"type": "string", "description": "What to look for; empty = summarize"}},
        required=[],
    )
    def browser_read(question: str = ""):
        if err := need_browser():
            return err
        import trafilatura

        page = session.page
        text = trafilatura.extract(session.html(), include_comments=False, include_tables=True) or \
            page.inner_text("body")
        if question:
            body = "\n".join(select_passages(text, question, n=4, size=350)) or clean(text, 1200)
        else:
            body = clean(text, 1500)
        return (f"Page: {clean(page.title(), 100)} ({page.url[:100]})\n{body}\n\n"
                "Answer in one or two short spoken sentences, only from this page.")

    @reg.tool(
        "Manage tabs in Max's browser: list them, switch to or close one by number, or open a new tab.",
        params={
            "action": {"type": "string", "enum": ["list", "switch", "close", "new"]},
            "number": {"type": "integer", "description": "Tab number from the list (1 = first)"},
        },
        required=["action"],
        direct=True,
    )
    def browser_tabs(action: str, number: int = 0):
        if action == "new":
            session.goto("about:blank", new_tab=True)
            return "Opened a new tab."
        if err := need_browser():
            return err
        tabs = session.tabs()
        if action == "list":
            names = [f"{i}. {clean(t.title() or t.url, 60)}" for i, t in enumerate(tabs, 1)]
            return ForLLM("Open tabs:\n" + "\n".join(names))
        if not 1 <= number <= len(tabs):
            return f"Error: there are {len(tabs)} tabs; say a number from 1 to {len(tabs)}."
        if action == "switch":
            page = session.switch(number - 1)
            return f"Switched to {clean(page.title() or 'that tab', 60)}."
        tabs[number - 1].close()
        return "Closed that tab."
