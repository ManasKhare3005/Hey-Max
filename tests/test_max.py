"""Tests run anywhere: no mic, GPU, Ollama or Windows needed."""
from pathlib import Path

import numpy as np
import pytest

from max_assistant.agent import Agent, parse_yes_no, strip_thinking
from max_assistant.audio import FRAME, NoiseFloor, record_utterance
from max_assistant.config import Section, load_config
from max_assistant.llm import ChatReply, ToolCall
from max_assistant.main import Context
from max_assistant.tools import system as system_tools
from max_assistant.tools.registry import DECLINED, ToolRegistry
from max_assistant.tools.system import best_match
from max_assistant.tts import clean_for_speech


class FakeLLM:
    """Replays scripted replies and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, messages, tools=None):
        self.calls.append({"model": model, "messages": [dict(m) for m in messages],
                           "tools": [t["function"]["name"] for t in tools or []]})
        return self.replies.pop(0)

    def unload(self, model):
        self.unloaded = getattr(self, "unloaded", []) + [model]


def make_registry(tmp_path, llm=None):
    cfg = load_config()
    cfg["notes"] = {"file": str(tmp_path / "notes.md")}
    reg = ToolRegistry(context=Context(Section(cfg), llm))
    system_tools.register(reg)
    return reg


def make_agent(reg, llm, confirm=lambda p: False, events=None):
    return Agent(llm, reg, "fast", "planner", confirm=confirm,
                 on_event=(lambda k, d: events.append((k, d))) if events is not None else None)


# ---------- parsing ----------

@pytest.mark.parametrize("text,expected", [
    ("Yes", True), ("yeah go ahead", True), ("sure.", True), ("okay", True),
    ("no", False), ("yes... no wait, cancel", False), ("", False), (None, False),
    ("what?", False), ("don't do it", False),
])
def test_yes_no(text, expected):
    assert parse_yes_no(text) is expected


def test_strip_thinking():
    assert strip_thinking("<think>hmm\nplan</think> Hello") == "Hello"
    # Leaked reasoning with only the closing tag (seen after tool calls)
    assert strip_thinking("Okay, the user asked...\nSo: done.\n</think>\n\nSettings opened.") == "Settings opened."
    assert strip_thinking("Hi there <think>unfinished") == "Hi there"
    assert strip_thinking("Plain reply.") == "Plain reply."


def test_clean_for_speech():
    out = clean_for_speech("**Done!** See https://x.com\n- item 🎉")
    assert "*" not in out and "http" not in out and "🎉" not in out
    assert "Done!" in out and "a link" in out


def test_best_match():
    names = ["spotify", "google chrome", "visual studio code", "microsoft edge"]
    assert best_match("chrome", names) == "google chrome"
    assert best_match("spotfy", names) == "spotify"
    assert best_match("visual studio", names) == "visual studio code"
    assert best_match("photoshop", names) is None


# ---------- registry ----------

def test_registry_schemas_and_validation(tmp_path):
    reg = make_registry(tmp_path)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert {"get_datetime", "open_app", "close_app", "take_note", "power", "free_gpu"} <= names
    assert reg.get("power").risky and reg.get("close_app").risky
    assert not reg.get("open_app").risky
    assert "missing argument" in reg.run("take_note", {})
    assert "unknown tool" in reg.run("hack_nasa", {})
    # hallucinated extra args are dropped, not passed through
    assert reg.run("get_datetime", {"timezone": "UTC"}).startswith("It's")


def test_notes_roundtrip(tmp_path):
    reg = make_registry(tmp_path)
    assert reg.run("read_notes", {}) == "You don't have any notes yet."
    reg.run("take_note", {"text": "buy milk"})
    reg.run("take_note", {"text": "call mom"})
    out = reg.run("read_notes", {"count": 5})
    assert "buy milk" in out and "call mom" in out


def test_open_website(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(system_tools.webbrowser, "open", opened.append)
    reg = make_registry(tmp_path)
    assert reg.run("open_website", {"target": "youtube.com"}) == "Opened youtube.com."
    assert reg.run("open_website", {"target": "weather in Tempe"}) == "Searching Google for weather in Tempe."
    assert reg.run("open_website", {"target": "cute dogs", "section": "images"}) ==         "Here are Google Images results for cute dogs."
    reg.run("open_website", {"target": "pizza", "section": "maps"})
    reg.run("open_website", {"target": "cats", "section": "bogus"})
    assert opened == [
        "https://youtube.com",
        "https://www.google.com/search?q=weather+in+Tempe",
        "https://www.google.com/search?q=cute+dogs&udm=2",
        "https://www.google.com/maps/search/pizza",
        "https://www.google.com/search?q=cats",            # unknown section -> normal search
    ]


def test_tool_exception_is_reported(tmp_path):
    reg = make_registry(tmp_path)

    @reg.tool("boom", params={})
    def explode():
        raise ValueError("kaboom")

    assert reg.run("explode", {}) == "Error running explode: kaboom"


# ---------- agent ----------

def test_plain_answer(tmp_path):
    llm = FakeLLM([ChatReply("Hello Manas.")])
    agent = make_agent(make_registry(tmp_path), llm)
    assert agent.handle("hi") == "Hello Manas."
    assert "think_harder" in llm.calls[0]["tools"]


def test_tool_call_then_answer(tmp_path):
    """Tools returning raw data (not direct) go back to the LLM to be phrased."""
    llm = FakeLLM([
        ChatReply("", [ToolCall("read_notes", {})]),
        ChatReply("You have no notes yet, Manas."),
    ])
    agent = make_agent(make_registry(tmp_path), llm)
    assert agent.handle("read my notes") == "You have no notes yet, Manas."
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg == {"role": "tool", "content": "You don't have any notes yet.", "tool_name": "read_notes"}


def test_direct_tool_skips_second_llm_call(tmp_path):
    llm = FakeLLM([ChatReply("", [ToolCall("take_note", {"text": "gym at 6"})])])
    agent = make_agent(make_registry(tmp_path), llm)
    assert agent.handle("note gym at 6") == "Noted."
    assert len(llm.calls) == 1
    assert "gym at 6" in (tmp_path / "notes.md").read_text()


def test_direct_tools_in_one_step_are_joined(tmp_path):
    reg = make_registry(tmp_path)
    reg.get("volume").func = lambda action, level=None: "Volume set to about 30 percent."
    llm = FakeLLM([ChatReply("", [ToolCall("take_note", {"text": "x"}),
                                  ToolCall("volume", {"action": "set", "level": 30})])])
    agent = make_agent(reg, llm)
    assert agent.handle("note x, volume 30") == "Noted. Volume set to about 30 percent."
    assert len(llm.calls) == 1


def test_compound_request_keeps_going_after_direct_tool(tmp_path):
    llm = FakeLLM([
        ChatReply("", [ToolCall("take_note", {"text": "gym"})]),
        ChatReply("Noted, and your notes are empty otherwise."),
    ])
    agent = make_agent(make_registry(tmp_path), llm)
    agent.handle("note gym and then read my notes")
    assert len(llm.calls) == 2   # "and"/"then" might need more steps, so the LLM continues


def test_direct_tool_error_goes_to_llm(tmp_path):
    llm = FakeLLM([
        ChatReply("", [ToolCall("volume", {"action": "set"})]),
        ChatReply("What level should I set it to?"),
    ])
    agent = make_agent(make_registry(tmp_path), llm)
    assert agent.handle("set the volume") == "What level should I set it to?"
    assert llm.calls[1]["messages"][-1]["content"].startswith("Error")


def test_system_prompt_is_stable_and_time_goes_in_user_message(tmp_path):
    llm = FakeLLM([ChatReply("Hi."), ChatReply("Sure.")])
    agent = make_agent(make_registry(tmp_path), llm)
    agent.handle("hello")
    agent.handle("thanks")
    first, second = llm.calls[0]["messages"], llm.calls[1]["messages"]
    assert first[0] == second[0]                       # identical system prompt -> reusable prefix
    assert first[1] == second[1]                       # history holds exactly what was sent
    assert first[-1]["content"].startswith("[") and first[-1]["content"].endswith("] hello")


def test_risky_tool_declined(tmp_path):
    ran = []
    reg = make_registry(tmp_path)
    reg.get("power").func = lambda action: ran.append(action)
    prompts = []
    llm = FakeLLM([ChatReply("", [ToolCall("power", {"action": "shutdown"})])])
    agent = make_agent(reg, llm, confirm=lambda p: prompts.append(p) or False)
    assert agent.handle("shut down") == "Okay, I won't."
    assert ran == []
    assert prompts == ["Do you really want me to shutdown the laptop?"]
    assert len(llm.calls) == 1   # a declined action needs no LLM phrasing


def test_risky_tool_approved(tmp_path):
    ran = []
    reg = make_registry(tmp_path)
    reg.get("power").func = lambda action: ran.append(action) or "Shutting down."
    llm = FakeLLM([
        ChatReply("", [ToolCall("power", {"action": "restart"})]),
        ChatReply("Restarting now."),
    ])
    events = []
    agent = make_agent(reg, llm, confirm=lambda p: True, events=events)
    agent.handle("restart")
    assert ran == ["restart"]
    kinds = [k for k, _ in events]
    assert kinds.index("confirm") < kinds.index("tool_call")


def test_escalation_switches_model(tmp_path):
    llm = FakeLLM([
        ChatReply("", [ToolCall("think_harder", {"reason": "planning"})]),
        ChatReply("Here's your study plan."),
    ])
    agent = make_agent(make_registry(tmp_path), llm)
    assert agent.handle("plan my week") == "Here's your study plan."
    assert [c["model"] for c in llm.calls] == ["fast", "planner"]
    assert "think_harder" not in llm.calls[1]["tools"]


def test_step_limit(tmp_path):
    llm = FakeLLM([ChatReply("", [ToolCall("read_notes", {})])] * 10)
    agent = make_agent(make_registry(tmp_path), llm)
    agent.max_steps = 3
    assert "stuck" in agent.handle("loop forever")
    assert len(llm.calls) == 3


def test_history_carries_over(tmp_path):
    llm = FakeLLM([ChatReply("It's 5 PM."), ChatReply("Sure.")])
    agent = make_agent(make_registry(tmp_path), llm)
    agent.handle("what time is it")
    agent.handle("and remind me later")
    roles = [m["role"] for m in llm.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_free_gpu_unloads_both(tmp_path):
    llm = FakeLLM([])
    reg = make_registry(tmp_path, llm)
    reg.run("free_gpu", {})
    cfg = load_config()
    assert set(llm.unloaded) == {cfg.llm.fast_model, cfg.llm.planner_model}


# ---------- audio ----------

class FakeMic:
    sample_rate = 16000

    def __init__(self, frames):
        self.frames = list(frames)

    def read(self, timeout=1.0):
        return self.frames.pop(0) if self.frames else None


def tone(amp):
    return (np.ones(FRAME) * amp).astype(np.int16)


def test_record_stops_after_silence():
    frames = [tone(10)] * 5 + [tone(5000)] * 10 + [tone(10)] * 20 + [tone(5000)] * 50
    audio = record_utterance(FakeMic(frames), NoiseFloor(100), silence_s=1.0, max_record_s=20)
    # lead-in (<=4) + 10 speech + ~13 silence frames; never reaches the later speech
    assert audio is not None
    assert len(audio) < 30 * FRAME
    assert audio.dtype == np.float32 and np.abs(audio).max() <= 1.0


def test_record_returns_none_when_silent():
    frames = [tone(10)] * 100
    assert record_utterance(FakeMic(frames), NoiseFloor(100), no_speech_timeout_s=2.0) is None


def test_wake_model_builtin():
    from max_assistant.wakeword import resolve_wake_model

    m = resolve_wake_model("hey_jarvis")
    assert (m.source, m.custom, m.phrase) == ("hey_jarvis", False, "Hey Jarvis")


def test_wake_model_custom_file(tmp_path):
    from max_assistant.wakeword import resolve_wake_model

    f = tmp_path / "hey_max.onnx"
    f.write_bytes(b"fake")
    m = resolve_wake_model(str(f), phrase="Hey Max")
    assert (m.source, m.custom, m.phrase) == (str(f), True, "Hey Max")
    assert resolve_wake_model(str(f)).phrase == "Hey Max"  # derived from the file name


def test_wake_model_missing_file_falls_back(tmp_path):
    from max_assistant.wakeword import resolve_wake_model

    m = resolve_wake_model(str(tmp_path / "nope.onnx"), phrase="Hey Max")
    assert (m.source, m.custom, m.phrase) == ("hey_jarvis", False, "Hey Jarvis")


def test_normalize_phrase():
    from max_assistant.wakeword import normalize_phrase

    assert normalize_phrase(" Hey, Max! ") == "HEY MAX"
    assert normalize_phrase("okay   max") == "OKAY MAX"


def test_keyword_line():
    from max_assistant.wakeword import keyword_line

    pieces = {"HEY MAX": ["▁HE", "Y", "▁MA", "X"]}
    line = keyword_line("Hey Max!", pieces.get, {"▁HE", "Y", "▁MA", "X"}, 1.0, 0.25)
    assert line == "▁HE Y ▁MA X :1.0 #0.25 @HEY_MAX"
    with pytest.raises(ValueError):
        keyword_line("Hey Max", pieces.get, {"▁HE"}, 1.0, 0.25)  # token missing from model
    with pytest.raises(ValueError):
        keyword_line("!!!", pieces.get, set(), 1.0, 0.25)


KWS_DIR = Path(__file__).resolve().parent.parent / "models" / "kws" / "gigaspeech-3.3M"


@pytest.mark.skipif(not (KWS_DIR / "tokens.txt").exists(), reason="keyword spotting model not downloaded")
@pytest.mark.parametrize("phrase,expected", [("lovely child", True), ("Hey Max", False)])
def test_sherpa_detector_on_real_audio(phrase, expected):
    """End to end on the model's bundled clip, which contains '...a lovely child...'."""
    pytest.importorskip("sherpa_onnx")
    import wave

    from max_assistant.wakeword import SherpaKeywordDetector

    det = SherpaKeywordDetector(phrase, str(KWS_DIR))
    with wave.open(str(KWS_DIR / "test_wavs" / "1.wav")) as w:
        assert w.getframerate() == 16000
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = np.concatenate([audio, np.zeros(16000, np.int16)])
    heard = any(det.process(audio[i:i + FRAME]) for i in range(0, len(audio), FRAME))
    assert heard is expected


