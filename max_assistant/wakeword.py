"""Wake-word detection (runs on CPU, always on). Two engines behind one interface:

- sherpa:       sherpa-onnx open-vocabulary keyword spotting. Type any English phrase
                ("Hey Max") in config; no training needed.
- openwakeword: openWakeWord, a built-in model name (hey_jarvis, alexa...) or a path to a
                custom-trained .onnx file.

If the configured engine can't load, it falls back to openWakeWord's 'hey_jarvis' so the
assistant still wakes up instead of crashing.
"""
from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import resolve_path

log = logging.getLogger(__name__)

FALLBACK_MODEL = "hey_jarvis"
MODEL_SUFFIXES = (".onnx", ".tflite")


# ---------------- shared ----------------

class WakeDetector:
    """Common cooldown logic. Subclasses implement _detect(frame) -> bool."""

    engine = ""
    phrase = ""
    threshold = 0.0
    last_score: float | None = None   # openWakeWord gives a live score; sherpa doesn't

    def __init__(self, cooldown_s: float = 2.0):
        self.cooldown_s = cooldown_s
        self._last_fire = 0.0

    def process(self, frame: np.ndarray) -> bool:
        """Feed one 80 ms int16 frame. Returns True when the wake word is heard."""
        if not self._detect(frame):
            return False
        now = time.monotonic()
        if now - self._last_fire <= self.cooldown_s:
            return False
        self._last_fire = now
        log.info("wake word detected (%s)", self.engine)
        self.reset()
        return True

    def _detect(self, frame: np.ndarray) -> bool:
        raise NotImplementedError

    def reset(self):
        """Clear internal audio buffers so old audio can't re-trigger it."""

    def describe(self) -> str:
        return f"{self.engine}, threshold {self.threshold}"


# ---------------- sherpa-onnx keyword spotting ----------------

def normalize_phrase(phrase: str) -> str:
    """'Hey, Max!' -> 'HEY MAX' (the English KWS model uses upper-case BPE)."""
    return " ".join(re.sub(r"[^A-Za-z' ]+", " ", phrase).upper().split())


def keyword_line(phrase: str, encode, valid_tokens: set[str], boost: float, threshold: float) -> str:
    """Build one keywords-file line, e.g. '▁HE Y ▁MA X :1.0 #0.25 @HEY_MAX'.
    `encode` turns text into BPE pieces (sentencepiece)."""
    text = normalize_phrase(phrase)
    if not text:
        raise ValueError("wake word phrase is empty")
    pieces = encode(text)
    unknown = [p for p in pieces if p not in valid_tokens]
    if unknown:
        raise ValueError(f"phrase {phrase!r} has tokens the model doesn't know: {unknown}")
    return f"{' '.join(pieces)} :{boost} #{threshold} @{text.replace(' ', '_')}"


class AutoGain:
    """Boosts quiet audio toward a target peak level; never turns loud audio down.
    The gain follows the loudest sample of the last ~1.5 s (including the current
    frame, so a sudden loud word is never clipped)."""

    def __init__(self, max_gain: float = 8.0, target_peak: float = 16000, window_frames: int = 19):
        self.max_gain = max_gain
        self.target_peak = target_peak
        self._peaks: deque[int] = deque(maxlen=window_frames)
        self.gain = 1.0

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        self._peaks.append(int(np.abs(frame.astype(np.int32)).max()) if len(frame) else 0)
        loudest = max(max(self._peaks), 1)
        self.gain = min(self.max_gain, max(1.0, self.target_peak / loudest))
        return np.clip(frame.astype(np.float32) * self.gain, -32768, 32767)


class SherpaKeywordDetector(WakeDetector):
    """sherpa-onnx keyword spotting, made robust with two tricks (measured, see tests):

    - Staggered streams: the model decodes audio in fixed chunks and misses the phrase
      when it straddles a chunk boundary badly (~1 in 5 calls). Several streams started
      a fraction of a chunk apart cover every alignment; any stream can fire.
    - Auto gain: quiet speech is boosted before decoding, loud speech left alone.
    """

    engine = "sherpa"
    CHUNK_S = 0.64   # decoding chunk of the chunk-16 zipformer (16 frames x 4 subsampling x 10 ms)

    def __init__(self, phrase: str, model_dir: str, threshold: float = 0.25, boost: float = 1.0,
                 cooldown_s: float = 1.0, streams: int = 4, max_gain: float = 8.0):
        super().__init__(cooldown_s)
        import sentencepiece as spm
        import sherpa_onnx

        d = resolve_path(model_dir)
        if not d.is_dir():
            raise FileNotFoundError(f"keyword spotting model not found at {d}")

        def pick(prefix: str) -> str:
            # Prefer the int8 files: ~3x smaller and faster, same accuracy for this job
            files = sorted(d.glob(f"{prefix}*.onnx"), key=lambda f: ".int8." not in f.name)
            if not files:
                raise FileNotFoundError(f"no {prefix}*.onnx in {d}")
            return str(files[0])

        tokens = d / "tokens.txt"
        valid = {line.split()[0] for line in tokens.read_text(encoding="utf-8").splitlines() if line.strip()}
        sp = spm.SentencePieceProcessor(model_file=str(d / "bpe.model"))
        line = keyword_line(phrase, lambda t: sp.encode(t, out_type=str), valid, boost, threshold)

        # sherpa-onnx reads keywords from a file; keep a generated copy next to the logs
        kw_file = resolve_path("data/wake_keywords.txt")
        kw_file.parent.mkdir(parents=True, exist_ok=True)
        kw_file.write_text(line + "\n", encoding="utf-8")

        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(tokens), encoder=pick("encoder"), decoder=pick("decoder"), joiner=pick("joiner"),
            keywords_file=str(kw_file), num_threads=1, keywords_threshold=threshold, keywords_score=boost,
        )
        self.phrase = phrase
        self.threshold = threshold
        self.num_streams = max(1, int(streams))
        self._stagger = int(16000 * self.CHUNK_S / self.num_streams)   # samples between stream starts
        self.agc = AutoGain(max_gain) if max_gain and max_gain > 1 else None
        self.reset()
        log.info("wake word: sherpa keyword spotting for %r (%s, %d streams, max gain %s)",
                 phrase, d.name, self.num_streams, max_gain)

    def _detect(self, frame: np.ndarray) -> bool:
        x = self.agc(frame) if self.agc else frame.astype(np.float32)
        x = x / 32768.0
        pos = self._fed              # sample position where this frame starts
        self._fed += len(frame)
        hit = False
        for k, s in enumerate(self.streams):
            if s is None:
                if pos < k * self._stagger:
                    continue
                s = self.streams[k] = self.spotter.create_stream()
            s.accept_waveform(16000, x)
            while self.spotter.is_ready(s):
                self.spotter.decode_stream(s)
                if self.spotter.get_result(s):
                    hit = True
                    self.spotter.reset_stream(s)
        return hit

    def reset(self):
        # Fresh streams drop buffered audio; they restart staggered on the next frames
        self.streams = [None] * self.num_streams
        self._fed = 0


