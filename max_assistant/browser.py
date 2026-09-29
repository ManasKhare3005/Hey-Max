"""Max's own Chrome window, driven with Playwright.

Runs your installed Chrome with a separate profile (data/browser-profile): Chrome won't let
automation attach to your everyday profile, and a separate one keeps Max's logins apart.
Sign in to sites once in Max's window and they stay signed in.

Page elements are found with a small script that tags every visible clickable/typeable
element with a number (data-max-id), so the LLM can refer to them by number when a
description is ambiguous.
"""
from __future__ import annotations

import atexit
import difflib
import logging
import re
from dataclasses import dataclass

from .config import resolve_path

log = logging.getLogger(__name__)

# Clicking these needs a spoken "yes" (they send, buy, delete or publish something)
RISKY_CLICK = re.compile(
    r"\b(buy|purchase|order|place order|checkout|check out|pay|payment|subscribe|donate|send|submit|"
    r"post|publish|reply|tweet|delete|remove|erase|unsubscribe|cancel (my )?(order|subscription|account)|"
    r"confirm|transfer|book now|reserve|sign out|log ?out|install|download)\b", re.I)

VIDEO_HREF = re.compile(r"/watch\?|/shorts/|/video/|vimeo\.com/\d|/videos/")

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10, "last": -1, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}

# Tags visible interactive elements with data-max-id and returns their descriptions
SCAN_JS = r"""
() => {
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, summary, [contenteditable=true],' +
              '[role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=radio],' +
              '[role=option], [role=switch], [role=searchbox], [role=combobox], [role=textbox]';
  document.querySelectorAll('[data-max-id]').forEach(e => e.removeAttribute('data-max-id'));
  const out = []; const H = window.innerHeight;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
    const img = el.querySelector('img[alt]');
    let name = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') ||
                el.getAttribute('title') || (img && img.alt) || '').replace(/\s+/g, ' ').trim().slice(0, 120);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : ['input', 'textarea'].includes(tag) ? 'textbox' : tag);
    if (!name && !['input', 'textarea', 'select'].includes(tag)) continue;
    const id = out.length + 1;
    el.setAttribute('data-max-id', id);
    out.push({id, name, tag, type, role, inView: r.bottom > 0 && r.top < H, top: Math.round(r.top + window.scrollY),
              left: Math.round(r.left),
              href: (el.getAttribute('href') || '').slice(0, 200),
              heading: !!el.querySelector('h1,h2,h3,h4'),
              image: !!el.querySelector('img, video') && r.width >= 60 && r.height >= 60,
              // results/articles live in main content, not the header, nav bars or footer
              main: !el.closest('header, nav, footer, [role=navigation], [role=banner], [role=contentinfo], ' +
                                '[role=search], #searchform, #top_nav, #hdtb, #foot, #masthead-container'),
              search: type === 'search' || role === 'searchbox' || role === 'combobox' ||
                      /search|query/i.test((el.getAttribute('name') || '') + ' ' + (el.getAttribute('placeholder') || '') +
                      ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')) ||
                      (el.getAttribute('name') || '') === 'q'});
    if (out.length >= 400) break;
  }
  return out;
}
"""


@dataclass
class Element:
    id: int
    name: str
    tag: str
    type: str
    role: str
    in_view: bool
    top: int
    heading: bool
    search: bool
    left: int = 0
    image: bool = False
    main: bool = True
    href: str = ""

    @property
    def typeable(self) -> bool:
        return self.tag in ("input", "textarea") or self.role in ("textbox", "searchbox", "combobox")

    def describe(self) -> str:
        kind = "text field" if self.typeable else self.role
        return f"[{self.id}] {kind}: {self.name or '(unnamed)'}"


def parse_ordinal(target: str) -> tuple[int | None, str]:
    """'the second video' -> (2, 'video'); 'Images' -> (None, 'Images'); '#12' handled elsewhere."""
    words = target.lower().replace("the ", " ").split()
    for i, w in enumerate(words):
        if w in ORDINALS:
            return ORDINALS[w], " ".join(words[:i] + words[i + 1:]).strip()
    return None, target