def test_auto_gain_boosts_quiet_but_not_loud():
    from max_assistant.wakeword import AutoGain

    agc = AutoGain(max_gain=8.0, target_peak=16000, window_frames=3)
    quiet = np.full(FRAME, 500, np.int16)
    assert agc(quiet).max() == pytest.approx(4000)          # capped at 8x
    loud = np.full(FRAME, 20000, np.int16)
    assert agc(loud).max() == pytest.approx(20000)          # never turned down
    assert agc(quiet).max() == pytest.approx(500)           # loud word still in window: no pumping
    for _ in range(3):
        out = agc(quiet)
    assert out.max() == pytest.approx(4000)                 # window passed: boost returns


class FakeStream:
    def __init__(self, sid):
        self.id, self.samples, self.pending, self.fired = sid, 0, False, False

    def accept_waveform(self, sr, x):
        self.samples += len(x)
        self.pending = True


class FakeSpotter:
    """Stands in for sherpa_onnx.KeywordSpotter: only stream `fire_stream` fires, once it has
    seen `fire_after` samples."""

    def __init__(self, fire_stream=2, fire_after=FRAME):
        self.fire_stream, self.fire_after, self.created = fire_stream, fire_after, []

    def create_stream(self):
        self.created.append(FakeStream(len(self.created)))
        return self.created[-1]

    def is_ready(self, s):
        return s.pending

    def decode_stream(self, s):
        s.pending = False

    def get_result(self, s):
        if s.id == self.fire_stream and not s.fired and s.samples >= self.fire_after:
            s.fired = True
            return "HEY_MAX"
        return ""

    def reset_stream(self, s):
        pass


