"""Canvas (ASU) through the read-only calendar feed: assignments, quizzes and class sessions.

The feed link comes from Canvas > Calendar > Calendar Feed and lives in secrets.yaml (it
works like a password). ASU doesn't allow personal API tokens, so grades and announcements
aren't available; the feed covers everything with a date.

Feed quirks handled here:
- UID "event-assignment-N" = assignment/quiz; "event-calendar-event-N" = class session.
- Assignments come as date-only (no time): treated as due by the end of that day.
- Cross-listed courses repeat each session once per section, labelled "(2024 Spring)",
  "(2026 Fall C)"...; only the section matching the course's current term is kept.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

CONTEXT = re.compile(r"\s*\[(\d{4})(Spring|Summer|Fall)([A-Z]?)-[^\]]*?-([A-Z]{2,4})(\d{3})[^\]]*\]\s*$")
SECTION = re.compile(r"\((\d{4}) (Spring|Summer|Fall)\s*([A-Z]?)\)")


@dataclass
class CanvasItem:
    kind: str                  # "assignment" or "class"
    title: str
    course: str                # e.g. "CSE 572"
    start: dt.datetime         # local time; for assignments, the end of the due day
    all_day: bool
    url: str = ""
    link: str = ""             # e.g. the Zoom link for a class session

    @property
    def when(self) -> str:
        if self.all_day:
            return "by end of day"
        return f"at {self.start.hour % 12 or 12}{self.start.strftime(':%M') if self.start.minute else ''} " \
               f"{'AM' if self.start.hour < 12 else 'PM'}"


def _local(value) -> tuple[dt.datetime, bool]:
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone().replace(tzinfo=None)
        return value, False
    return dt.datetime.combine(value, dt.time(23, 59)), True       # date-only: due that day


def parse_feed(data: bytes) -> list[CanvasItem]:
    import icalendar

    items = []
    for e in icalendar.Calendar.from_ical(data).walk("VEVENT"):
        uid = str(e.get("UID", ""))
        summary = str(e.get("SUMMARY", "")).strip()
        m = CONTEXT.search(summary)
        course, term = "", None
        if m:
            year, season, part, dept, num = m.groups()
            course, term = f"{dept} {num}", (year, season, part)
            summary = summary[: m.start()].strip()
        start, all_day = _local(e.get("DTSTART").dt)
        if uid.startswith("event-assignment"):
            kind = "assignment"
        else:
            kind = "class"
            section = SECTION.search(summary)
            if section and term and section.groups() != term:
                continue                        # another section's copy of the same session
            summary = SECTION.sub("", summary).strip(" :-")
            if course and summary.upper().startswith(course):
                summary = summary[len(course):].strip(" :-") or summary   # "CSE 573: Semantic..." -> "Semantic..."
        desc = str(e.get("DESCRIPTION") or "")
        link = next(iter(re.findall(r"https://\S*zoom\.us/\S+?(?=[)\s]|$)", desc)), "")
        items.append(CanvasItem(kind, summary, course, start, all_day, str(e.get("URL") or ""), link))
    # the same class session can still appear twice (different course shells): keep one
    seen, unique = set(), []
    for it in sorted(items, key=lambda i: i.start):
        key = (it.kind, it.course, it.title.lower(), it.start)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique


class CanvasFeed:
    def __init__(self, url: str, cache_s: float = 900, timeout: float = 20):
        self.url = url
        self.cache_s = cache_s
        self.timeout = timeout
        self._items: list[CanvasItem] | None = None
        self._at = 0.0
        self._lock = threading.Lock()
        self.last_error = ""

    def items(self) -> list[CanvasItem]:
        """Cached for 15 minutes; if Canvas is down (e.g. maintenance), the last copy is used."""
        with self._lock:
            if self._items is None or time.monotonic() - self._at > self.cache_s:
                try:
                    r = requests.get(self.url, timeout=self.timeout)
                    r.raise_for_status()
                    self._items = parse_feed(r.content)
                    self._at = time.monotonic()
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = str(exc)[:200]
                    log.warning("Canvas feed unavailable: %s", exc)
                    if self._items is None:
                        raise
            return list(self._items)

    def between(self, start: dt.datetime, end: dt.datetime, kind: str | None = None) -> list[CanvasItem]:
        return [i for i in self.items() if start <= i.start < end and (kind is None or i.kind == kind)]


PERIODS = ("today", "tomorrow", "this_week", "next_7_days")


def period_range(period: str, now: dt.datetime | None = None) -> tuple[dt.datetime, dt.datetime]:
    now = now or dt.datetime.now()
    today = dt.datetime.combine(now.date(), dt.time())
    if period == "tomorrow":
        return today + dt.timedelta(days=1), today + dt.timedelta(days=2)
    if period == "this_week":                     # through Sunday
        return today, today + dt.timedelta(days=7 - today.weekday())
    if period == "next_7_days":
        return today, today + dt.timedelta(days=7)
    return today, today + dt.timedelta(days=1)


def describe_due(items: list[CanvasItem], period: str, now: dt.datetime | None = None) -> str:
    label = {"next_7_days": "in the next 7 days"}.get(period, period.replace("_", " "))
    if not items:
        return f"Nothing due {label} on Canvas."
    now = now or dt.datetime.now()
    parts = []
    for it in items[:6]:
        day = "" if period in ("today", "tomorrow") else f"{it.start.strftime('%A')} "
        parts.append(f"{it.title} for {it.course or 'a course'}, {day}{it.when}".replace("  ", " "))
    more = f", and {len(items) - 6} more" if len(items) > 6 else ""
    return f"Due {label}: " + "; ".join(parts) + more + "."
