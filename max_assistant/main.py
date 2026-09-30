"""Max entry point: voice loop (default) or typed chat (--text)."""
from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
from pathlib import Path
import time
from logging.handlers import RotatingFileHandler

from .agent import Agent, parse_yes_no
from .config import load_config, resolve_path
from .llm import OllamaClient
from .tools import system as system_tools
from .tools import browser as browser_tools
from .tools import canvas as canvas_tools
from .tools import digest as digest_tools
from .tools import notes as notes_tools
from .tools import memory as memory_tools
from .tools import web as web_tools
from .tools.registry import ToolRegistry

log = logging.getLogger("max")

CANCEL_PHRASES = ("never mind", "nevermind", "cancel", "forget it", "nothing")
# What speech-to-text tends to "hear" in near-silence or in the tail of Max's own voice
PHANTOM_PHRASES = {"thank you", "thanks", "thanks for watching", "you", "bye", "okay", "uh", "um", "hmm"}


def is_question(reply: str) -> bool:
    """Does Max's reply end by asking the user something?"""
    return reply.rstrip().endswith("?")


def converse(first_text: str, respond, listen_again, follow_up: bool = True,
             on_heard=lambda t: None, on_idle=lambda: None) -> int:
    """Run one exchange, then keep going while Max's reply is a question.

    respond(text) -> reply (after speaking it); listen_again() -> transcript or ''.
    Returns how many requests were handled.
    """
    text, handled = first_text, 0
    while True:
        if not text:
            on_idle()
            return handled
        if text.lower().strip(" .!?") in CANCEL_PHRASES:
            respond(None)
            return handled
        on_heard(text)
        reply = respond(text)
        handled += 1
        if not (follow_up and reply and is_question(reply)):
            return handled
        text = listen_again()
        if text.lower().strip(" .!?,") in PHANTOM_PHRASES:
            text = ""      # not a real answer: go back to waiting for the wake word


class Context:
    """Shared state that tools can read (config, LLM client, last search results...)."""

    def __init__(self, cfg, llm):
        self.cfg = cfg
        self.llm = llm
        self.last_files: list[str] = []
        self.models_asleep = False
        self.web = None   # WebSearch, set by tools.web
        self.browser = None   # BrowserSession, set by tools.browser
        self.memory = None    # MemoryStore (facts, conversation log, actions)
        self.reminders = None
        self.canvas = None     # CanvasFeed, when secrets.yaml has the feed link
        self.notes = None      # NotesManager (meeting / lecture notes)
        self.stt = None        # SpeechToText, shared with notes (voice mode creates it)
        self.confirm = lambda prompt: False   # spoken yes/no, set by build()