def test_sherpa_streams_start_staggered_and_any_can_fire():
    from max_assistant.wakeword import SherpaKeywordDetector, WakeDetector

    det = SherpaKeywordDetector.__new__(SherpaKeywordDetector)   # skip model loading
    WakeDetector.__init__(det, cooldown_s=1.0)
    det.spotter, det.agc, det.num_streams = FakeSpotter(), None, 4
    det._stagger = int(16000 * det.CHUNK_S / det.num_streams)
    det.reset()

    started = []                      # (frame, streams alive) before any detection
    fired = []
    for i in range(12):
        if det.process(np.zeros(FRAME, np.int16)):
            fired.append(i)
            assert det.streams == [None] * 4   # firing restarts the staggered set
            break
        started.append(sum(s is not None for s in det.streams))
    # 0.64 s chunk / 4 streams = 2560 samples = 2 frames: a new stream every 2 frames
    assert det._stagger == 2560
    assert started == [1, 1, 2, 2]
    assert fired == [4]               # stream #2 starts at frame 4 and fires on its first frame


def test_llm_forces_gpu_layers_and_falls_back_on_load_error(monkeypatch):
    from max_assistant import llm as llm_mod

    sent = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body, self.text = code, body, str(body)

        def json(self):
            return self._body

    def fake_post(url, json, timeout):
        sent.append(json["options"].copy())
        if "num_gpu" in json["options"]:
            return Resp(500, {"error": "cudaMalloc failed: out of memory"})
        return Resp(200, {"message": {"content": "hi"}})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    client = llm_mod.OllamaClient("http://x", num_ctx=4096, num_predict=256, gpu_layers={"fast": 99})
    assert client.chat("fast", [{"role": "user", "content": "hey"}]).content == "hi"
    assert sent[0]["num_gpu"] == 99 and sent[0]["num_ctx"] == 4096 and sent[0]["num_predict"] == 256
    assert "num_gpu" not in sent[1]            # retried with Ollama's own split
    client.chat("fast", [{"role": "user", "content": "again"}])
    assert "num_gpu" not in sent[2]            # and stays off for the session
    client.chat("planner", [])
    assert "num_gpu" not in sent[3]            # never forced for other models


