"""Dashboard backend: REST API + WebSocket event stream, served from a background thread.

Bound to 127.0.0.1 only. The phone app reaches it through `tailscale serve` (see
remote.py): requests from this laptop are trusted, anything proxied needs the phone token.
The React dashboard (dashboard/dist) is served at "/".
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
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT
from .events import ApprovalBroker, EventBus
from . import remote

log = logging.getLogger(__name__)
DIST = ROOT / "dashboard" / "dist"


@dataclass
class Runtime:
    """Everything the API needs from the running assistant."""
    ctx: Any
    bus: EventBus
    approvals: ApprovalBroker
    run_command: Callable[..., str]             # (text, source=) -> reply (serialised with voice)
    info: dict = field(default_factory=dict)    # static facts: name, wake phrase, models...
    controls: Any = None                        # events.Controls (voice mode only)
    token: str = ""                             # phone token; "" = only this laptop may connect


class Text(BaseModel):
    text: str


class NewReminder(BaseModel):
    text: str
    when: str


class Answer(BaseModel):
    approved: bool


class NotesStart(BaseModel):
    kind: str = "lecture"
    title: str = ""


class DocsSummarize(BaseModel):
    paths: list[str] = []          # exact files; empty = every course file (or those matching course)
    course: str = ""
    redo: bool = False


class Speak(BaseModel):
    text: str


class Confirm(BaseModel):
    confirm: str = ""


class PhoneResult(BaseModel):
    id: int
    ok: bool = True
    message: str = ""


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
    speech = {"tts": None, "lock": threading.Lock()}

    def local(request) -> bool:
        return remote.is_local(request.client.host if request.client else None, request.headers)

    def allowed(request) -> bool:
        return local(request) or bool(rt.token) and remote.token_ok(rt.token, request.headers,
                                                                     request.query_params)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # The dashboard's static files hold nothing private; every API call does
        if request.url.path.startswith("/api") and not allowed(request):
            return JSONResponse({"detail": "pair this device first (missing or wrong token)"}, 401)
        return await call_next(request)

    def source(request) -> str:
        return "dashboard" if local(request) else "phone"

    def local_only(request):
        if not local(request):
            raise HTTPException(403, "only from the laptop itself")

    # ----- phone: pairing, voice in, voice out -----
    @app.get("/api/ping")
    def ping(request: Request):
        return {"ok": True, "name": rt.info.get("name", "Max"), "mode": rt.info.get("mode"),
                "local": local(request)}

    @app.get("/api/pair")
    def pair(request: Request):
        local_only(request)                  # never hand the token to a remote caller
        phone = cfg.get("phone", {}) or {}
        url = (phone.get("public_url") or remote.tailscale_url() or "").rstrip("/")
        link = remote.pairing_link(url, rt.token) if url and rt.token else ""
        return {"enabled": bool(rt.token), "url": url, "token": rt.token, "link": link,
                "qr_svg": remote.qr_svg(link) if link else "", "tailscale": bool(remote.tailscale_url())}

    def stt():
        ctx = rt.ctx
        if ctx.stt is None:                  # text mode: load Whisper on first use
            from .stt import SpeechToText

            ctx.stt = SpeechToText(cfg.stt.model, cfg.stt.device, cfg.stt.compute_type,
                                   prompt=cfg.stt.get("prompt"))
        return ctx.stt

    @app.post("/api/voice")
    async def voice(request: Request):
        """Phone push-to-talk: WAV body (16-bit PCM) -> transcript -> agent -> reply text."""
        from starlette.concurrency import run_in_threadpool

        from .listen import strip_wake_word

        body = await request.body()
        if not body or len(body) > 20 * 2**20:
            raise HTTPException(400, "send a WAV recording (up to 20 MB)")
        try:
            audio = remote.decode_wav(body)
        except Exception as exc:
            raise HTTPException(400, f"couldn't read the audio: {exc}")
        text = await run_in_threadpool(lambda: stt().whisper(audio))
        text = strip_wake_word(text) if text else ""
        if not text or text.lower().strip(" .!?,") in ("thank you", "thanks", "you", "bye", "uh", "um"):
            return {"heard": "", "reply": ""}
        reply = await run_in_threadpool(rt.run_command, text, source(request))
        return {"heard": text, "reply": reply}

    @app.post("/api/speak")
    def speak(body: Speak):
        """Max's voice for the phone: text -> WAV (Piper, same voice as the laptop)."""
        from .tts import clean_for_speech, make_voice

        text = clean_for_speech(body.text)[:1500]
        if not text:
            raise HTTPException(400, "nothing to say")
        with speech["lock"]:
            if speech["tts"] is None:
                speech["tts"] = make_voice(cfg.tts)              # same voice as the laptop (Ava or Piper)
                if speech["tts"] is None:
                    raise HTTPException(503, "no voice available")
            audio, sr = speech["tts"].synthesize(text)
        return Response(remote.encode_wav(audio, sr), media_type="audio/wav")

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
        if not allowed(socket):
            await socket.close(code=4401)
            return
        await socket.accept()
        q = rt.bus.subscribe(asyncio.get_running_loop())
        phone = getattr(rt.ctx, "phone", None) if not local(socket) else None
        if phone is not None:
            phone.connected(+1)
        try:
            await socket.send_json({"kind": "hello", "data": {"recent": list(rt.bus.recent)[-80:],
                                                              "state": rt.bus.state}})
            while True:
                await socket.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.bus.unsubscribe(q)
            if phone is not None:
                phone.connected(-1)

    # ----- command accuracy test results (evals.py) -----
    @app.get("/api/evals")
    def evals_results():
        from . import evals

        return {"runs": evals.runs(), "latest": evals.latest()}

    # ----- privacy page: what Max keeps, export, delete (laptop only) -----
    @app.get("/api/privacy")
    def privacy(request: Request):
        local_only(request)
        from .privacy import Privacy

        return Privacy(rt.ctx).categories()

    @app.get("/api/privacy/export")
    def privacy_export(request: Request):
        local_only(request)
        from .privacy import Privacy

        name = f"max-data-{time.strftime('%Y-%m-%d')}.zip"
        return Response(Privacy(rt.ctx).export(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/privacy/delete/{category}")
    def privacy_delete(category: str, body: Confirm, request: Request):
        local_only(request)
        from .privacy import Privacy

        if body.confirm != category:                 # the page sends the category name again to confirm
            raise HTTPException(400, "confirm by sending the category name")
        try:
            message = Privacy(rt.ctx).delete(category)
        except KeyError:
            raise HTTPException(404, f"unknown category {category}")
        rt.bus.publish("privacy", {"deleted": category})
        return {"ok": True, "message": message}

    # ----- the phone carrying out Max's actions (phone.py) -----
    def bridge():
        phone = getattr(rt.ctx, "phone", None)
        if phone is None:
            raise HTTPException(503, "phone actions are disabled")
        return phone

    @app.post("/api/phone/result")
    def phone_result(body: PhoneResult):
        if not bridge().resolve(body.id, {"ok": body.ok, "message": body.message}):
            raise HTTPException(404, "no such pending phone action (it may have timed out)")
        return {"ok": True}

    @app.post("/api/phone/state")
    async def phone_state(request: Request):
        state = await request.json()
        apps = [a for a in state.get("apps", []) if isinstance(a, dict)][:600]
        bridge().set_state({**state, "apps": apps})
        return {"ok": True, "apps": len(apps)}

    # ----- commands & approvals -----
    @app.post("/api/command")
    def command(body: Text, request: Request):   # runs in FastAPI's thread pool
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty command")
        return {"reply": rt.run_command(text, source(request))}

    @app.post("/api/approvals/{approval_id}")
    def approve(approval_id: int, body: Answer, request: Request):
        if not rt.approvals.answer(approval_id, body.approved, source(request)):
            raise HTTPException(404, "no such pending approval")
        return {"ok": True}

    # ----- remote control (desktop overlay, later the phone) -----
    @app.post("/api/control/{action}")
    def control(action: str):
        c = rt.controls
        if c is None:
            raise HTTPException(409, "remote control needs voice mode")
        if action == "listen":
            c.paused.clear()
            c.listen_now.set()
        elif action == "pause":
            c.paused.set()
        elif action == "resume":
            c.paused.clear()
        elif action == "quit":
            c.quit.set()
        else:
            raise HTTPException(404, f"unknown action {action}")
        rt.bus.publish("control", {"action": action})
        return {"ok": True, "paused": c.paused.is_set()}

    @app.get("/api/today")
    def today():
        from .digest import gather

        plan = gather(rt.ctx)
        item = lambda i: {"title": i.title, "course": i.course, "when": i.when, "due": i.start.isoformat(),
                          "link": i.link or i.url}
        return {
            "date": plan.day.isoformat(),
            "due_today": [item(i) for i in plan.due_today],
            "due_soon": [item(i) for i in plan.due_soon],
            "classes": [item(i) for i in plan.classes],
            "reminders": [{"id": r.id, "text": r.text, "due": r.due.isoformat()} for r in plan.reminders],
            "canvas_error": plan.canvas_error,
            "paused": bool(rt.controls and rt.controls.paused.is_set()),
        }

    # ----- meeting / lecture notes -----
    def notes_mgr():
        if rt.ctx.notes is None:
            raise HTTPException(503, "notes are disabled")
        return rt.ctx.notes

    @app.get("/api/notes/status")
    def notes_status():
        return notes_mgr().status()

    @app.get("/api/notes/live")
    def notes_live():
        """Live transcript while recording: recent Whisper text + live captions after it."""
        return notes_mgr().live_view()

    @app.post("/api/notes/start")
    def notes_start(body: NotesStart):
        try:
            s = notes_mgr().start(body.kind, body.title)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "title": s.title, "kind": s.kind}

    @app.post("/api/notes/stop")
    def notes_stop():
        try:
            notes_mgr().stop()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @app.get("/api/notes")
    def notes_list():
        return rt.ctx.memory.notes(50) if rt.ctx.memory else []

    @app.get("/api/notes/{note_id}")
    def notes_get(note_id: int):
        from pathlib import Path

        n = rt.ctx.memory.note(note_id) if rt.ctx.memory else None
        if n is None:
            raise HTTPException(404, "no such notes")
        folder = Path(n["folder"])
        read = lambda name: (folder / name).read_text(encoding="utf-8") if (folder / name).exists() else ""
        from .docnotes import TEXT_FILE

        text = read(TEXT_FILE) if n.get("kind") == "document" else read("transcript.md")
        return {**n, "notes_md": read("notes.md"), "transcript_md": text,
                "summary_md": read("summary.md")}

    @app.post("/api/notes/{note_id}/summary")
    def notes_summary(note_id: int, refresh: bool = False):
        """Short study summary (TL;DR, takeaways, to-dos); made by the local model on first ask."""
        from pathlib import Path

        n = rt.ctx.memory.note(note_id) if rt.ctx.memory else None
        if n is None or not (Path(n["folder"]) / "notes.md").exists():
            raise HTTPException(404, "no such notes")
        try:
            text = notes_mgr().summary(Path(n["folder"]), n.get("kind") or "lecture", refresh)
        except Exception as exc:
            raise HTTPException(503, f"couldn't write the summary: {exc}")
        return {"summary_md": text}

    # ----- notes from course documents -----
    def docs_mgr():
        if getattr(rt.ctx, "docnotes", None) is None:
            raise HTTPException(503, "course files are disabled")
        return rt.ctx.docnotes

    @app.get("/api/documents")
    def docs_list():
        """Course files, whether each already has notes, and what's being summarized now."""
        d = docs_mgr()
        files = [{"path": str(p), "name": p.name, "course": c, "done": d.is_done(p)} for p, c in d.files()]
        return {"folder": str(d.library.folders[0]), "files": files, "status": d.status()}

    @app.post("/api/documents/summarize")
    def docs_summarize(body: DocsSummarize):
        from pathlib import Path

        d = docs_mgr()
        items = d.match(body.course)
        if body.paths:
            want = {str(Path(p)) for p in body.paths}
            items = [(p, c) for p, c in items if str(p) in want]
        if not items:
            raise HTTPException(404, "no matching course files")
        queued, skipped = d.enqueue(items, redo=body.redo)
        return {"queued": [p.name for p in queued], "skipped": [p.name for p in skipped]}

    @app.post("/api/documents/upload")
    async def docs_upload(request: Request, name: str, course: str = "", summarize: bool = True):
        """Add a document (raw file body; ?name=lecture3.pdf&course=CSE 573) to the course folder,
        then write its notes in the background."""
        from pathlib import Path

        from starlette.concurrency import run_in_threadpool

        from .course import TYPES
        from .notes import safe_name

        d = docs_mgr()
        name = Path(name).name
        ext = Path(name).suffix.lower()
        if ext not in TYPES:
            raise HTTPException(400, f"Max can read {', '.join(sorted(TYPES))} files")
        body = await request.body()
        if not body or len(body) > 50 * 2**20:
            raise HTTPException(400, "send the file (up to 50 MB)")
        folder = d.library.folders[0] / safe_name(course) if course.strip() else d.library.folders[0]
        folder.mkdir(parents=True, exist_ok=True)
        stem = safe_name(Path(name).stem)
        target, n = folder / f"{stem}{ext}", 2
        while target.exists():
            target, n = folder / f"{stem} ({n}){ext}", n + 1
        target.write_bytes(body)
        threading.Thread(target=d.library.scan, name="course-scan-upload", daemon=True).start()
        queued = []
        if summarize:
            course_name = d.library._course_of(target, d.library.folders[0])
            queued, _ = await run_in_threadpool(d.enqueue, [(target, course_name)])
        return {"saved": str(target), "name": target.name, "summarizing": bool(queued)}

    @app.post("/api/notes/{note_id}/open")
    def notes_open(note_id: int, request: Request):
        import os

        local_only(request)                            # opens Explorer on the laptop

        n = rt.ctx.memory.note(note_id) if rt.ctx.memory else None
        if n is None:
            raise HTTPException(404, "no such notes")
        os.startfile(n["folder"])                      # opens the folder in Explorer
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