# ---------------- openWakeWord ----------------

@dataclass
class WakeModel:
    source: str        # built-in name or absolute file path, as openWakeWord expects it
    custom: bool
    phrase: str        # what the user should say, for on-screen hints


def builtin_phrase(name: str) -> str:
    """'hey_jarvis' -> 'Hey Jarvis'."""
    return name.replace("_", " ").title()


def resolve_wake_model(model: str, phrase: str | None = None) -> WakeModel:
    """Work out which openWakeWord model to load. A missing custom file falls back to
    the built-in 'hey_jarvis'."""
    model = (model or FALLBACK_MODEL).strip()
    if model.lower().endswith(MODEL_SUFFIXES):
        path = resolve_path(model)
        if path.is_file():
            return WakeModel(str(path), True, phrase or builtin_phrase(path.stem))
        log.warning("Custom wake word model not found at %s; falling back to '%s'", path, FALLBACK_MODEL)
        model = FALLBACK_MODEL
    return WakeModel(model, False, builtin_phrase(model))


class WakeWordDetector(WakeDetector):
    engine = "openwakeword"

    def __init__(self, model: str = FALLBACK_MODEL, threshold: float = 0.5, cooldown_s: float = 2.0,
                 phrase: str | None = None):
        super().__init__(cooldown_s)
        import openwakeword
        from openwakeword.model import Model

        self.wake_model = resolve_wake_model(model, phrase)
        self.phrase = self.wake_model.phrase
        try:
            # Always fetches the shared melspectrogram/embedding models too. For a custom
            # model, grab the small fallback (an empty list would download every model).
            openwakeword.utils.download_models([FALLBACK_MODEL if self.wake_model.custom else self.wake_model.source])
        except TypeError:  # older versions take no argument
            openwakeword.utils.download_models()
        except Exception as exc:
            log.warning("Could not download wake word models (offline?): %s", exc)

        self.model = Model(wakeword_models=[self.wake_model.source], inference_framework="onnx")
        log.info("wake word: openWakeWord %s (%s)", self.wake_model.source,
                 "custom" if self.wake_model.custom else "built-in")
        self.threshold = threshold
        self.last_score = 0.0

    def _detect(self, frame: np.ndarray) -> bool:
        scores = self.model.predict(frame)
        self.last_score = float(max(scores.values())) if scores else 0.0
        return self.last_score >= self.threshold

    def reset(self):
        try:
            self.model.reset()
        except AttributeError:
            pass

    def describe(self) -> str:
        kind = "custom" if self.wake_model.custom else "built-in"
        return f"openwakeword {self.wake_model.source} ({kind}), threshold {self.threshold}"


# ---------------- factory ----------------

def make_detector(w) -> WakeDetector:
    """Build the detector from the `wake_word` config section."""
    engine = (w.get("engine") or "openwakeword").lower()
    cooldown = w.get("cooldown_s", 1.0)
    oww = w.get("openwakeword") or {}
    if engine == "sherpa":
        s = w.get("sherpa") or {}
        try:
            return SherpaKeywordDetector(w.get("phrase") or "Hey Max",
                                         s.get("model_dir", "models/kws/gigaspeech-3.3M"),
                                         s.get("threshold", 0.25), s.get("boost", 1.0), cooldown,
                                         s.get("streams", 4), s.get("max_gain", 8.0))
        except Exception as exc:
            log.warning("sherpa wake word unavailable (%s); falling back to openWakeWord '%s'",
                        exc, FALLBACK_MODEL)
            return WakeWordDetector(FALLBACK_MODEL, oww.get("threshold", 0.5), cooldown)
    # Older configs kept model/threshold directly under wake_word
    return WakeWordDetector(oww.get("model", w.get("model", FALLBACK_MODEL)),
                            oww.get("threshold", w.get("threshold", 0.5)), cooldown,
                            phrase=w.get("phrase"))