# ---------- web ----------

def test_select_passages_prefers_query_terms():
    from max_assistant.web import select_passages

    text = ("Paris is the capital of France. It has many museums. " * 3 +
            "The Eiffel Tower is 330 metres tall including antennas. " +
            "Bread is popular. " * 10)
    picked = select_passages(text, "how tall is the Eiffel Tower", n=1, size=120)
    assert len(picked) == 1 and "330 metres" in picked[0]


def test_select_passages_recency_finds_last_table_rows():
    from max_assistant.web import select_passages

    rows = " ".join(f"| {1967 + i} | Team{i} won the Super Bowl |." for i in range(60))
    picked = select_passages(rows, "most recent Super Bowl winner", n=1, size=200, this_year=2026)
    assert "2026" in picked[0] and "Team59" in picked[0]
    old = select_passages(rows, "first Super Bowl winner", n=1, size=200, this_year=2026)
    assert "1967" in old[0]                      # no recency intent: earliest match wins


def test_web_search_falls_back_to_duckduckgo(monkeypatch):
    import requests

    from max_assistant import web

    def boom(*a, **k):
        raise requests.ConnectionError("docker is off")

    monkeypatch.setattr(web.requests, "get", boom)
    monkeypatch.setattr(web.WebSearch, "_duckduckgo",
                        lambda self, q, n: [web.SearchResult("DDG hit", "https://example.com/a", "snippet")])
    ws = web.WebSearch("http://127.0.0.1:8888")
    hits = ws.search("anything")
    assert [h.title for h in hits] == ["DDG hit"] and ws.last_backend == "duckduckgo"


def test_web_search_tool_includes_passages_and_guard(tmp_path, monkeypatch):
    from max_assistant import web
    from max_assistant.tools import web as web_tools

    monkeypatch.setattr(web.WebSearch, "search", lambda self, q, n=None: [
        web.SearchResult("Super Bowl Winners", "https://www.espn.com/sb", "Every champion since 1967.")])
    monkeypatch.setattr(web, "fetch_text", lambda url, timeout=5.0: "| 2025 | Philadelphia won |. | 2026 | Seattle won |.")
    cfg = load_config()
    reg = ToolRegistry(context=Context(cfg, FakeLLM([])))
    web_tools.register(reg)
    out = reg.run("web_search", {"query": "latest Super Bowl winner"})
    assert "1. Super Bowl Winners (espn.com)" in out
    assert "Seattle won" in out                  # passage read from the page
    assert "don't guess" in out                  # anchored to the results


# ---------- browser (fake page, no Chrome needed) ----------

def el(i, name, role="link", **kw):
    from max_assistant.browser import Element

    base = dict(tag="a" if role == "link" else "button", type="", in_view=True, top=100 * i, heading=False,
                search=False, left=0, image=False, main=True)
    base.update(kw)
    return Element(i, name, base.pop("tag"), base.pop("type"), role, **base)


def test_pick_exact_ordinal_number_and_ambiguous():
    from max_assistant.browser import pick

    page = [el(1, "Images", role="tab", main=False), el(2, "Videos", role="tab", main=False),
            el(3, "Cute puppies playing in the snow", heading=True), el(4, "Ten tiny dogs you will love", heading=True),
            el(5, "Privacy", main=False)]
    assert pick(page, "Images")[0].id == 1
    assert pick(page, "first result")[0].id == 3            # nav tabs aren't results
    assert pick(page, "second result")[0].id == 4
    assert pick(page, "#5")[0].id == 5
    chosen, candidates = pick(page, "zzz qqq")
    assert chosen is None and candidates                    # nothing clear: let the LLM choose


def test_pick_thumbnail_becomes_title_link():
    from max_assistant.browser import pick

    page = [el(1, "Sponsored thing you should buy today", top=50),
            el(2, "11:53:45 Now playing", top=400, image=True),
            el(3, "lofi hip hop radio - beats to relax/study to", top=402),
            el(4, "6:10:58", top=700, image=True),
            el(5, "Best of lofi hip hop 2021 - 6 hour mix", top=703)]
    assert pick(page, "first video")[0].id == 3             # ad has no thumbnail; title beats "11:53:45"
    assert pick(page, "second video")[0].id == 5


class FakeSession:
    running = True

    def __init__(self, elements):
        self.elements, self.clicked, self.typed = elements, [], []

    def scan(self):
        return self.elements

    def settle(self, *a):
        pass

    def click(self, e):
        self.clicked.append(e.id)

    def type_into(self, e, text, submit):
        self.typed.append((e.id if e else None, text, submit))


def browser_registry(confirm_answer=False):
    from max_assistant.tools import browser as browser_tools

    cfg = load_config()
    ctx = Context(cfg, FakeLLM([]))
    asked = []
    ctx.confirm = lambda p: asked.append(p) or confirm_answer
    reg = ToolRegistry(context=ctx)
    browser_tools.register(reg)              # picks up the FakeSession patched in by patch_session
    return reg, ctx, asked


def patch_session(monkeypatch, fake):
    from max_assistant.tools import browser as browser_tools

    monkeypatch.setattr(browser_tools, "BrowserSession", lambda *a, **k: fake)


def test_browser_click_risky_needs_yes(tmp_path, monkeypatch):
    fake = FakeSession([el(1, "Subscribe to Lofi Girl.", role="button"), el(2, "Share", role="button")])
    patch_session(monkeypatch, fake)
    reg, ctx, asked = browser_registry(confirm_answer=False)
    assert reg.run("browser_click", {"target": "Share"}) == "Clicked Share."
    assert reg.run("browser_click", {"target": "Subscribe"}) == DECLINED
    assert asked == ["This will click 'Subscribe to Lofi Girl'. Should I?"]
    assert fake.clicked == [2]


