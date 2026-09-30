"""Daily digest: what's due, classes and reminders for the day, emailed every morning.

Sent through Gmail with an app password (secrets.yaml -> email). If the laptop was off at
the scheduled time, it goes out when Max starts, until `catch_up_until`; never twice a day.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage

from .canvas import CanvasItem

log = logging.getLogger(__name__)
LINK = re.compile(r"https://[^\s<]+")


@dataclass
class DayPlan:
    day: dt.date
    due_today: list[CanvasItem] = field(default_factory=list)
    due_soon: list[CanvasItem] = field(default_factory=list)      # next 3 days
    classes: list[CanvasItem] = field(default_factory=list)
    reminders: list = field(default_factory=list)                # Reminder objects due that day
    canvas_error: str = ""

    @property
    def empty(self) -> bool:
        return not (self.due_today or self.due_soon or self.classes or self.reminders)


def gather(ctx, day: dt.date | None = None) -> DayPlan:
    day = day or dt.date.today()
    start = dt.datetime.combine(day, dt.time())
    plan = DayPlan(day)
    if ctx.canvas is not None:
        try:
            plan.due_today = ctx.canvas.between(start, start + dt.timedelta(days=1), "assignment")
            plan.due_soon = ctx.canvas.between(start + dt.timedelta(days=1), start + dt.timedelta(days=4), "assignment")
            plan.classes = ctx.canvas.between(start, start + dt.timedelta(days=1), "class")
        except Exception as exc:
            plan.canvas_error = str(exc)[:200]
    if ctx.reminders is not None:
        plan.reminders = [r for r in ctx.reminders.pending() if r.due.date() == day]
    return plan


def spoken(plan: DayPlan) -> str:
    """Short version for "what does my day look like?"."""
    parts = []
    if plan.due_today:
        parts.append("Due today: " + ", ".join(f"{i.title} for {i.course}" for i in plan.due_today[:4]) + ".")
    if plan.classes:
        parts.append("Class: " + ", ".join(f"{c.course} {c.when}" for c in plan.classes[:3]) + ".")
    if plan.reminders:
        parts.append("Reminders: " + ", ".join(f"{r.text} at {r.due.strftime('%I:%M %p').lstrip('0')}" for r in plan.reminders[:3]) + ".")
    if plan.due_soon:
        parts.append("Coming up: " + ", ".join(f"{i.title} ({i.course}, {i.start.strftime('%A')})" for i in plan.due_soon[:3]) + ".")
    if not parts:
        return "Nothing due and no classes today. A free day."
    return " ".join(parts)


def focus_note(llm, model: str, plan: DayPlan) -> str:
    """Two friendly sentences on what to focus on, from the local model (rule-based fallback)."""
    fallback = ("Nothing is due today, so it's a good day to get ahead on what's coming up."
                if not plan.due_today else
                f"Today's priority: {plan.due_today[0].title} for {plan.due_today[0].course}.")
    if llm is None or plan.empty:
        return fallback
    try:
        reply = llm.chat(model, [
            {"role": "system", "content": "You write the opening of a student's morning email. Two short, warm, "
                                          "practical sentences on what to focus on today. No greeting, no lists."},
            {"role": "user", "content": spoken(plan)},
        ])
        text = reply.content.strip().strip('"')
        return text if 20 < len(text) < 400 else fallback
    except Exception as exc:
        log.warning("focus note failed: %s", exc)
        return fallback


def render(plan: DayPlan, note: str, name: str = "Max") -> tuple[str, str, str]:
    """(subject, plain text, html)."""
    day = plan.day.strftime("%A, %B ") + str(plan.day.day)
    n = len(plan.due_today)
    subject = f"Your day, {plan.day.strftime('%a %b')} {plan.day.day}: " + (f"{n} due today" if n else "nothing due today")

    def when_day(i: CanvasItem) -> str:
        return f"{i.start.strftime('%a')} {i.when}"

    sections = [
        ("Due today", [f"{i.title} ({i.course}) {i.when}" for i in plan.due_today]),
        ("Classes today", [f"{c.course} {c.title} {c.when}" + (f" · Zoom: {c.link}" if c.link else "") for c in plan.classes]),
        ("Reminders", [f"{r.text} at {r.due.strftime('%I:%M %p').lstrip('0')}" for r in plan.reminders]),
        ("Coming up (next 3 days)", [f"{i.title} ({i.course}) {when_day(i)}" for i in plan.due_soon]),
    ]
    text = [f"{day}", "", note, ""]
    for title, rows in sections:
        if rows:
            text += [title.upper()] + [f"  - {r}" for r in rows] + [""]
    if plan.canvas_error:
        text.append(f"(Canvas couldn't be reached: {plan.canvas_error})")
    text.append(f"-- {name}, your local assistant")

    def li(rows):
        # links (e.g. Zoom) become clickable
        link = lambda m: f"<a href='{m.group(0)}' style='color:#0891b2'>join</a>"
        return "".join(f"<li style='margin:4px 0'>{LINK.sub(link, html.escape(r))}</li>" for r in rows)

    blocks = "".join(
        f"<h3 style='font:600 12px/1.4 monospace;letter-spacing:.14em;text-transform:uppercase;color:#0e7490;margin:18px 0 6px'>{title}</h3>"
        f"<ul style='margin:0;padding-left:18px;color:#0f172a'>{li(rows)}</ul>"
        for title, rows in sections if rows)
    if not blocks:
        blocks = "<p style='color:#334155'>Nothing due, no classes and no reminders today.</p>"
    body = (
        "<div style='font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;padding:24px;"
        "border:1px solid #cbe9f2;border-radius:14px;background:#f8fdff'>"
        f"<div style='font:600 11px monospace;letter-spacing:.3em;color:#0891b2'>{html.escape(name.upper())} · DAILY DIGEST</div>"
        f"<h2 style='margin:6px 0 10px;color:#0f172a'>{html.escape(day)}</h2>"
        f"<p style='font-size:15px;line-height:1.55;color:#1e293b'>{html.escape(note)}</p>{blocks}"
        + (f"<p style='color:#b45309;font-size:13px'>Canvas couldn't be reached: {html.escape(plan.canvas_error)}</p>"
           if plan.canvas_error else "")
        + "<p style='color:#64748b;font-size:12px;margin-top:22px'>Sent by Max from your laptop.</p></div>")
    return subject, "\n".join(text), body


def send_email(email_cfg: dict, subject: str, text: str, body_html: str):
    user, password = email_cfg.get("smtp_user"), email_cfg.get("app_password")
    to = email_cfg.get("to") or user
    if not user or not password or "@" not in str(user) or "xxxx" in str(password):
        raise RuntimeError("email isn't set up: add smtp_user and app_password to secrets.yaml")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Max <{user}>", to
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    host, port = email_cfg.get("smtp_host", "smtp.gmail.com"), int(email_cfg.get("smtp_port", 465))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(user, str(password).replace(" ", ""))
        smtp.send_message(msg)


def send_digest(ctx, day: dt.date | None = None) -> str:
    """Build and email today's digest. Returns a short status line."""
    plan = gather(ctx, day)
    llm_model = ctx.cfg.llm.fast_model
    note = focus_note(None if ctx.models_asleep else ctx.llm, llm_model, plan)
    subject, text, body = render(plan, note, ctx.cfg.assistant.name)
    send_email((ctx.cfg.get("secrets", {}) or {}).get("email", {}) or {}, subject, text, body)
    if ctx.memory is not None:
        ctx.memory.set("digest_last_sent", (day or dt.date.today()).isoformat())
        ctx.memory.log_action("digest", {"subject": subject})
    return subject


