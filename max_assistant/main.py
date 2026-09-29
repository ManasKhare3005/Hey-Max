"""Max entry point: voice loop (default) or typed chat (--text)."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from .agent import Agent, parse_yes_no
from .config import load_config, resolve_path
from .llm import OllamaClient
from .tools import system as system_tools
from .tools import browser as browser_tools
from .tools import web as web_tools
from .tools.registry import ToolRegistry

log = logging.getLogger("max")

CANCEL_PHRASES = ("never mind", "nevermind", "cancel", "forget it", "nothing")


class Context:
    """Shared state that tools can read (config, LLM client, last search results...)."""

    def __init__(self, cfg, llm):
        self.cfg = cfg
        self.llm = llm
        self.last_files: list[str] = []
        self.models_asleep = False
        self.web = None   # WebSearch, set by tools.web
        self.browser = None   # BrowserSession, set by tools.browser
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


def build(cfg, confirm, on_event):
    llm = OllamaClient(cfg.llm.host, cfg.llm.keep_alive, cfg.llm.temperature,
                       cfg.llm.get("think", False), cfg.llm.request_timeout_s,
                       num_ctx=cfg.llm.get("num_ctx"), num_predict=cfg.llm.get("num_predict"),
                       gpu_layers={cfg.llm.fast_model: cfg.llm.get("fast_model_gpu_layers")}
                       if cfg.llm.get("fast_model_gpu_layers") else None)
    ctx = Context(cfg, llm)
    ctx.confirm = confirm
    registry = ToolRegistry(context=ctx)
    system_tools.register(registry)
    web_tools.register(registry)
    browser_tools.register(registry)
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
    )
    return ctx, llm, agent


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


def event_printer(verbose: bool):
    def on_event(kind, data):
        if kind == "tool_call":
            print(f"   ⚙  {data['tool']}({data['args']})")
        elif kind == "escalate":
            print(f"   🧠 handing off to {data['to']}")
        elif verbose and kind == "tool_result":
            print(f"   ↳ {data['result'][:200]}")
    return on_event


# ---------------- text mode ----------------

def run_text(cfg, verbose: bool):
    def confirm(prompt: str) -> bool:
        return parse_yes_no(input(f"   ⚠  {prompt} (yes/no) > "))

    _, llm, agent = build(cfg, confirm, event_printer(verbose))
    check_models(llm, cfg)
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


def run_voice(cfg, verbose: bool):
    from .audio import Microphone, NoiseFloor, chime, record_utterance, rms
    from .stt import SpeechToText
    from .tts import Speaker

    a = cfg.audio
    print("Loading speech models (first run downloads them)...")
    mic = Microphone(a.sample_rate, a.input_device)
    stt = SpeechToText(cfg.stt.model, cfg.stt.device, cfg.stt.compute_type)
    speaker = Speaker(cfg.tts.engine, cfg.tts.piper_voice, cfg.tts.speed, a.output_device, mic=mic)
    wake = make_wake_detector(cfg)
    noise = NoiseFloor()

    def listen(timeout: float) -> str:
        if a.chime:
            chime("wake", a.output_device)
        mic.flush()
        audio = record_utterance(mic, noise, a.vad_sensitivity, a.silence_s, timeout, a.max_record_s)
        return stt.transcribe(audio) if audio is not None else ""

    def confirm(prompt: str) -> bool:
        say(prompt + " Say yes or no.")
        answer = listen(cfg.safety.confirm_timeout_s)
        print(f"   you: {answer or '(silence)'}")
        return parse_yes_no(answer)

    speak_lock = threading.Lock()

    def say(text: str):
        with speak_lock:          # one voice at a time: a filler never overlaps the answer
            speaker.say(text)

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

    ctx, llm, agent = build(cfg, confirm, on_event)
    models_ok = check_models(llm, cfg)
    if models_ok:
        threading.Thread(target=llm.preload, args=(cfg.llm.fast_model,), daemon=True).start()

    mic.start()
    print(f"\n✅ Ready. Say \"{wake.phrase}\". Press Ctrl+C to quit.\n")
    say(f"{cfg.assistant.name} online.")

    try:
        while True:
            frame = mic.read(timeout=1.0)
            if frame is None:
                continue
            if not wake.process(frame):
                if rms(frame) < noise.level * 2:
                    noise.update(frame)  # learn the room's background noise while idle
                continue

            print("👂 Listening...")
            text = listen(a.no_speech_timeout_s)
            if not text:
                if a.chime:
                    chime("done", a.output_device)
                print("   (didn't catch anything)")
            elif text.lower().strip(" .!?") in CANCEL_PHRASES:
                say("Okay.")
            else:
                print(f"🗣  you: {text}")
                ctx.models_asleep = False
                answer = agent.handle(text)
                print(f"🤖 {cfg.assistant.name.lower()}: {answer}\n")
                say(answer)
            wake.reset()
            mic.flush()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        mic.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="max", description="Local voice assistant")
    parser.add_argument("--text", action="store_true", help="Type instead of talking (no mic needed)")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--list-devices", action="store_true", help="Show audio devices and exit")
    parser.add_argument("--wake-test", action="store_true", help="Show live wake word scores to tune the threshold")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.list_devices:
        from .audio import list_devices

        print(list_devices())
        return

    cfg = load_config(args.config)
    setup_logging(cfg, args.verbose)
    if args.wake_test:
        run_wake_test(cfg)
    elif args.text:
        run_text(cfg, args.verbose)
    else:
        run_voice(cfg, args.verbose)


if __name__ == "__main__":
    main()