def test_browser_type_search_submits_and_refuses_passwords(tmp_path, monkeypatch):
    fake = FakeSession([el(1, "Search", role="searchbox", tag="input", search=True),
                        el(2, "Password", role="textbox", tag="input", type="password"),
                        el(3, "Comment", role="textbox", tag="textarea")])
    patch_session(monkeypatch, fake)
    reg, ctx, asked = browser_registry()
    assert reg.run("browser_type", {"text": "lofi music"}) == "Typed it and submitted."
    assert fake.typed == [(1, "lofi music", True)]           # search box: Enter by default
    assert reg.run("browser_type", {"text": "hunter2", "field": "password"}).startswith("Error: I don't type passwords")
    assert reg.run("browser_type", {"text": "great video", "field": "comment", "submit": True}) == DECLINED
    assert len(fake.typed) == 1 and len(asked) == 1          # posting a comment needed a yes


def test_ambiguous_click_goes_back_to_llm(tmp_path, monkeypatch):
    fake = FakeSession([el(1, "Open settings panel", role="button"), el(2, "Open settings menu", role="button")])
    patch_session(monkeypatch, fake)
    reg, ctx, asked = browser_registry()
    llm = FakeLLM([ChatReply("", [ToolCall("browser_click", {"target": "zzqq"})]),
                   ChatReply("Which one: the settings panel or the settings menu?")])
    agent = make_agent(reg, llm)
    assert agent.handle("click that thing") == "Which one: the settings panel or the settings menu?"
    assert len(llm.calls) == 2                               # list went to the LLM, not read aloud


def test_pick_counts_videos_on_a_video_page():
    from max_assistant.browser import pick

    page = [el(1, "The Beatles", image=True, href="/channel/UCc4K7"),
            el(2, "Hey Jude (Remastered 2015)", image=True, href="/watch?v=A_MjCqQoLLA"),
            el(3, "Let It Be (Remastered 2009)", image=True, href="/watch?v=QDYfEBY9NM4"),
            el(4, "Come Together", image=True, href="/watch?v=45cYwDMibGo")]
    assert pick(page, "second link")[0].id == 3            # channel card isn't counted
    assert pick(page, "first video")[0].id == 2


