"""Reminders: stored in the memory database, fired by a background scheduler.

When one is due, Max says it out loud and shows a Windows notification. Reminders that
came due while Max wasn't running are announced at the next start ("While you were away"),
except ones the phone has a copy of: the phone already showed those, so Max stays quiet.

Every reminder has a `uid` (shared with the phone, which keeps its own copy and fires it even
when the laptop is off) and an `updated` time; sync.py merges the two sides.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_HOUR = 9          # "remind me Friday" with no time -> 9 AM

# Phrases dateparser doesn't understand, rewritten into ones it does
_REWRITES = [
    (r"\bnext\s+(mon|tues|wednes|thurs|fri|satur|sun)day\b", r"\1day"),
    (r"\btonight\s+at\s+(\d{1,2})(?::(\d{2}))?\b(?!\s*[ap]\.?m)", lambda m: f"today at {m.group(1)}:{m.group(2) or '00'} pm"),
    (r"\btonight\b", "today at 8 pm"),
    (r"\bthis\s+(morning|afternoon|evening)\b", r"today \1"),
    (r"\bmorning\b", "9 am"),
    (r"\bafternoon\b", "2 pm"),
    (r"\bevening\b", "6 pm"),
    (r"\bnight\b", "9 pm"),
]
_HAS_TIME = re.compile(r"\d\s*(am|pm|a\.m|p\.m)|\d:\d\d|\bnoon\b|\bmidnight\b|\bin\s+\d+\s*(min|hour|sec)|o'?clock", re.I)


def has_time(text: str) -> bool:
    """Does the phrase name a time of day (not just a day)?"""
    phrase = text
    for pattern, repl in _REWRITES:
        phrase = re.sub(pattern, repl, phrase, flags=re.I)
    return bool(_HAS_TIME.search(phrase)) or bool(re.search(r"T\d\d:\d\d", phrase))


def new_uid() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


def parse_when(text: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """'Friday at 9am', 'tomorrow morning', 'in 2 hours', '2026-10-02T09:00' -> datetime."""
    import dateparser

    now = now or dt.datetime.now()
    phrase = text.strip()
    for pattern, repl in _REWRITES:
        phrase = re.sub(pattern, repl, phrase, flags=re.I)
    when = dateparser.parse(phrase, settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now,
                                              "RETURN_AS_TIMEZONE_AWARE": False})
    if when is None:
        return None
    if not _HAS_TIME.search(phrase) and "T" not in phrase:
        when = when.replace(hour=DEFAULT_HOUR, minute=0, second=0, microsecond=0)
    if when <= now and when.date() == now.date() and not _HAS_TIME.search(phrase):
        when += dt.timedelta(days=1)
    return when.replace(microsecond=0)


def spoken_time(when: dt.datetime, now: dt.datetime | None = None) -> str:
    """'today at 6 PM', 'tomorrow at 9 AM', 'Friday, October 2 at 9:30 AM'."""
    now = now or dt.datetime.now()
    clock = f"{when.hour % 12 or 12}{when.strftime(':%M') if when.minute else ''} {'AM' if when.hour < 12 else 'PM'}"
    days = (when.date() - now.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{when.strftime('%A, %B')} {when.day} at {clock}"


@dataclass
class Reminder:
    id: int
    text: str
    due: dt.datetime
    status: str = "pending"
    uid: str = ""
    updated: int = 0              # ms since the epoch, for sync
    on_phone: bool = False        # the phone has a copy (and fires it itself)

    def to_sync(self) -> dict:
        return {"uid": self.uid, "text": self.text, "due_ms": int(self.due.timestamp() * 1000),
                "status": self.status, "updated_ms": self.updated}


def migrate(db):
    """Older databases: add uid / updated / on_phone to reminders."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(reminders)")}
    if "uid" not in cols:
        db.execute("ALTER TABLE reminders ADD COLUMN uid TEXT")
    if "updated" not in cols:
        db.execute("ALTER TABLE reminders ADD COLUMN updated INTEGER NOT NULL DEFAULT 0")
    if "on_phone" not in cols:
        db.execute("ALTER TABLE reminders ADD COLUMN on_phone INTEGER NOT NULL DEFAULT 0")
    for (rid,) in db.execute("SELECT id FROM reminders WHERE uid IS NULL OR uid = ''").fetchall():
        db.execute("UPDATE reminders SET uid = ?, updated = ? WHERE id = ?", (new_uid(), now_ms(), rid))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS reminders_uid ON reminders(uid)")
    db.commit()


