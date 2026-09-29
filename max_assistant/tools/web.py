"""Web tools: search (and, later, research and browser control)."""
from __future__ import annotations

from ..web import WebSearch, format_research, format_results
from .registry import ToolRegistry


def register(reg: ToolRegistry):
    cfg = reg.context.cfg if reg.context is not None else None
    w = cfg.get("web", {}) if cfg is not None else {}
    search = WebSearch(w.get("searxng_url", "http://127.0.0.1:8888"), w.get("results", 5),
                       w.get("timeout_s", 8.0), w.get("fallback_duckduckgo", True))
    read_pages = int(w.get("read_pages", 4))
    if reg.context is not None:
        reg.context.web = search

    @reg.tool(
        "Search the web and read the top results yourself, to answer a question out loud: current "
        "facts, news, prices, scores, weather, or anything you're unsure of. If the user wants to "
        "SEE results ('search for X', 'google X', 'show me X'), use open_website instead.",
        params={"query": {"type": "string", "description": "Search terms, e.g. 'weather in Tempe today'"}},
    )
    def web_search(query: str):
        if read_pages > 0:
            # Snippets alone often lack the answer (index pages, "winners by year" lists),
            # so read the top pages and keep the passages that match the question
            results, passages = search.research(query, pages=read_pages)
            text = format_research(query, results, passages)
        else:
            results = search.search(query)
            text = format_results(query, results)
        # The model's own knowledge is dated; keep it anchored to what the results say
        return text + (
            "\n\nAnswer ONLY from these results, in one or two short spoken sentences, and name the "
            "source site. If they don't contain the answer, say you couldn't find it; don't guess."
            if results else "")