def test_open_website_site_search(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(system_tools.webbrowser, "open", opened.append)
    reg = make_registry(tmp_path)
    assert reg.run("open_website", {"target": "Beatles", "site": "youtube"}) == "Searching YouTube for Beatles."
    reg.run("open_website", {"target": "usb c hub", "site": "amazon.com"})
    assert opened == ["https://www.youtube.com/results?search_query=Beatles", "https://www.amazon.com/s?k=usb+c+hub"]


def test_background_writer_never_blocks_the_caller():
    import threading
    import time

    from max_assistant.winutil import BackgroundWriter

    class StalledConsole:
        """Like a console in 'Select' mode: writes block until released."""
        def __init__(self):
            self.release, self.written = threading.Event(), []

        def write(self, text):
            self.release.wait()
            self.written.append(text)

        def flush(self):
            pass

    console = StalledConsole()
    out = BackgroundWriter(console, max_items=10)
    start = time.perf_counter()
    for i in range(50):
        print(f"line {i}", file=out)       # would hang forever if written directly
    assert time.perf_counter() - start < 0.5
    console.release.set()
    for _ in range(50):
        if console.written:
            break
        time.sleep(0.02)
    assert console.written and console.written[0] == "line 0"


# ---------- listening ----------

def test_looks_complete_and_strip_wake_word():
    from max_assistant.listen import looks_complete, strip_wake_word

    assert looks_complete("Open YouTube.") and looks_complete("What time is it?")
    assert not looks_complete("Can you search for...") and not looks_complete("Search for")
    assert not looks_complete("open the") and not looks_complete("")
    assert strip_wake_word("Max, open YouTube.") == "open YouTube."
    assert strip_wake_word("Hey Max what time is it") == "what time is it"
    assert strip_wake_word("Maxwell's equations") == "Maxwell's equations"


def speech(n, amp=5000):
    return [tone(amp)] * n


def test_listen_answers_early_when_the_request_is_complete():
    from max_assistant.listen import listen_for_command

    calls = []

    def transcribe(audio):
        calls.append(len(audio))
        return "Open YouTube."

    frames = speech(10) + [tone(10)] * 40            # 0.8 s speech, then 3.2 s of silence
    mic = FakeMic(frames)
    assert listen_for_command(mic, NoiseFloor(100), transcribe, silence_s=1.0, early_s=0.4) == "Open YouTube."
    used = 50 - len(mic.frames)
    assert used < 10 + 12 + 4                        # returned at ~0.4-0.5 s of silence, not 1.0 s


def test_listen_waits_when_the_sentence_trails_off():
    from max_assistant.listen import listen_for_command

    replies = iter(["Can you search for...", "Can you search for arctic monkeys?", "Can you search for arctic monkeys?"])
    frames = speech(10) + [tone(10)] * 15 + speech(8) + [tone(10)] * 40   # 1.2 s pause mid-sentence
    mic = FakeMic(frames)
    text = listen_for_command(mic, NoiseFloor(100), lambda a: next(replies), silence_s=1.0, early_s=0.4,
                              max_silence_s=1.8)
    assert text == "Can you search for arctic monkeys?"   # didn't cut off at the pause


def test_listen_keeps_words_said_right_after_wake():
    from max_assistant.listen import listen_for_command

    got = []
    frames = speech(6) + [tone(10)] * 30             # talking immediately, no pause for the chime
    listen_for_command(FakeMic(frames), NoiseFloor(100), lambda a: got.append(a) or "Open Spotify.")
    assert got and len(got[0]) >= 6 * FRAME          # all the speech frames were transcribed


def test_stt_routes_clear_speech_to_fast_engine_and_quiet_to_whisper():
    from max_assistant.stt import SpeechToText, snr_db

    rng = np.random.default_rng(0)
    noise = lambda n, db: rng.normal(0, 10 ** (db / 20), n)
    tone_ = lambda n, db: np.sin(np.arange(n) / 5) * 10 ** (db / 20)
    clear = np.concatenate([noise(8000, -60), tone_(16000, -6) + noise(16000, -60), noise(8000, -60)]).astype(np.float32)
    murky = np.concatenate([noise(8000, -40), tone_(16000, -38) + noise(16000, -40), noise(8000, -40)]).astype(np.float32)
    assert snr_db(clear) > 40 and snr_db(murky) < 12

    stt = SpeechToText.__new__(SpeechToText)       # skip loading real models
    used = []
    stt.fast = type("F", (), {"transcribe": lambda self, a: used.append("fast") or "Open YouTube."})()
    stt.fast_min_snr_db = 12.0
    stt.whisper = lambda a: used.append("whisper") or "open youtube"
    assert stt.transcribe(clear) == "Open YouTube." and used == ["fast"]
    assert stt.transcribe(murky) == "open youtube" and used == ["fast", "whisper"]
    stt.fast = type("F", (), {"transcribe": lambda self, a: ""})()   # fast engine heard nothing
    assert stt.transcribe(clear) == "open youtube"                   # -> Whisper double-checks


def test_listen_retranscribes_everything_when_it_still_sounds_unfinished():
    from max_assistant.listen import listen_for_command

    seen = []

    def transcribe(audio):
        seen.append(len(audio))
        return "search for arctic monkeys on..." if len(seen) == 1 else "search for arctic monkeys on YouTube"

    frames = speech(10) + [tone(10)] * 40
    text = listen_for_command(FakeMic(frames), NoiseFloor(100), transcribe, silence_s=1.0, early_s=0.4, max_silence_s=1.8)
    assert text == "search for arctic monkeys on YouTube"
    assert len(seen) == 2 and seen[1] > seen[0]      # second pass covered the quiet tail too


# ---------- follow-up conversation ----------

def run_converse(first, replies, follow_ups, follow_up=True):
    from max_assistant.main import converse

    replies, follow_ups, said, idle = iter(replies), iter(follow_ups), [], []

    def respond(text):
        said.append(text)
        return next(replies) if text is not None else None

    n = converse(first, respond, lambda: next(follow_ups), follow_up=follow_up, on_idle=lambda: idle.append(1))
    return n, said, idle


def test_follow_up_after_a_question_without_wake_word():
    n, said, idle = run_converse("search for something",
                                 ["What should I search for?", "Searching Google for lofi music."],
                                 ["lofi music"])
    assert said == ["search for something", "lofi music"] and n == 2


def test_no_follow_up_after_a_statement():
    n, said, _ = run_converse("open youtube", ["Opened youtube.com."], [])   # follow_ups would raise if used
    assert n == 1


def test_follow_up_ends_on_silence_or_phantom_text():
    n, said, idle = run_converse("play something", ["Which song?"], [""])
    assert n == 1 and idle == [1]                      # silence -> back to waiting for "Hey Max"
    n, said, idle = run_converse("play something", ["Which song?"], ["Thank you."])
    assert n == 1 and idle == [1]                      # classic phantom transcript ignored


def test_follow_ups_chain_and_can_be_turned_off():
    n, said, _ = run_converse("set a reminder", ["For when?", "What should it say?", "Noted."],
                              ["tomorrow", "call mom"])
    assert said == ["set a reminder", "tomorrow", "call mom"] and n == 3
    n, said, _ = run_converse("search", ["What for?"], [], follow_up=False)
    assert n == 1


def test_never_mind_in_a_follow_up():
    n, said, _ = run_converse("search", ["What should I search for?"], ["never mind"])
    assert said == ["search", None] and n == 1        # None = the "Okay." path


def test_snr_not_fooled_by_noise_gated_mic():
    from max_assistant.stt import snr_db

    gated = np.concatenate([np.zeros(8000), np.sin(np.arange(16000) / 5) * 0.3, np.zeros(8000)]).astype(np.float32)
    assert snr_db(gated) < 70            # digital-zero "silence" no longer means a perfect recording


# ---------- memory & reminders ----------

class WordEmbedder:
    """Deterministic stand-in for the real embedder: hashed bag of words, normalised."""

    def embed(self, texts):
        import re as _re
        out = np.zeros((len(texts), 256), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in _re.findall(r"[a-z0-9]+", t.lower()):
                if w not in {"my", "is", "the", "a", "on", "what", "when", "s"}:
                    out[i, hash(w) % 256] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


def memory_store():
    from max_assistant.memory import MemoryStore
    return MemoryStore(":memory:", WordEmbedder())


def test_memory_facts_search_dedupe_delete():
    m = memory_store()
    fid, old = m.add_fact("My CSE 572 exam is on Friday October 2.")
    assert old is None
    m.add_fact("My sister's name is Priya")
    hits = m.search_facts("when is my CSE 572 exam", k=1)
    assert hits[0].text == "My CSE 572 exam is on Friday October 2"
    _, old = m.add_fact("My CSE 572 exam is on Friday October 2")        # same fact again
    assert old is not None and len(m.facts()) == 2
    assert m.delete_fact(fid) and len(m.facts()) == 1
    assert not m.search_facts("CSE 572 exam", min_score=0.5)


def test_memory_turns_and_actions():
    m = memory_store()
    m.log_turn("search for arctic monkeys", "Searching YouTube for arctic monkeys.", ["open_website"])
    m.log_turn("set volume to 30", "Volume set to about 30 percent.", ["volume"])
    assert m.search_turns("arctic monkeys", k=1)[0].text.startswith("you: search for arctic monkeys")
    assert [t["tools"] for t in m.recent_turns()] == [["open_website"], ["volume"]]
    m.log_action("tool", {"tool": "volume", "result": "ok"})
    assert m.actions()[-1]["detail"]["tool"] == "volume"


def test_context_for_only_attaches_relevant_facts():
    m = memory_store()
    m.add_fact("My CSE 572 exam is on Friday October 2")
    assert "exam" in (m.context_for("CSE 572 exam Friday October") or "")
    assert m.context_for("open youtube") is None


def test_parse_when_phrases():
    import datetime as dt
    from max_assistant.reminders import parse_when, spoken_time

    now = dt.datetime(2026, 9, 30, 0, 45)          # Wednesday, 12:45 AM
    cases = {"Friday at 9am": (2026, 10, 2, 9, 0), "friday": (2026, 10, 2, 9, 0),
             "tomorrow morning": (2026, 10, 1, 9, 0), "tonight at 9": (2026, 9, 30, 21, 0),
             "next Monday 8am": (2026, 10, 5, 8, 0), "in 2 hours": (2026, 9, 30, 2, 45),
             "Thursday evening": (2026, 10, 1, 18, 0), "2026-10-02T09:00": (2026, 10, 2, 9, 0)}
    for phrase, expect in cases.items():
        assert parse_when(phrase, now) == dt.datetime(*expect), phrase
    assert parse_when("gibberish zzz", now) is None
    assert spoken_time(dt.datetime(2026, 10, 1, 18, 0), now) == "tomorrow at 6 PM"
    assert spoken_time(dt.datetime(2026, 10, 2, 9, 30), now) == "Friday, October 2 at 9:30 AM"


def test_reminder_scheduler_fires_due_and_flags_missed():
    import datetime as dt
    from max_assistant.reminders import ReminderScheduler, Reminders

    r = Reminders(memory_store())
    now = dt.datetime.now()
    r.add("stretch", now - dt.timedelta(seconds=30))       # just due
    r.add("exam", now - dt.timedelta(hours=3))             # came due while Max was off
    r.add("later", now + dt.timedelta(hours=1))
    fired = []
    ReminderScheduler(r, lambda rem, missed: fired.append((rem.text, missed))).check(now)
    assert sorted(fired) == [("exam", True), ("stretch", False)]
    assert [x.text for x in r.pending()] == ["later"]      # fired ones aren't repeated


def memory_registry(confirm_answer=True):
    from max_assistant.reminders import Reminders
    from max_assistant.tools import memory as memory_tools

    ctx = Context(load_config(), FakeLLM([]))
    ctx.memory = memory_store()
    ctx.reminders = Reminders(ctx.memory)
    ctx.confirm = lambda p: confirm_answer
    reg = ToolRegistry(context=ctx)
    memory_tools.register(reg)
    return reg, ctx


def test_memory_tools_end_to_end():
    reg, ctx = memory_registry()
    assert reg.run("remember", {"fact": "My CSE 572 exam is on Friday October 2"}) == "Got it, I'll remember that."
    out = reg.run("recall", {"query": "when is my CSE 572 exam"})
    assert "Friday October 2" in out
    assert reg.run("forget", {"what": "CSE 572 exam"}) == "Forgotten."
    assert reg.run("recall", {"query": "CSE 572 exam"}) == "Nothing saved about that."


def test_reminder_tools():
    reg, ctx = memory_registry()
    out = reg.run("set_reminder", {"text": "CSE 572 exam", "when": "tomorrow at 9am"})
    assert out.startswith("Okay, I'll remind you tomorrow at 9 AM")
    assert reg.run("set_reminder", {"text": "x", "when": "blorp"}).startswith("Error")
    reg.run("set_reminder", {"text": "call mom", "when": "in 2 hours"})
    assert reg.run("list_reminders", {}).startswith("You have 2 reminders")
    assert reg.run("cancel_reminder", {"which": "the exam"}) == "Cancelled the reminder: CSE 572 exam."
    assert [r.text for r in ctx.reminders.pending()] == ["call mom"]


def test_agent_attaches_memories_and_logs_turns(tmp_path):
    logged = []
    llm = FakeLLM([ChatReply("", [ToolCall("take_note", {"text": "x"})])])
    agent = Agent(llm, make_registry(tmp_path), "fast", "planner",
                  recall=lambda t: "Manas's exam is on Friday" if "exam" in t else None,
                  on_turn=lambda u, a, tools: logged.append((u, a, tools)))
    agent.handle("note about my exam")
    assert "(You remember: Manas's exam is on Friday)" in llm.calls[0]["messages"][-1]["content"]
    assert logged == [("note about my exam", "Noted.", ["take_note"])]


# ---------- dashboard API ----------

def api_client(run_command=lambda text: f"echo: {text}"):
    from fastapi.testclient import TestClient

    from max_assistant.events import ApprovalBroker, EventBus
    from max_assistant.reminders import Reminders
    from max_assistant.server import Runtime, create_app

    ctx = Context(load_config(), FakeLLM([]))
    ctx.memory = memory_store()
    ctx.reminders = Reminders(ctx.memory)
    bus = EventBus()
    approvals = ApprovalBroker(bus)
    rt = Runtime(ctx, bus, approvals, run_command, {"name": "Max"})
    return TestClient(create_app(rt)), rt


def test_api_facts_reminders_and_commands():
    client, rt = api_client()
    assert client.post("/api/facts", json={"text": "My sister's name is Priya"}).json()["updated"] is False
    facts = client.get("/api/facts").json()
    assert [f["text"] for f in facts] == ["My sister's name is Priya"]
    assert client.delete(f"/api/facts/{facts[0]['id']}").json() == {"ok": True}
    r = client.post("/api/reminders", json={"text": "exam", "when": "tomorrow at 9am"}).json()
    assert r["spoken"] == "tomorrow at 9 AM"
    assert [x["text"] for x in client.get("/api/reminders").json()] == ["exam"]
    assert client.post("/api/reminders", json={"text": "x", "when": "blorp"}).status_code == 400
    assert client.post("/api/command", json={"text": "open youtube"}).json() == {"reply": "echo: open youtube"}
    assert [e["kind"] for e in rt.bus.recent][-1] == "reminder"


def test_api_approvals_race_and_websocket():
    client, rt = api_client()
    a = rt.approvals.open("Shut down the laptop?", "power")
    assert client.get("/api/state").json()["approvals"] == [{"id": a.id, "prompt": "Shut down the laptop?", "tool": "power"}]
    assert client.post(f"/api/approvals/{a.id}", json={"approved": True}).json() == {"ok": True}
    assert a.done.is_set() and a.approved is True
    rt.approvals.close(a, True, "dashboard")
    assert client.post(f"/api/approvals/{a.id}", json={"approved": False}).status_code == 404
    with client.websocket_connect("/api/ws") as ws:
        hello = ws.receive_json()
        assert hello["kind"] == "hello" and any(e["kind"] == "approval_result" for e in hello["data"]["recent"])
        rt.bus.publish("stage", {"stage": "thinking"})
        assert ws.receive_json()["data"] == {"stage": "thinking"}


def test_forget_requests_skip_the_model_and_still_confirm():
    from max_assistant.agent import FORGET

    assert FORGET.match("could you forget my exam").group(1) == "my exam"
    assert FORGET.match("can you forget what I told you about lofi").group(1) == "lofi"
    assert FORGET.match("I forgot my keys") is None and FORGET.match("don't forget to call mom") is None
    reg, ctx = memory_registry(confirm_answer=False)
    ctx.memory.add_fact("Manas prefers lofi music when studying")
    llm = FakeLLM([])                                   # would raise if the model were asked
    agent = Agent(llm, reg, "fast", "planner")
    assert agent.handle("forget the lofi music when studying") == "Okay, I won't."
    assert len(ctx.memory.facts()) == 1 and llm.calls == []


def test_false_claim_gets_one_nudge():
    reg, ctx = memory_registry()
    llm = FakeLLM([ChatReply("Okay, I'll remind you tomorrow."),               # claims, no tool
                   ChatReply("", [ToolCall("set_reminder", {"text": "gym", "when": "tomorrow at 7am"})])])
    agent = Agent(llm, reg, "fast", "planner")
    assert agent.handle("remind me about gym tomorrow at 7").startswith("Okay, I'll remind you tomorrow at 7 AM")
    assert "System check" in llm.calls[1]["messages"][-1]["content"]
    assert [r.text for r in ctx.reminders.pending()] == ["gym"]


# ---------- Canvas & daily digest ----------

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:event-assignment-1
DTSTART;VALUE=DATE:20261001
SUMMARY:Lab 4 [2026FallC-T-CSE572-68549]
END:VEVENT
BEGIN:VEVENT
UID:event-assignment-2
DTSTART;VALUE=DATE:20261004
SUMMARY:Assignment1 [2026FallC-T-CSE579-73246]
END:VEVENT
BEGIN:VEVENT
UID:event-calendar-event-10
DTSTART:20261001T120000Z
DTEND:20261001T131500Z
SUMMARY:CSE 573: Semantic Web Mining (2026 Fall C) [2026FallC-T-CSE573-82398]
DESCRIPTION:[Click here to join Zoom Meeting] (https://asu.zoom.us/j/123)
END:VEVENT
BEGIN:VEVENT
UID:event-calendar-event-11
DTSTART:20261001T120000Z
DTEND:20261001T131500Z
SUMMARY:CSE 573: Semantic Web Mining (2024 Spring) [2026FallC-T-CSE573-82398]
END:VEVENT
END:VCALENDAR
"""


def test_canvas_feed_parsing():
    from max_assistant.canvas import describe_due, parse_feed

    items = parse_feed(ICS)
    assignments = [i for i in items if i.kind == "assignment"]
    classes = [i for i in items if i.kind == "class"]
    assert [(a.title, a.course, a.all_day) for a in assignments] == [("Lab 4", "CSE 572", True), ("Assignment1", "CSE 579", True)]
    assert len(classes) == 1                                  # other section's copy dropped
    assert classes[0].course == "CSE 573" and classes[0].link == "https://asu.zoom.us/j/123"
    assert describe_due(assignments[:1], "today") == "Due today: Lab 4 for CSE 572, by end of day."
    assert describe_due([], "tomorrow") == "Nothing due tomorrow on Canvas."


class FakeFeed:
    def __init__(self, items):
        self._items = items

    def between(self, start, end, kind=None):
        return [i for i in self._items if start <= i.start < end and (kind is None or i.kind == kind)]


def digest_ctx(tmp_items=None):
    import datetime as dt
    from max_assistant.canvas import parse_feed
    from max_assistant.reminders import Reminders

    ctx = Context(load_config(), FakeLLM([]))
    ctx.memory = memory_store()
    ctx.reminders = Reminders(ctx.memory)
    ctx.canvas = FakeFeed(parse_feed(ICS) if tmp_items is None else tmp_items)
    ctx.reminders.add("stand-up", dt.datetime(2026, 10, 1, 10, 0))
    return ctx


def test_digest_content_and_spoken_summary():
    import datetime as dt
    from max_assistant.digest import gather, render, spoken

    plan = gather(digest_ctx(), dt.date(2026, 10, 1))
    assert [i.title for i in plan.due_today] == ["Lab 4"] and [i.title for i in plan.due_soon] == ["Assignment1"]
    assert len(plan.classes) == 1 and [r.text for r in plan.reminders] == ["stand-up"]
    subject, text, body = render(plan, "Focus on Lab 4 first.")
    assert subject == "Your day, Thu Oct 1: 1 due today"
    assert "Lab 4 (CSE 572) by end of day" in text and "Zoom: https://asu.zoom.us/j/123" in text
    assert "Focus on Lab 4 first." in body and "<li" in body
    s = spoken(plan)
    assert s.startswith("Due today: Lab 4 for CSE 572.") and "Coming up: Assignment1" in s


def test_digest_schedule_catch_up_and_once_a_day():
    import datetime as dt
    from max_assistant.digest import DigestScheduler

    ctx = digest_ctx()
    sched = DigestScheduler(ctx, "07:00", weekends=False, catch_up_until="18:00")
    thu = dt.datetime(2026, 10, 1, 6, 59)
    assert not sched.due(thu)                                       # too early
    assert sched.due(thu.replace(hour=9))                           # late start: catch up
    assert not sched.due(thu.replace(hour=19))                      # too late to be useful
    assert not sched.due(dt.datetime(2026, 10, 3, 8, 0))            # Saturday, weekends off
    ctx.memory.set("digest_last_sent", "2026-10-01")
    assert not sched.due(thu.replace(hour=9))                       # already sent today


def test_digest_refuses_placeholder_email_settings():
    from max_assistant.digest import send_email

    with pytest.raises(RuntimeError, match="isn't set up"):
        send_email({"smtp_user": "your.address@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}, "s", "t", "<p>h</p>")
