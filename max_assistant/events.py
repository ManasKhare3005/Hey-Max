"""Event bus: everything Max does is published once here and streamed to the dashboard
(and later the phone and watch apps). Also holds the approval broker, so a risky action
can be approved by voice *or* by a click, whichever comes first."""
from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field


class EventBus:
    """Thread-safe publish; subscribers are asyncio queues living in the server's loop."""

    def __init__(self, history: int = 300):
        self._lock = threading.Lock()
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.recent: deque[dict] = deque(maxlen=history)
        self.state: dict = {"stage": "starting"}       # latest value of stateful events
        self._ids = itertools.count(1)

    def publish(self, kind: str, data: dict | None = None):
        event = {"id": next(self._ids), "ts": time.time(), "kind": kind, "data": data or {}}
        with self._lock:
            if kind in ("stage", "level", "heartbeat", "status"):
                self.state[kind] = event["data"]
            if kind != "level":                           # mic levels are too chatty to keep
                self.recent.append(event)
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_latest, q, event)
            except RuntimeError:                          # loop closed: subscriber is gone
                self.unsubscribe(q)
        return event

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subs = [(l, s) for l, s in self._subs if s is not q]


def _put_latest(q: asyncio.Queue, event: dict):
    if q.full():                     # a slow client drops its oldest event rather than blocking Max
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


@dataclass
class Approval:
    id: int
    prompt: str
    tool: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    approved: bool | None = None
    by: str = ""


class ApprovalBroker:
    """Pending approvals that can be answered from the dashboard."""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._pending: dict[int, Approval] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def open(self, prompt: str, tool: str = "") -> Approval:
        a = Approval(next(self._ids), prompt, tool)
        with self._lock:
            self._pending[a.id] = a
        self.bus.publish("approval_request", {"id": a.id, "prompt": prompt, "tool": tool})
        return a

    def answer(self, approval_id: int, approved: bool, by: str = "dashboard") -> bool:
        with self._lock:
            a = self._pending.get(approval_id)
        if a is None or a.done.is_set():
            return False
        a.approved, a.by = approved, by
        a.done.set()
        return True

    def close(self, a: Approval, approved: bool, by: str):
        """Record the final outcome (from voice, dashboard or timeout) and tell the dashboard."""
        if not a.done.is_set():
            a.approved, a.by = approved, by
            a.done.set()
        with self._lock:
            self._pending.pop(a.id, None)
        self.bus.publish("approval_result", {"id": a.id, "approved": bool(a.approved), "by": a.by})

    def pending(self) -> list[dict]:
        with self._lock:
            return [{"id": a.id, "prompt": a.prompt, "tool": a.tool} for a in self._pending.values()]
