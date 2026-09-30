"""Dashboard backend: REST API + WebSocket event stream, served from a background thread.

Bound to 127.0.0.1 only. This is also the API the Phase 4 phone/watch apps will use
(then exposed over Tailscale). The React dashboard (dashboard/dist) is served at "/".
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT
from .events import ApprovalBroker, EventBus

log = logging.getLogger(__name__)
DIST = ROOT / "dashboard" / "dist"


@dataclass
class Runtime:
    """Everything the API needs from the running assistant."""
    ctx: Any
    bus: EventBus
    approvals: ApprovalBroker
    run_command: Callable[[str], str]           # typed command -> reply (serialised with voice)
    info: dict = field(default_factory=dict)    # static facts: name, wake phrase, models...


class Text(BaseModel):
    text: str


class NewReminder(BaseModel):
    text: str
    when: str


class Answer(BaseModel):
    approved: bool


class _Cached:
    """Slow status probes (nvidia-smi, SearXNG, Ollama) cached for a few seconds."""

    def __init__(self, fn: Callable[[], Any], ttl: float):
        self.fn, self.ttl, self.value, self.at = fn, ttl, None, 0.0
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() - self.at > self.ttl:
                try:
                    self.value = self.fn()
                except Exception as exc:
                    self.value = {"error": str(exc)[:120]}
                self.at = time.monotonic()
            return self.value


def _gpu():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    used, total, util = (float(x) for x in out.split(",")[:3])
    return {"vram_used_mb": used, "vram_total_mb": total, "util": util}


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="Max", docs_url="/api/docs", openapi_url="/api/openapi.json")
    cfg = rt.ctx.cfg
    host = cfg.llm.host.rstrip("/")
    searx = (cfg.get("web", {}) or {}).get("searxng_url", "")

    def ollama():
        models = requests.get(f"{host}/api/ps", timeout=2).json().get("models", [])
        return [{"name": m["name"], "vram_mb": round(m.get("size_vram", 0) / 2**20),
                 "size_mb": round(m.get("size", 0) / 2**20)} for m in models]

    def searxng_up():
        if not searx:
            return False
        return requests.get(f"{searx}/search", params={"q": "ping", "format": "json"}, timeout=2).ok

    probes = {"gpu": _Cached(_gpu, 5), "models": _Cached(ollama, 5), "searxng": _Cached(searxng_up, 30)}

    # ----- state & events -----
    @app.get("/api/state")
    def state():
        ctx = rt.ctx
        reminders = ctx.reminders.pending() if ctx.reminders else []
        return {
            **rt.info,
            "stage": rt.bus.state.get("stage", {}),
            "heartbeat": rt.bus.state.get("heartbeat", {}),
            "approvals": rt.approvals.pending(),
            "next_reminder": ({"id": reminders[0].id, "text": reminders[0].text, "due": reminders[0].due.isoformat()}
                              if reminders else None),
            "services": {
                "models": probes["models"].get(),
                "gpu": probes["gpu"].get(),
                "searxng": probes["searxng"].get(),
                "browser": bool(ctx.browser and ctx.browser.running),
                "memory": ctx.memory is not None,
            },
        }

    @app.get("/api/events/recent")
    def recent():
        return list(rt.bus.recent)

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        q = rt.bus.subscribe(asyncio.get_running_loop())
        try:
            await socket.send_json({"kind": "hello", "data": {"recent": list(rt.bus.recent)[-80:],
                                                              "state": rt.bus.state}})
            while True:
                await socket.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.bus.unsubscribe(q)

    # ----- commands & approvals -----
    @app.post("/api/command")
    def command(body: Text):                  # runs in FastAPI's thread pool
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty command")
        return {"reply": rt.run_command(text)}

    @app.post("/api/approvals/{approval_id}")
    def approve(approval_id: int, body: Answer):
        if not rt.approvals.answer(approval_id, body.approved):
            raise HTTPException(404, "no such pending approval")
        return {"ok": True}

    # ----- history -----
    @app.get("/api/turns")
    def turns(limit: int = 50):
        return rt.ctx.memory.recent_turns(limit) if rt.ctx.memory else []

    @app.get("/api/actions")
    def actions(limit: int = 200):
        return rt.ctx.memory.actions(limit) if rt.ctx.memory else []

    # ----- memory -----
    def memory():
        if rt.ctx.memory is None:
            raise HTTPException(503, "memory is disabled")
        return rt.ctx.memory

    @app.get("/api/facts")
    def facts(q: str = ""):
        hits = memory().search_facts(q, k=50, min_score=0.3) if q else memory().facts()
        return [{"id": h.id, "text": h.text, "created": h.ts, "score": round(h.score, 3)} for h in hits]

    @app.post("/api/facts")
    def add_fact(body: Text):
        fid, old = memory().add_fact(body.text)
        rt.bus.publish("memory", {"action": "added", "id": fid, "text": body.text})
        return {"id": fid, "updated": old is not None}

    @app.delete("/api/facts/{fact_id}")
    def delete_fact(fact_id: int):
        if not memory().delete_fact(fact_id):
            raise HTTPException(404, "no such fact")
        rt.bus.publish("memory", {"action": "deleted", "id": fact_id})
        return {"ok": True}

    # ----- reminders -----
    @app.get("/api/reminders")
    def reminders():
        if rt.ctx.reminders is None:
            return []
        return [{"id": r.id, "text": r.text, "due": r.due.isoformat()} for r in rt.ctx.reminders.pending()]

    @app.post("/api/reminders")
    def add_reminder(body: NewReminder):
        from .reminders import parse_when, spoken_time

        due = parse_when(body.when)
        if due is None:
            raise HTTPException(400, f"couldn't understand the time '{body.when}'")
        r = rt.ctx.reminders.add(body.text, due)
        rt.bus.publish("reminder", {"action": "added", "id": r.id, "text": r.text, "due": due.isoformat()})
        return {"id": r.id, "due": due.isoformat(), "spoken": spoken_time(due)}

    @app.delete("/api/reminders/{reminder_id}")
    def cancel_reminder(reminder_id: int):
        rt.ctx.reminders.set_status(reminder_id, "cancelled")
        rt.bus.publish("reminder", {"action": "cancelled", "id": reminder_id})
        return {"ok": True}

    # ----- the React app -----
    if (DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=DIST, html=True), name="dashboard")
    else:
        @app.get("/", response_class=HTMLResponse)
        def placeholder():
            return ("<h2 style='font-family:sans-serif'>Max API is running.</h2>"
                    "<p style='font-family:sans-serif'>Build the dashboard: <code>cd dashboard && npm install "
                    "&& npm run build</code>. API docs: <a href='/api/docs'>/api/docs</a></p>")

    return app


def serve(rt: Runtime, host: str = "127.0.0.1", port: int = 8765):
    """Start the server in a daemon thread. Returns the uvicorn Server (or None on failure)."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(create_app(rt), host=host, port=port, log_level="warning",
                                           access_log=False))
    threading.Thread(target=server.run, name="dashboard", daemon=True).start()
    log.info("dashboard at http://%s:%d", host, port)
    return server
