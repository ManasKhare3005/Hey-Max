"""Send the model only the tools a request needs.

With 49 tools the definitions alone took ~5,200 tokens, more than the model's whole 4,096-token
window (system prompt and conversation included), so Ollama refused requests. Doubling the
window would not fit the 4 GB GPU. Instead each request gets:

- a small core that is always there (open/search the web, open apps, the time),
- the tool families that match the request by meaning (bge-small embeddings, the same model
  memory uses) or by clear keywords ("my phone", "remind", "lecture slides"...),
- the families used in the last two turns, so follow-ups ("click the second one") still work.

A family travels together (all browser tools, all phone tools), because one step often leads
to the next.
"""
from __future__ import annotations

import re

import numpy as np

CORE = ["open_website", "web_search", "open_app", "get_datetime"]

FAMILIES: dict[str, list[str]] = {
    "browser": ["browser_click", "browser_type", "browser_navigate", "browser_media", "browser_read", "browser_tabs"],
    "phone": ["phone_open_app", "phone_call", "phone_message", "phone_alarm", "phone_timer", "phone_navigate",
              "phone_play", "phone_email", "phone_notifications", "phone_status"],
    "memory": ["remember", "recall", "forget"],
    "reminders": ["set_reminder", "list_reminders", "cancel_reminder"],
    "events": ["add_event", "list_events", "cancel_event"],
    "canvas": ["canvas_due", "canvas_classes", "day_summary", "email_digest"],
    "notes": ["start_notes", "stop_notes", "meeting_notes"],
    "quick_notes": ["take_note", "read_notes"],
    "course": ["course_search", "course_files", "summarize_course_files"],
    "files": ["find_files", "open_file"],
    "system": ["close_app", "media_control", "volume", "system_status", "screenshot", "lock_screen", "power",
               "cancel_shutdown", "free_gpu"],
    "screen": ["read_screen"],
    "mail": ["mail_check", "mail_search", "mail_read", "mail_reply", "mail_send", "mail_archive", "mail_mark_read"],
}

KEYWORDS: dict[str, re.Pattern] = {
    "phone": re.compile(r"\b(my phone|on (the |my )?phone|phone'?s|text (him|her|them|mom|dad)|whatsapp|call\s+\w+|alarm|timer|"
                        r"wake me|notifications?|what did i miss|navigate|directions|take me to|battery|spotify|instagram|"
                        r"email \w+)\b", re.I),
    "memory": re.compile(r"\b(remember|forget|what do you know about|do you remember|my (name|birthday|sister|brother|mom|dad))\b", re.I),
    "reminders": re.compile(r"\bremind|reminders?\b", re.I),
    "events": re.compile(r"\b(calendar|events?|appointments?|what'?s on|am i (free|busy)|plans? (for|on)|"
                         r"anything (on|planned))\b", re.I),
    "canvas": re.compile(r"\b(due|assignments?|homework|canvas|classes?|quiz|exam|deadline|my day|schedule|digest|summary|lab \d)\b", re.I),
    "notes": re.compile(r"\b(take notes|notes (on|for|of)|record (this|the)|lecture notes|meeting notes|stop (taking )?notes|"
                        r"key points|summar\w+ (of )?(the|my|today'?s) (lecture|meeting|class))\b", re.I),
    "quick_notes": re.compile(r"\b(take a note|note that|jot|my notes|read (my |the )?notes)\b", re.I),
    "course": re.compile(r"\b(professor|lecture|slides?|handout|reading|course (material|files|folder)|textbook|midterm|final|"
                         r"pdfs?|summari[sz]e (each|every|all|them|these|those)|notes (of|on|for) (each|every|all|them)|"
                         r"study guide|explain .* (from|in) (the )?(lab|class|lecture))\b", re.I),
    "files": re.compile(r"\b(find|open|where is) (the |my |a )?(file|pdf|document|report|docx|pptx|download|resume|cv|essay)", re.I),
    "system": re.compile(r"\b(volume|louder|quieter|mute|pause|next song|skip|shut ?down|restart|lock|sleep|close|"
                         r"screenshot|cpu|memory usage|free (the )?gpu|go to sleep)\b", re.I),
    "browser": re.compile(r"\b(click|tab|scroll|type|fill|page|video|play (the|it|that)|first (one|result)|second|third|"
                          r"go back|read (the|this) page)\b", re.I),
    "mail": re.compile(r"\b(e-?mails?|mail|inbox|gmail|unread|reply|replied|archive|mark (it|them|that) (as )?read)\b", re.I),
    "screen": re.compile(r"\b(on my screen|this (error|window|page|screen)|what am i looking at|read (my|the) screen)\b", re.I),
}


class ToolSelector:
    def __init__(self, registry, embedder=None, top_families: int = 3, min_score: float = 0.55, max_tools: int = 24):
        self.registry = registry
        self.embedder = embedder
        self.top_families = top_families
        self.min_score = min_score
        self.max_tools = max_tools          # ~3k tokens at most: room left for the conversation
        self._family_of = {t: f for f, ts in FAMILIES.items() for t in ts}
        self._names: list[str] = []
        self._vecs = None

    def _ensure_vectors(self):
        names = list(self.registry.tools)
        if self._vecs is not None and names == self._names:
            return
        self._names = names
        if self.embedder is None:
            self._vecs = None
            return
        texts = [f"{n.replace('_', ' ')}: {self.registry.tools[n].description}" for n in names]
        self._vecs = np.asarray(self.embedder.embed(texts), dtype=np.float32)

    def family(self, tool: str) -> str:
        return self._family_of.get(tool, tool)

    def members(self, family: str) -> list[str]:
        return [t for t in FAMILIES.get(family, [family]) if t in self.registry.tools]

    def select(self, user_text: str, recent_tools: list[str] = ()) -> list[str]:
        """Tool names for this request (always a subset of the registry)."""
        available = set(self.registry.tools)
        families: list[str] = []
        families += [f for f, pat in KEYWORDS.items() if pat.search(user_text)]
        families += [self.family(t) for t in recent_tools]
        self._ensure_vectors()
        if self._vecs is not None and len(self._names):
            q = np.asarray(self.embedder.embed([user_text])[0], dtype=np.float32)
            scores = self._vecs @ q
            best: dict[str, float] = {}
            for name, s in zip(self._names, scores):
                fam = self.family(name)
                best[fam] = max(best.get(fam, -1.0), float(s))
            ranked = sorted(best.items(), key=lambda kv: -kv[1])
            families += [f for f, s in ranked[: self.top_families] if s >= self.min_score]
        chosen: list[str] = [t for t in CORE if t in available]
        for fam in dict.fromkeys(families):           # keep order (keywords, recent, meaning), drop repeats
            new = [t for t in self.members(fam) if t not in chosen]
            if chosen and len(chosen) + len(new) > self.max_tools:
                continue                              # a whole family or none: half a family confuses the model
            chosen += new
        return chosen