def score(name: str, target: str) -> float:
    """How well an element's text matches what the user said (0..1)."""
    a, b = name.lower().strip(), target.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if re.search(rf"\b{re.escape(b)}\b", a):
        return 0.9 - min(0.3, (len(a) - len(b)) / 200)          # contained as whole words; shorter is better
    if b in a:
        return 0.7
    return difflib.SequenceMatcher(None, a, b).ratio() * 0.8


def pick(elements: list[Element], target: str) -> tuple[Element | None, list[Element]]:
    """Choose the element the user means. Returns (element, []) when clear, or
    (None, candidates) when the LLM should choose from a short list."""
    t = target.strip()
    m = re.fullmatch(r"#?\s*(\d+)", t)
    if m:
        el = next((e for e in elements if e.id == int(m.group(1))), None)
        return el, []
    nth, rest = parse_ordinal(t)
    if nth is not None:
        noun = re.sub(r"\b(result|link|one|item|option)s?\b", "", rest).strip()
        links = [e for e in elements if e.role == "link" and e.main]
        pool = [e for e in links if e.heading or e.image] or [e for e in links if len(e.name) > 25] or links
        videos = [e for e in pool if VIDEO_HREF.search(e.href)]
        # On a page of videos, "the second result" means the second video, not a channel card
        if videos and ("video" in noun or (not noun.strip() and len(videos) * 2 >= len(pool))):
            pool = videos
            noun = noun.replace("videos", "").replace("video", "")
        if noun:
            named = [e for e in pool if noun.rstrip("s") in e.name.lower()]
            pool = named or pool
        pool.sort(key=lambda e: (e.top // 40, e.left))     # rows top to bottom, then left to right
        if pool and (nth == -1 or nth <= len(pool)):
            chosen = pool[nth - 1 if nth > 0 else -1]
            if chosen.image:
                # A thumbnail's text is often just a duration ("11:53"); its title link sits on
                # the same row and goes to the same place, with a name worth saying out loud
                titles = [e for e in links if not e.image and abs(e.top - chosen.top) < 30
                          and len(e.name) > max(25, len(chosen.name))]
                if titles:
                    chosen = min(titles, key=lambda e: e.left)
            return chosen, []
        return None, pool[:10]
    scored = sorted(((score(e.name, t) + (0.05 if e.in_view else 0), e) for e in elements),
                    key=lambda p: -p[0])
    if not scored or scored[0][0] < 0.45:
        return None, [e for _, e in scored[:10]]
    best_score, best = scored[0]
    close = [e for s, e in scored if s >= best_score - 0.1]
    if len({e.name.lower() for e in close}) == 1:
        return best, []          # one clear winner (maybe repeated, e.g. in a menu and a footer)
    return None, [e for _, e in scored[:8]]


class BrowserSession:
    def __init__(self, profile_dir: str = "data/browser-profile", channel: str = "chrome", headless: bool = False):
        self.profile_dir = str(resolve_path(profile_dir))
        self.channel = channel
        self.headless = headless
        self._pw = None
        self._ctx = None
        self._page = None
        self.elements: list[Element] = []
        self.extra_args: list[str] = []      # e.g. an off-screen window position for tests
        atexit.register(self.close)

    # ----- lifecycle -----
    def _launch(self):
        from playwright.sync_api import sync_playwright

        if self._pw is None:
            self._pw = sync_playwright().start()
        resolve_path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile_dir,
            channel=None if self.channel in ("", "chromium") else self.channel,
            headless=self.headless,
            no_viewport=True,
            # Without these, Chrome announces it is automated and Google shows a bot check
            ignore_default_args=["--enable-automation"],
            # Videos Max opens should play: a fresh profile has no autoplay "engagement" yet,
            # so Chrome would otherwise open YouTube paused
            args=["--disable-blink-features=AutomationControlled", "--start-maximized",
                  "--autoplay-policy=no-user-gesture-required", *self.extra_args],
        )
        self._ctx.on("close", lambda *_: self._forget())
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        log.info("browser launched (%s, profile %s)", self.channel, self.profile_dir)

    def _forget(self):
        self._ctx = None
        self._page = None

    @property
    def running(self) -> bool:
        return self._ctx is not None

    @property
    def page(self):
        """Current tab, launching Chrome (or reopening it after you closed it) if needed."""
        if self._ctx is None:
            self._launch()
        if self._page is None or self._page.is_closed():
            open_pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = open_pages[-1] if open_pages else self._ctx.new_page()
        return self._page

    def close(self):
        try:
            if self._ctx is not None:
                self._ctx.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = self._page = None

    # ----- actions -----
    def goto(self, url: str, new_tab: bool = False) -> str:
        if new_tab and self._ctx is not None:
            self._page = self._ctx.new_page()
        page = self.page
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.bring_to_front()
        self.settle(1500)
        return page.title() or url

    def scan(self) -> list[Element]:
        raw = self.page.evaluate(SCAN_JS)
        self.elements = [Element(e["id"], e["name"], e["tag"], e["type"], e["role"], e["inView"], e["top"],
                                 e["heading"], e["search"], e["left"], e["image"], e["main"], e["href"])
                         for e in raw]
        return self.elements

    def locator(self, el: Element):
        return self.page.locator(f'[data-max-id="{el.id}"]').first

    def click(self, el: Element):
        loc = self.locator(el)
        loc.scroll_into_view_if_needed(timeout=5000)
        with_nav = el.role == "link"
        try:
            loc.click(timeout=2500)
        except Exception as exc:
            # Sticky headers/overlays can cover the element; a DOM click still works
            log.info("normal click failed (%s); using a direct click", str(exc).splitlines()[0])
            loc.evaluate("e => e.click()")
        if with_nav:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
        # Links opening a new tab: follow them
        if self._ctx is not None and self._ctx.pages and self._ctx.pages[-1] is not self._page:
            newest = self._ctx.pages[-1]
            if not newest.is_closed() and newest.url != "about:blank":
                self._page = newest
                newest.bring_to_front()

    def type_into(self, el: Element | None, text: str, submit: bool):
        page = self.page
        if el is not None:
            loc = self.locator(el)
            loc.click(timeout=5000)
            loc.fill(text, timeout=5000)
        else:
            page.keyboard.type(text, delay=10)
        if submit:
            page.keyboard.press("Enter")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            self.settle()

    def settle(self, timeout_ms: int = 3000):
        """Wait briefly for single-page apps (YouTube, Gmail) to finish drawing after an action."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    def focused_is_password(self) -> bool:
        return bool(self.page.evaluate("() => (document.activeElement?.type || '').toLowerCase() === 'password'"))

    def scroll(self, how: str):
        js = {"down": "window.scrollBy(0, window.innerHeight * 0.8)",
              "up": "window.scrollBy(0, -window.innerHeight * 0.8)",
              "top": "window.scrollTo(0, 0)",
              "bottom": "window.scrollTo(0, document.body.scrollHeight)"}[how]
        self.page.evaluate(js)

    def media(self, action: str) -> str | None:
        """Play/pause/mute/unmute/restart the main video on the page (also inside embedded
        players' iframes). Returns the new state, or None if there's no video."""
        js = """async (action) => {
          const vids = [...document.querySelectorAll('video')];
          if (!vids.length) return null;
          const area = v => { const r = v.getBoundingClientRect(); return r.width * r.height; };
          const v = vids.sort((a, b) => area(b) - area(a))[0];
          try {
            if (action === 'play') await v.play();
            else if (action === 'pause') v.pause();
            else if (action === 'mute') v.muted = true;
            else if (action === 'unmute') v.muted = false;
            else if (action === 'restart') { v.currentTime = 0; await v.play(); }
          } catch (e) { return 'error:' + e.name; }
          return v.paused ? 'paused' : (v.muted ? 'playing muted' : 'playing');
        }"""
        for frame in [self.page.main_frame, *[f for f in self.page.frames if f is not self.page.main_frame]]:
            try:
                state = frame.evaluate(js, action)
            except Exception:
                continue
            if state is not None:
                return state
        return None

    def html(self) -> str:
        return self.page.content()

    def tabs(self) -> list:
        return [p for p in self._ctx.pages if not p.is_closed()] if self._ctx else []

    def switch(self, index: int):
        tabs = self.tabs()
        self._page = tabs[index]
        self._page.bring_to_front()
        return self._page
