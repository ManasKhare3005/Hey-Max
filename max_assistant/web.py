"""Web search: self-hosted SearXNG first, DuckDuckGo (ddgs) as a fallback when it's down."""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    @property
    def domain(self) -> str:
        return re.sub(r"^www\.", "", urlparse(self.url).netloc)


def clean(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def format_results(query: str, results: list[SearchResult], snippet_chars: int = 220) -> str:
    """Compact, numbered list for the LLM (small context window, so snippets are trimmed)."""
    if not results:
        return f"No web results found for '{query}'."
    lines = [f"Web results for '{query}':"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {clean(r.title, 100)} ({r.domain}): {clean(r.snippet, snippet_chars)}")
    return "\n".join(lines)


STOPWORDS = set("""a an and are as at be by do does for from how i in is it latest me most my now of on
or recent right show tell that the this to today was what when where which who why will with you""".split())
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.8"}


def keywords(query: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9$%.]+", query.lower()) if w not in STOPWORDS and len(w) > 1]


RECENCY = re.compile(r"\b(latest|recent|current|currently|last|newest|now|today|this (year|week|month)|so far)\b", re.I)
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")


def wants_recent(query: str) -> bool:
    return bool(RECENCY.search(query))


def select_passages(text: str, query: str, n: int = 2, size: int = 360, recent: bool | None = None,
                    this_year: int | None = None) -> list[str]:
    """Pick the n chunks of a page that best match the query. Chunks are ~size characters,
    cut on sentence boundaries; ties go to the earlier chunk (pages lead with key facts).
    For 'latest/most recent' questions, chunks mentioning the page's newest year win, which
    finds the last rows of a long table (e.g. a list of winners by year)."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", re.sub(r"[ \t]+", " ", text or ""))
    chunks, cur = [], ""
    for s in (s.strip() for s in sentences):
        if not s:
            continue
        if cur and len(cur) + len(s) > size:
            chunks.append(cur)
            cur = ""
        cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    words = keywords(query)
    if not chunks:
        return []
    recent = wants_recent(query) if recent is None else recent
    this_year = this_year or dt.date.today().year
    years = [{int(y) for y in YEAR.findall(c) if int(y) <= this_year} for c in chunks]
    newest = max((max(ys) for ys in years if ys), default=None)

    def score(i: int) -> float:
        low = chunks[i].lower()
        s = sum(1 for w in set(words) if w in low)
        s += 0.3 * bool(re.search(r"\d", chunks[i]))            # facts usually carry numbers/dates
        if recent and newest and years[i]:
            s += 3 if newest in years[i] else 1.5 if newest - 1 in years[i] else 0
        return s

    if not words and not recent:
        return [clean(c, size + 40) for c in chunks[:n]]
    ranked = sorted(range(len(chunks)), key=lambda i: (-score(i), i))
    best = [i for i in ranked[:n] if score(i) > 0]
    return [clean(chunks[i], size + 40) for i in sorted(best)]


def fetch_text(url: str, timeout: float = 5.0, max_bytes: int = 2_000_000) -> str:
    """Download a page and extract its main text (no menus/ads). Empty string on failure."""
    import trafilatura

    try:
        r = requests.get(url, headers=UA, timeout=timeout, stream=True)
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html"):
            return ""
        raw = r.raw.read(max_bytes, decode_content=True)
        html = raw.decode(r.encoding or "utf-8", errors="replace")
        return trafilatura.extract(html, include_comments=False, include_tables=True, favor_precision=True) or ""
    except Exception as exc:   # network errors, odd encodings, parser failures: skip the page
        log.debug("fetch failed for %s: %s", url, exc)
        return ""


def format_research(query: str, results: list[SearchResult], passages: dict[str, list[str]],
                    snippet_chars: int = 160) -> str:
    if not results:
        return f"No web results found for '{query}'."
    lines = [f"Web results for '{query}':"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {clean(r.title, 90)} ({r.domain}): {clean(r.snippet, snippet_chars)}")
        for p in passages.get(r.url, []):
            lines.append(f"   > {p}")
    return "\n".join(lines)


class WebSearch:
    def __init__(self, searxng_url: str | None = "http://127.0.0.1:8888", results: int = 5,
                 timeout: float = 8.0, fallback: bool = True):
        self.searxng_url = (searxng_url or "").rstrip("/")
        self.results = results
        self.timeout = timeout
        self.fallback = fallback
        self.last_backend = ""

    def search(self, query: str, n: int | None = None) -> list[SearchResult]:
        n = n or self.results
        if self.searxng_url:
            try:
                hits = self._searxng(query, n)
                if hits:
                    self.last_backend = "searxng"
                    return hits
                log.info("SearXNG returned no results for %r", query)
            except (requests.RequestException, ValueError) as exc:
                log.warning("SearXNG unavailable (%s)%s", exc, "; using DuckDuckGo" if self.fallback else "")
        if not self.fallback:
            return []
        try:
            hits = self._duckduckgo(query, n)
            self.last_backend = "duckduckgo"
            return hits
        except Exception as exc:
            log.warning("DuckDuckGo search failed: %s", exc)
            return []

    def research(self, query: str, pages: int = 4, passages_per_page: int = 2,
                 fetch_timeout: float = 5.0) -> tuple[list[SearchResult], dict[str, list[str]]]:
        """Search, then read the top pages in parallel and keep the passages matching the query."""
        from concurrent.futures import ThreadPoolExecutor

        results = self.search(query)
        targets = [r for r in results[:pages] if r.url.startswith("http")]
        found: dict[str, list[str]] = {}
        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                texts = list(pool.map(lambda r: fetch_text(r.url, fetch_timeout), targets))
            for r, text in zip(targets, texts):
                picked = select_passages(text, query, passages_per_page)
                if picked:
                    found[r.url] = picked
        return results, found

    def _searxng(self, query: str, n: int) -> list[SearchResult]:
        r = requests.get(f"{self.searxng_url}/search", params={"q": query, "format": "json"},
                         timeout=self.timeout)
        r.raise_for_status()
        seen, out = set(), []
        for item in r.json().get("results", []):
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(SearchResult(item.get("title") or url, url, item.get("content") or ""))
            if len(out) >= n:
                break
        return out

    def _duckduckgo(self, query: str, n: int) -> list[SearchResult]:
        from ddgs import DDGS

        return [SearchResult(h.get("title") or h.get("href", ""), h.get("href", ""), h.get("body") or "")
                for h in DDGS().text(query, max_results=n) if h.get("href")]
