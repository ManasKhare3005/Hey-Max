"""Events: Max's own calendar ("add a study group tomorrow at 3pm", "what's on Friday?").

Stored in the memory database and synced to the phone (sync.py), which shows them in a local
"Max" calendar on the phone, so they're there with the laptop off and events added on the
phone offline reach the laptop later. Events without a time of day are all-day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .reminders import new_uid, now_ms


@dataclass
class Event:
    uid: str
    title: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool = False
    location: str = ""
    status: str = "active"         # or "cancelled"
    updated: int = 0

    def to_sync(self) -> dict:
        return {"uid": self.uid, "title": self.title, "start_ms": int(self.start.timestamp() * 1000),
                "end_ms": int(self.end.timestamp() * 1000), "all_day": self.all_day, "location": self.location,
                "status": self.status, "updated_ms": self.updated}

    def spoken(self, now: dt.datetime | None = None) -> str:
        from .reminders import spoken_time

        if self.all_day:
            days = (self.start.date() - (now or dt.datetime.now()).date()).days
            day = "today" if days == 0 else "tomorrow" if days == 1 else f"{self.start:%A, %B} {self.start.day}"
            return f"{self.title}, {day} (all day)"
        where = f" at {self.location}" if self.location else ""
        return f"{self.title}, {spoken_time(self.start, now)}{where}"


class Events:
    def __init__(self, store):
        self.store = store                 # MemoryStore (its schema has the events table)

    def _rows(self, where: str, args=()) -> list[Event]:
        with self.store.lock:
            rows = self.store.db.execute(f"SELECT * FROM events WHERE {where} ORDER BY start", args).fetchall()
        return [Event(r["uid"], r["title"], dt.datetime.fromisoformat(r["start"]), dt.datetime.fromisoformat(r["end"]),
                      bool(r["all_day"]), r["location"], r["status"], r["updated"]) for r in rows]

    def _write(self, e: Event):
        with self.store.lock:
            self.store.db.execute(
                "INSERT INTO events (uid, title, start, end, all_day, location, status, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET title = excluded.title, "
                "start = excluded.start, end = excluded.end, all_day = excluded.all_day, "
                "location = excluded.location, status = excluded.status, updated = excluded.updated",
                (e.uid, e.title, e.start.isoformat(timespec="seconds"), e.end.isoformat(timespec="seconds"),
                 int(e.all_day), e.location, e.status, dt.datetime.now().isoformat(timespec="seconds"), e.updated))
            self.store.db.commit()

    def add(self, title: str, start: dt.datetime, minutes: int = 60, location: str = "", all_day: bool = False) -> Event:
        start = start.replace(microsecond=0)
        if all_day:
            start = start.replace(hour=0, minute=0, second=0)
            end = start + dt.timedelta(days=1)
        else:
            end = start + dt.timedelta(minutes=max(5, int(minutes or 60)))
        e = Event(new_uid(), title.strip(), start, end, all_day, location.strip(), "active", now_ms())
        self._write(e)
        return e

    def by_uid(self, uid: str) -> Event | None:
        rows = self._rows("uid = ?", (uid,))
        return rows[0] if rows else None

    def cancel(self, uid: str) -> bool:
        e = self.by_uid(uid)
        if e is None:
            return False
        e.status, e.updated = "cancelled", now_ms()
        self._write(e)
        return True

    def between(self, start: dt.datetime, end: dt.datetime) -> list[Event]:
        """Active events overlapping [start, end)."""
        return self._rows("status = 'active' AND start < ? AND end > ?",
                          (end.isoformat(timespec="seconds"), start.isoformat(timespec="seconds")))

    def on(self, day: dt.date) -> list[Event]:
        start = dt.datetime.combine(day, dt.time())
        return self.between(start, start + dt.timedelta(days=1))

    def upcoming(self, days: int = 14) -> list[Event]:
        now = dt.datetime.now()
        return self.between(now, now + dt.timedelta(days=days))

    def find(self, description: str) -> Event | None:
        """Best upcoming match for 'the dentist one' (by meaning)."""
        items = self.upcoming(60)
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        vecs = self.store.embedder.embed([description] + [e.title for e in items])
        return items[int((vecs[1:] @ vecs[0]).argmax())]

    def for_sync(self, past_days: int = 30) -> list[Event]:
        """What the phone's calendar should hold: everything from a month ago on, cancelled ones
        that changed lately too (so the phone removes them)."""
        since = dt.datetime.now() - dt.timedelta(days=past_days)
        recent = int(since.timestamp() * 1000)
        return self._rows("(status = 'active' AND end >= ?) OR updated >= ?", (since.isoformat(timespec="seconds"), recent))

    def apply_sync(self, item: dict) -> Event | None:
        from .sync import wins

        uid = str(item.get("uid") or "")
        if not uid or not str(item.get("title") or "").strip():
            return None
        status = "cancelled" if item.get("status") == "cancelled" else "active"
        incoming = Event(uid, str(item["title"]).strip(),
                         dt.datetime.fromtimestamp(int(item["start_ms"]) / 1000).replace(microsecond=0),
                         dt.datetime.fromtimestamp(int(item["end_ms"]) / 1000).replace(microsecond=0),
                         bool(item.get("all_day")), str(item.get("location") or ""), status,
                         int(item.get("updated_ms") or now_ms()))
        ours = self.by_uid(uid)
        # "cancelled" counts as finished for the merge rule (sync.wins)
        as_sync = lambda e: {"status": "cancelled" if e.status == "cancelled" else "pending", "updated_ms": e.updated}
        if ours is None or wins(as_sync(incoming), as_sync(ours)):
            if ours is not None:
                incoming.updated = max(incoming.updated, ours.updated)   # never older than what we had
            self._write(incoming)
        return self.by_uid(uid)
