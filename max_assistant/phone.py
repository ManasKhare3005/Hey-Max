"""Link to the phone app: Max asks the phone to do something and waits for its answer.

The laptop publishes a `phone_action` event (the phone app hears it over its WebSocket),
the phone carries it out (opens an app, starts a call, sets an alarm, reads notifications...)
and posts the outcome to /api/phone/result. The phone also posts its state when it connects
(installed apps, battery) so Max can match app names without asking.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None


class PhoneBridge:
    def __init__(self, bus, timeout: float = 12.0):
        self.bus = bus
        self.timeout = timeout
        self.connections = 0                 # phone WebSockets open right now
        self.state: dict = {}                # last state the phone posted (apps, battery, ...)
        self.state_at = 0.0
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()

    @property
    def online(self) -> bool:
        return self.connections > 0

    def connected(self, delta: int):
        with self._lock:
            self.connections = max(0, self.connections + delta)

    def set_state(self, state: dict):
        self.state = state or {}
        self.state_at = time.time()

    def apps(self) -> dict[str, str]:
        """Installed apps the phone reported: lower-case label -> package."""
        return {a["label"].lower(): a["package"] for a in self.state.get("apps", []) if a.get("label") and a.get("package")}

    def request(self, action: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """Ask the phone to do `action`; returns {"ok": bool, "message": str, ...}."""
        if not self.online:
            return {"ok": False, "message": "Your phone isn't connected to Max right now."}
        rid = next(self._ids)
        pending = _Pending()
        with self._lock:
            self._pending[rid] = pending
        try:
            self.bus.publish("phone_action", {"id": rid, "action": action, "params": params or {}})
            if not pending.done.wait(timeout or self.timeout):
                return {"ok": False, "message": "Your phone didn't answer in time. Is the Max app allowed to run in the background?"}
            return pending.result or {"ok": False, "message": "The phone sent an empty answer."}
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    def resolve(self, rid: int, result: dict) -> bool:
        with self._lock:
            pending = self._pending.get(rid)
        if pending is None:
            return False
        pending.result = result
        pending.done.set()
        return True