def _hm(value: str, default: dt.time) -> dt.time:
    try:
        h, m = str(value).split(":")
        return dt.time(int(h), int(m))
    except Exception:
        return default


class DigestScheduler:
    """Sends the digest once a day at the configured time (or on catch-up after a late start)."""

    def __init__(self, ctx, send_at: str = "07:00", weekends: bool = True, catch_up_until: str = "18:00",
                 on_sent=None, interval_s: float = 60.0):
        self.ctx = ctx
        self.send_at = _hm(send_at, dt.time(7, 0))
        self.until = _hm(catch_up_until, dt.time(18, 0))
        self.weekends = weekends
        self.on_sent = on_sent or (lambda subject, error: None)
        self.interval_s = interval_s
        self._stop = threading.Event()

    def due(self, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now()
        if not self.weekends and now.weekday() >= 5:
            return False
        if not (self.send_at <= now.time() < self.until):
            return False
        return self.ctx.memory.get("digest_last_sent") != now.date().isoformat()

    def check(self, now: dt.datetime | None = None):
        if not self.due(now):
            return
        try:
            subject = send_digest(self.ctx)
            log.info("daily digest sent: %s", subject)
            self.on_sent(subject, None)
        except Exception as exc:
            log.warning("daily digest failed: %s", exc)
            # don't retry every minute on a config problem; try again tomorrow
            self.ctx.memory.set("digest_last_sent", (now or dt.datetime.now()).date().isoformat())
            self.on_sent(None, str(exc))

    def start(self):
        threading.Thread(target=self._run, name="digest", daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self.check()
            self._stop.wait(self.interval_s)
