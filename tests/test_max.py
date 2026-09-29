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
from max_assistant.tools.registry import ToolRegistry
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