def setup_logging(cfg, verbose: bool):
    path = resolve_path(cfg.logging.file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    handlers.append(console)
    logging.basicConfig(
        level=getattr(logging, str(cfg.logging.level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def build(cfg, confirm, on_event, bus=None):
    llm = OllamaClient(cfg.llm.host, cfg.llm.keep_alive, cfg.llm.temperature,
                       cfg.llm.get("think", False), cfg.llm.request_timeout_s,
                       num_ctx=cfg.llm.get("num_ctx"), num_predict=cfg.llm.get("num_predict"),
                       gpu_layers={cfg.llm.fast_model: cfg.llm.get("fast_model_gpu_layers")}
                       if cfg.llm.get("fast_model_gpu_layers") else None)
    ctx = Context(cfg, llm)
    ctx.confirm = confirm
    mem_cfg = cfg.get("memory", {})
    if mem_cfg.get("enabled", True):
        from .memory import MemoryStore
        from .reminders import Reminders

        ctx.memory = MemoryStore(mem_cfg.get("db", "data/max.db"))
        ctx.reminders = Reminders(ctx.memory)
        on_event = _logging_events(ctx.memory, on_event)
    if bus is not None:
        inner = on_event

        def on_event(kind, data, inner=inner):
            bus.publish(kind, data)
            inner(kind, data)
    notes_cfg = cfg.get("notes", {}) or {}
    if notes_cfg.get("enabled", True):
        from .notes import NotesManager

        def transcriber():
            if ctx.stt is None:                        # text mode: load Whisper on first use
                from .stt import SpeechToText

                ctx.stt = SpeechToText(cfg.stt.model, cfg.stt.device, cfg.stt.compute_type)
            return ctx.stt.whisper

        folder = Path(os.path.expanduser(notes_cfg.get("folder", "~/Documents/Max Notes")))
        ctx.notes = NotesManager(ctx, transcriber, folder, on_event=on_event,
                                 include_mic_in_meetings=notes_cfg.get("include_mic_in_meetings", True))
    registry = ToolRegistry(context=ctx)
    system_tools.register(registry)
    web_tools.register(registry)
    browser_tools.register(registry)
    memory_tools.register(registry)
    canvas_tools.register(registry)
    digest_tools.register(registry)
    notes_tools.register(registry)
    agent = Agent(
        llm, registry,
        fast_model=cfg.llm.fast_model,
        planner_model=cfg.llm.planner_model,
        name=cfg.assistant.name,
        user_name=cfg.assistant.user_name,
        max_steps=cfg.assistant.max_steps,
        confirm_risky=cfg.safety.confirm_risky,
        confirm=confirm,
        on_event=on_event,
        recall=ctx.memory.context_for if ctx.memory else None,
        on_turn=ctx.memory.log_turn if ctx.memory else None,
    )
    return ctx, llm, agent


def _logging_events(memory, on_event):
    """Also record what Max did (tool calls, approvals) in the action log for the dashboard."""
    def handler(kind, data):
        if kind == "tool_result":
            memory.log_action("tool", {"tool": data["tool"], "result": str(data["result"])[:300]})
        elif kind == "confirm_result":
            memory.log_action("approval", {"tool": data["tool"], "approved": data["approved"]})
        on_event(kind, data)
    return handler


def check_models(llm: OllamaClient, cfg) -> bool:
    have = llm.available_models()
    if not have:
        print("⚠  Can't reach Ollama at", cfg.llm.host, "- start the Ollama app and try again.")
        return False
    ok = True
    for m in (cfg.llm.fast_model, cfg.llm.planner_model):
        if m not in have and f"{m}:latest" not in have:
            print(f"⚠  Model {m} isn't downloaded. Run:  ollama pull {m}")
            ok = False
    return ok


# Said while a slow tool runs, so there's no dead air (voice mode only)
FILLERS = {
    "web_search": "Let me look that up.",
    "browser_read": "Reading the page.",
    "__escalate__": "Let me think about that one.",
}


def reminder_text(reminder, missed: bool) -> str:
    return (f"While you were away, you had a reminder: {reminder.text}." if missed
            else f"Reminder: {reminder.text}.")


def start_digest(ctx, bus=None):
    """Daily digest email at the configured time (needs memory for its "already sent" state)."""
    d = ctx.cfg.get("digest", {}) or {}
    if not d.get("enabled", False) or ctx.memory is None:
        return None
    from .digest import DigestScheduler
    from .reminders import toast

    def on_sent(subject, error):
        if bus is not None:
            bus.publish("digest", {"subject": subject, "error": error})
        if error:
            toast("Max: daily digest not sent", error)

    return DigestScheduler(ctx, d.get("time", "07:00"), d.get("weekends", True),
                           d.get("catch_up_until", "18:00"), on_sent).start()


def start_reminders(ctx, announce):
    """Run the reminder scheduler; `announce(text)` delivers the spoken/printed part."""
    if ctx.reminders is None:
        return None
    from .reminders import ReminderScheduler, toast

    def fire(reminder, missed):
        text = reminder_text(reminder, missed)
        toast("Max reminder", reminder.text)
        announce(text)

    return ReminderScheduler(ctx.reminders, fire).start()


def event_printer(verbose: bool):
    def on_event(kind, data):
        if kind == "tool_call":
            print(f"   ⚙  {data['tool']}({data['args']})")
        elif kind == "escalate":
            print(f"   🧠 handing off to {data['to']}")
        elif verbose and kind == "tool_result":
            print(f"   ↳ {data['result'][:200]}")
    return on_event


def start_dashboard(cfg, ctx, bus, approvals, run_command, info, controls=None):
    d = cfg.get("dashboard", {}) or {}
    if not d.get("enabled", True):
        return None
    try:
        from .server import Runtime, serve

        host, port = d.get("host", "127.0.0.1"), int(d.get("port", 8765))
        server = serve(Runtime(ctx, bus, approvals, run_command, info, controls), host, port)
        print(f"🖥  Dashboard: http://{host}:{port}")
        return server
    except Exception as exc:                   # the assistant works without it
        log.warning("dashboard failed to start: %s", exc)
        return None


def static_info(cfg, phrase: str, mode: str) -> dict:
    return {"name": cfg.assistant.name, "user": cfg.assistant.user_name, "wake_phrase": phrase, "mode": mode,
            "fast_model": cfg.llm.fast_model, "planner_model": cfg.llm.planner_model,
            "stt": cfg.stt.model, "started": time.time()}


# ---------------- text mode ----------------

def run_text(cfg, verbose: bool):
    def confirm(prompt: str) -> bool:
        return parse_yes_no(input(f"   ⚠  {prompt} (yes/no) > "))

    from .events import ApprovalBroker, EventBus

    bus = EventBus()
    approvals = ApprovalBroker(bus)
    agent_lock = threading.Lock()
    ctx, llm, agent = build(cfg, confirm, event_printer(verbose), bus=bus)
    check_models(llm, cfg)

    def run_command(text: str) -> str:
        with agent_lock:
            bus.publish("heard", {"text": text, "source": "dashboard"})
            reply = agent.handle(text)
            print(f"\n(dashboard) you > {text}\n{cfg.assistant.name.lower()} > {reply}\nyou > ", end="")
            return reply

    start_dashboard(cfg, ctx, bus, approvals, run_command, static_info(cfg, "(text mode)", "text"))
    start_digest(ctx, bus)
    bus.publish("stage", {"stage": "text mode"})
    start_reminders(ctx, lambda text: (bus.publish("reminder", {"action": "fired", "text": text}),
                                       print(f"\n⏰ {text}\nyou > ", end="")))
    print(f"{cfg.assistant.name} text mode. Type 'quit' to exit.\n")
    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in ("quit", "exit", "bye"):
            break
        if text:
            with agent_lock:
                bus.publish("heard", {"text": text, "source": "keyboard"})
                print(f"{cfg.assistant.name.lower()} > {agent.handle(text)}\n")


# ---------------- voice mode ----------------

def make_wake_detector(cfg):
    from .wakeword import make_detector

    return make_detector(cfg.wake_word)


def run_wake_test(cfg):
    """Live wake-word detections (and scores for openWakeWord), for tuning the threshold."""
    from .audio import Microphone, chime

    a = cfg.audio
    mic = Microphone(a.sample_rate, a.input_device)
    wake = make_wake_detector(cfg)
    scored = wake.last_score is not None
    print(f"Wake word: {wake.describe()}")
    print(f"Say \"{wake.phrase}\" 10 times, then talk normally / play a video to check for false "
          "triggers. Ctrl+C to stop.\n")
    mic.start()
    hits, peak, window_start, started = 0, 0.0, time.monotonic(), time.monotonic()
    try:
        while True:
            frame = mic.read(timeout=1.0)
            if frame is None:
                continue
            if wake.process(frame):
                hits += 1
                score = f" (score {wake.last_score:.2f})" if scored else ""
                print(f"  🔔 DETECTED #{hits} at {time.monotonic() - started:.0f}s{score}", flush=True)
                chime("wake", a.output_device)
            if scored:
                peak = max(peak, wake.last_score)
                if time.monotonic() - window_start >= 1.0:
                    print(f"  peak {peak:.2f} |{'#' * int(peak * 40):<40}|", flush=True)
                    peak, window_start = 0.0, time.monotonic()
    except KeyboardInterrupt:
        print(f"\n{hits} detections. Missed some? Lower the threshold. False triggers? Raise it.")
    finally:
        mic.stop()


def run_voice(cfg, verbose: bool, tray: bool = False):
    from .audio import Microphone, NoiseFloor, chime, rms
    from .listen import listen_for_command
    from .diagnostics import Heartbeat, foreground_window
    from .stt import SpeechToText
    from .tts import Speaker

    a = cfg.audio
    print("Loading speech models (first run downloads them)...")
    mic = Microphone(a.sample_rate, a.input_device)
    stt = SpeechToText(cfg.stt.model, cfg.stt.device, cfg.stt.compute_type,
                       cfg.stt.get("fast_model_dir"), cfg.stt.get("fast_min_snr_db", 12.0),
                       cfg.stt.get("prompt"))
    speaker = Speaker(cfg.tts.engine, cfg.tts.piper_voice, cfg.tts.speed, a.output_device, mic=mic)
    wake = make_wake_detector(cfg)
    noise = NoiseFloor()

    from .events import ApprovalBroker, EventBus

    bus = EventBus()
    approvals = ApprovalBroker(bus)
    agent_lock = threading.Lock()         # a typed (dashboard) and a spoken command never overlap
    beat = Heartbeat(mic, publish=bus.publish)
    level_tick = {"n": 0}

    def on_level(mean_square: float):     # ~6 updates/s for the dashboard orb
        level_tick["n"] += 1
        if level_tick["n"] % 2 == 0:
            bus.publish("level", {"rms": round(mean_square ** 0.5, 1)})

    mic.on_level = on_level

    def ring():
        mic.muted.set()           # don't record our own beep (it would count as speech)
        try:
            chime("wake", a.output_device)
        finally:
            mic.muted.clear()

    def listen(timeout: float, should_stop=None) -> str:
        # No mic.flush() here: audio right after "Hey Max" is kept, so "Hey Max, open
        # YouTube" works in one breath. (Speaker.say already drops Max's own voice.)
        if a.chime:
            threading.Thread(target=ring, daemon=True).start()
        beat.set("listening to command")
        text = listen_for_command(mic, noise, stt.transcribe, a.vad_sensitivity, a.silence_s, timeout,
                                  a.max_record_s, a.get("early_s", 0.4), a.get("max_silence_s", 1.8),
                                  should_stop=should_stop)
        if text:
            bus.publish("heard", {"text": text, "source": "voice"})
        return text

    def confirm(prompt: str) -> bool:
        # Voice and dashboard race: a click stops the listening early
        request = approvals.open(prompt)
        say(prompt + " Say yes or no.")
        answer = "" if request.done.is_set() else listen(cfg.safety.confirm_timeout_s, request.done.is_set)
        if request.done.is_set():
            approved, by = bool(request.approved), "dashboard"
        else:
            approved, by = parse_yes_no(answer), "voice"
            print(f"   you: {answer or '(silence)'}")
        approvals.close(request, approved, by)
        return approved

    speak_lock = threading.Lock()

    def say(text: str):
        with speak_lock:          # one voice at a time: a filler never overlaps the answer
            speaker.say(text)

    def say_main(text: str):      # the main loop's own speech, tracked by the heartbeat
        beat.set("speaking")
        say(text)

    printer = event_printer(verbose)
    turn = {"filler": False}

    def on_event(kind, data):
        printer(kind, data)
        if kind == "thinking" and data.get("step") == 0:
            turn["filler"] = False
        key = data.get("tool") if kind == "tool_call" else "__escalate__" if kind == "escalate" else None
        if key in FILLERS and not turn["filler"]:
            turn["filler"] = True
            threading.Thread(target=say, args=(FILLERS[key],), daemon=True).start()

    ctx, llm, agent = build(cfg, confirm, on_event, bus=bus)
    ctx.stt = stt
    announcements: queue.Queue[str] = queue.Queue()   # spoken reminders wait until Max is idle
    if ctx.notes is not None:
        def notes_done(result, error):
            from .reminders import toast

            if error:
                announcements.put("Sorry, writing up the notes failed. The transcript may still be saved.")
                return
            announcements.put(f"Your notes for {result.title} are ready.")
            toast("Max: notes ready", f"{result.title}: saved to {result.folder}")

        ctx.notes.on_done = notes_done

    def run_command(text: str) -> str:
        with agent_lock:
            bus.publish("heard", {"text": text, "source": "dashboard"})
            reply = agent.handle(text)
            print(f"⌨  (dashboard) you: {text}\n🤖 {cfg.assistant.name.lower()}: {reply}\n")
            if cfg.get("dashboard", {}).get("speak_typed_replies", False):
                announcements.put(reply)
            return reply

    def respond(text):
        if text is None:              # "never mind"
            say_main("Okay.")
            return None
        ctx.models_asleep = False
        beat.set("thinking")
        with agent_lock:
            answer = agent.handle(text)
        print(f"🤖 {cfg.assistant.name.lower()}: {answer}\n")
        say_main(answer)
        return answer

    def listen_follow_up() -> str:
        # Max just asked something: listen again without the wake word. Let the room echo
        # of Max's own voice die down first so it isn't recorded as an answer.
        time.sleep(0.3)
        mic.flush()
        print("👂 Listening (reply to Max, no need to say the wake word)...")
        return listen(a.get("follow_up_timeout_s", 5.0))

    def idle():
        if a.chime:
            chime("done", a.output_device)
        print("   (didn't catch anything)")
    models_ok = check_models(llm, cfg)
    if models_ok:
        threading.Thread(target=llm.preload, args=(cfg.llm.fast_model,), daemon=True).start()

    from .winutil import keep_awake_in_background

    tweaks = keep_awake_in_background()
    if tweaks:
        log.info("background listening: %s", ", ".join(tweaks))
    scheduler = start_reminders(ctx, lambda text: (bus.publish("reminder", {"action": "fired", "text": text}),
                                                   announcements.put(text)))
    from .events import Controls

    controls = Controls()
    start_dashboard(cfg, ctx, bus, approvals, run_command, static_info(cfg, wake.phrase, "voice"), controls)
    digest = start_digest(ctx, bus)
    mic.start()
    beat.start()
    print(f"\n✅ Ready. Say \"{wake.phrase}\". Press Ctrl+C to quit.\n")
    tray_icon = None
    if tray:
        from .tray import Tray

        d = cfg.get("dashboard", {}) or {}

        def sleep_models():
            for m in (cfg.llm.fast_model, cfg.llm.planner_model):
                llm.unload(m)
            ctx.models_asleep = True
            bus.publish("status", {"models": "asleep"})

        tray_icon = Tray(cfg.assistant.name, f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}",
                         sleep_models, controls).start()
    say_main(f"{cfg.assistant.name} online.")

    try:
        beat.set("waiting for wake word")
        while True:
            if controls.quit.is_set():               # tray, overlay or API asked Max to stop
                break
            manual = controls.listen_now.is_set()    # push-to-talk from the overlay/dashboard
            if manual:
                controls.listen_now.clear()
                mic.flush()                          # start fresh: only what's said after the click
            else:
                if controls.paused.is_set():         # paused: ignore the mic
                    if beat.stage != "paused":
                        beat.set("paused")
                    mic.read(timeout=0.3)
                    continue
                if beat.stage == "paused":
                    beat.set("waiting for wake word")
                if not announcements.empty():
                    text = announcements.get()
                    print(f"⏰ {text}")
                    say_main(text)
                    wake.reset()
                    beat.set("waiting for wake word")
                frame = mic.read(timeout=0.3)
                if frame is None:
                    continue
                if not wake.process(frame):
                    if rms(frame) < noise.level * 2:
                        noise.update(frame)  # learn the room's background noise while idle
                    continue

            log.info("front window at wake: %s", foreground_window())
            bus.publish("wake", {"phrase": wake.phrase, "manual": manual})
            print("👂 Listening...")
            converse(listen(a.no_speech_timeout_s), respond, listen_follow_up,
                     follow_up=a.get("follow_up", True),
                     on_heard=lambda t: print(f"🗣  you: {t}"), on_idle=idle)
            wake.reset()
            mic.flush()
            beat.set("waiting for wake word")
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        bus.publish("stage", {"stage": "stopped"})
        if tray_icon is not None and tray_icon.icon is not None:
            tray_icon.icon.stop()
        beat.stop()
        if scheduler:
            scheduler.stop()
        if digest:
            digest.stop()
        mic.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="max", description="Local voice assistant")
    parser.add_argument("--text", action="store_true", help="Type instead of talking (no mic needed)")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--list-devices", action="store_true", help="Show audio devices and exit")
    parser.add_argument("--wake-test", action="store_true", help="Show live wake word scores to tune the threshold")
    parser.add_argument("--tray", action="store_true",
                        help="Background mode: no console needed, tray icon (used by install_autostart.ps1)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.list_devices:
        from .audio import list_devices

        print(list_devices())
        return

    cfg = load_config(args.config)
    if not args.text:
        # Before logging binds to stdout: a stalled console must never freeze the listener
        from .winutil import unblock_console

        unblock_console()
    setup_logging(cfg, args.verbose)
    if not (args.text or args.wake_test):
        from .winutil import single_instance

        instance = single_instance()               # noqa: F841 (held for the process lifetime)
        if instance is None:
            d = cfg.get("dashboard", {}) or {}
            print(f"{cfg.assistant.name} is already running; opening the dashboard.")
            import webbrowser

            webbrowser.open(f"http://{d.get('host', '127.0.0.1')}:{d.get('port', 8765)}")
            return
    if args.wake_test:
        run_wake_test(cfg)
    elif args.text:
        run_text(cfg, args.verbose)
    else:
        run_voice(cfg, args.verbose, tray=args.tray)


if __name__ == "__main__":
    main()