class Reminders:
    def __init__(self, store):
        self.store = store            # MemoryStore (shares its database and lock)
        with store.lock:
            migrate(store.db)

    def add(self, text: str, due: dt.datetime, uid: str = "") -> Reminder:
        uid, updated = uid or new_uid(), now_ms()
        due = due.replace(microsecond=0)
        with self.store.lock:
            cur = self.store.db.execute(
                "INSERT INTO reminders (text, due, created, uid, updated) VALUES (?, ?, ?, ?, ?)",
                (text.strip(), due.isoformat(timespec="seconds"),
                 dt.datetime.now().isoformat(timespec="seconds"), uid, updated))
            self.store.db.commit()
            return Reminder(cur.lastrowid, text.strip(), due, uid=uid, updated=updated)

    def _rows(self, where: str, args=()) -> list[Reminder]:
        with self.store.lock:
            rows = self.store.db.execute(
                f"SELECT id, text, due, status, uid, updated, on_phone FROM reminders WHERE {where} ORDER BY due",
                args).fetchall()
        return [Reminder(r["id"], r["text"], dt.datetime.fromisoformat(r["due"]), r["status"], r["uid"],
                         r["updated"], bool(r["on_phone"])) for r in rows]

    def by_uid(self, uid: str) -> Reminder | None:
        rows = self._rows("uid = ?", (uid,))
        return rows[0] if rows else None

    def for_sync(self, days: int = 14) -> list[Reminder]:
        """Pending ones, and any that changed lately (so the phone hears about done / cancelled)."""
        since = int((dt.datetime.now() - dt.timedelta(days=days)).timestamp() * 1000)
        return self._rows("status = 'pending' OR updated >= ?", (since,))

    def apply_sync(self, item: dict) -> Reminder | None:
        """A reminder from the phone: insert it, or update ours if theirs wins (sync.wins)."""
        from .sync import wins

        uid = str(item.get("uid") or "")
        if not uid or not str(item.get("text") or "").strip():
            return None
        due = dt.datetime.fromtimestamp(int(item["due_ms"]) / 1000).replace(microsecond=0)
        status = item.get("status") if item.get("status") in ("pending", "done", "cancelled") else "pending"
        updated = int(item.get("updated_ms") or now_ms())
        ours = self.by_uid(uid)
        with self.store.lock:
            if ours is None:
                self.store.db.execute(
                    "INSERT INTO reminders (text, due, created, status, uid, updated, on_phone) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (str(item["text"]).strip(), due.isoformat(timespec="seconds"),
                     dt.datetime.now().isoformat(timespec="seconds"), status, uid, updated))
            elif wins({**item, "status": status, "updated_ms": updated}, ours.to_sync()):
                updated = max(updated, ours.updated)      # never older than what we had (phone clocks drift)
                self.store.db.execute(
                    "UPDATE reminders SET text = ?, due = ?, status = ?, updated = ?, on_phone = 1 WHERE uid = ?",
                    (str(item["text"]).strip(), due.isoformat(timespec="seconds"), status, updated, uid))
            else:
                self.store.db.execute("UPDATE reminders SET on_phone = 1 WHERE uid = ?", (uid,))
            self.store.db.commit()
        return self.by_uid(uid)

    def mark_on_phone(self, uids: list[str]):
        with self.store.lock:
            self.store.db.executemany("UPDATE reminders SET on_phone = 1 WHERE uid = ?", [(u,) for u in uids])
            self.store.db.commit()

    def pending(self) -> list[Reminder]:
        return self._rows("status = 'pending'")

    def due(self, now: dt.datetime | None = None) -> list[Reminder]:
        now = now or dt.datetime.now()
        return self._rows("status = 'pending' AND due <= ?", (now.isoformat(timespec="seconds"),))

    def set_status(self, rid: int, status: str):
        with self.store.lock:
            self.store.db.execute("UPDATE reminders SET status = ?, fired = ?, updated = ? WHERE id = ?",
                                  (status, dt.datetime.now().isoformat(timespec="seconds"), now_ms(), rid))
            self.store.db.commit()

    def find(self, description: str) -> Reminder | None:
        """Best pending match for 'the exam reminder' (by meaning, via the embedder)."""
        items = self.pending()
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        vecs = self.store.embedder.embed([description] + [r.text for r in items])
        scores = vecs[1:] @ vecs[0]
        return items[int(scores.argmax())]


class ReminderScheduler:
    """Checks for due reminders every few seconds and hands them to `fire`."""

    def __init__(self, reminders: Reminders, fire: Callable[[Reminder, bool], None], interval_s: float = 10.0,
                 missed_after_s: float = 300.0):
        self.reminders = reminders
        self.fire = fire               # fire(reminder, missed)
        self.interval_s = interval_s
        self.missed_after_s = missed_after_s
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="reminders", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def check(self, now: dt.datetime | None = None):
        now = now or dt.datetime.now()
        for r in self.reminders.due(now):
            missed = (now - r.due).total_seconds() > self.missed_after_s
            if missed and r.on_phone:          # the phone showed it while Max was away: stay quiet
                self.reminders.set_status(r.id, "done")
                continue
            try:
                self.fire(r, missed)
            except Exception as exc:
                log.warning("reminder %s failed to fire: %s", r.id, exc)
            self.reminders.set_status(r.id, "done")

    def _run(self):
        while not self._stop.is_set():
            self.check()
            self._stop.wait(self.interval_s)


def toast(title: str, message: str):
    """Windows notification; silently skipped elsewhere or if it fails."""
    try:
        from winotify import Notification, audio

        n = Notification(app_id="Max", title=title, msg=message, duration="long")
        n.set_audio(audio.Reminder, loop=False)
        n.show()
    except Exception as exc:
        log.debug("toast failed: %s", exc)
