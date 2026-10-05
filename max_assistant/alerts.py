"""Deadline countdown: phone notifications before Canvas deadlines and classes.

Every minute: anything due within 24 h or 3 h, and any class starting within 10 minutes,
becomes an `alert` event that the phone app shows as a notification (tap opens the Zoom or
Canvas link). Each alert is sent once (remembered in the database); if the laptop was off and
an item is already inside the 3-hour window, only the 3-hour alert goes out.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading

log = logging.getLogger(__name__)


def _key(item) -> str:
    return f"{item.kind}|{item.course}|{item.title}|{item.start.isoformat()}"


def _left(delta: dt.timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes >= 90:
        hours = round(minutes / 60)
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{max(minutes, 1)} minute" + ("s" if minutes != 1 else "")


class DeadlineAlerts:
    def __init__(self, ctx, publish, hours: list[float] | None = None, class_minutes: float = 10, interval_s: float = 60):
        self.ctx = ctx
        self.publish = publish                    # publish(kind, data): the event bus
        self.hours = sorted(hours or [24, 3], reverse=True)
        self.class_minutes = class_minutes
        self.interval_s = interval_s
        self._stop = threading.Event()

    def _sent(self) -> set[str]:
        raw = self.ctx.memory.get("alerts_sent")
        return set(json.loads(raw)) if raw else set()

    def check(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or dt.datetime.now()
        feed = getattr(self.ctx, "canvas", None)
        if feed is None:
            return []
        try:
            items = feed.between(now, now + dt.timedelta(hours=max(self.hours) + 1))
        except Exception as exc:
            log.debug("deadline alerts: canvas unavailable (%s)", exc)
            return []
        sent = self._sent()
        out = []
        for item in items:
            left = item.start - now
            if item.kind == "assignment":
                windows = [h for h in self.hours if left <= dt.timedelta(hours=h)]
                if not windows:
                    continue
                tag = f"{_key(item)}|{min(windows)}h"
                if tag in sent:
                    continue
                sent.update(f"{_key(item)}|{h}h" for h in windows)       # skip the bigger windows we're past
                due = "by end of day" if item.all_day else item.when
                out.append({"kind": "due", "title": f"Due in {_left(left)}: {item.title}",
                            "text": f"{item.course} · due {due}", "url": item.url})
            elif item.kind == "class" and left <= dt.timedelta(minutes=self.class_minutes):
                tag = f"{_key(item)}|class"
                if tag in sent:
                    continue
                sent.add(tag)
                out.append({"kind": "class", "title": f"Class in {_left(left)}: {item.title}",
                            "text": f"{item.course} · tap to join" if item.link else item.course,
                            "url": item.link or item.url})
        if out:
            # keep only keys for items still in the future, so the list doesn't grow forever
            live = {_key(i) for i in items}
            self.ctx.memory.set("alerts_sent", json.dumps(sorted(t for t in sent if t.rsplit("|", 1)[0] in live)))
            for alert in out:
                log.info("alert: %s", alert["title"])
                self.publish("alert", alert)
        return out

    def start(self):
        threading.Thread(target=self._run, name="alerts", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval_s):
            try:
                self.check()
            except Exception as exc:
                log.warning("deadline alerts failed: %s", exc)


class MailAlerts:
    """Phone notification when unread mail arrives from senders the user chose (config
    mail.alert_senders: names, addresses or domains like "@asu.edu"). Checks every 2 minutes;
    mail already there when Max starts doesn't trigger alerts."""

    def __init__(self, ctx, publish, senders: list[str], interval_s: float = 120):
        self.ctx = ctx
        self.publish = publish
        self.senders = [s.lower().strip() for s in senders if s and s.strip()]
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._primed = False

    def matches(self, mail) -> bool:
        who = f"{mail.sender} {mail.address}".lower()
        return any(s in who for s in self.senders)

    def check(self) -> list[dict]:
        mail = getattr(self.ctx, "mail", None)
        if mail is None or not self.senders:
            return []
        stored = self.ctx.memory.get("mail_alerted")
        if stored is not None:
            self._primed = True          # not the very first run: anything new since last time alerts
        seen = set(json.loads(stored or "[]"))
        out = []
        for m in mail.search("is:unread in:inbox newer_than:2d", 15):
            if m.msgid in seen:
                continue
            seen.add(m.msgid)
            if self._primed and self.matches(m):
                out.append({"kind": "mail", "title": f"Email from {m.sender}", "text": m.subject, "url": m.link})
        self._primed = True
        self.ctx.memory.set("mail_alerted", json.dumps(sorted(seen)[-300:]))
        for alert in out:
            log.info("mail alert: %s", alert["title"])
            self.publish("alert", alert)
        return out

    def start(self):
        threading.Thread(target=self._run, name="mail-alerts", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while True:
            try:
                self.check()
            except Exception as exc:
                log.warning("mail alerts: %s", exc)
            if self._stop.wait(self.interval_s):
                return
