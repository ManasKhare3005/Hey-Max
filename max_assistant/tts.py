"""Text-to-speech: Piper (offline), or a Microsoft natural voice (e.g. Ava) with Piper as fallback;
the built-in Windows voice if neither works."""
from __future__ import annotations

import io
import logging
import re
import threading
import wave

import numpy as np

from .config import resolve_path

log = logging.getLogger(__name__)


def clean_for_speech(text: str) -> str:
    """Strip markdown and symbols that sound awful when read aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"[^\w\s.,!?'’:;%$°/()-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class PiperTTS:
    def __init__(self, voice_path: str, speed: float = 1.0):
        from piper import PiperVoice

        path = resolve_path(voice_path)
        if not path.exists():
            raise FileNotFoundError(f"Piper voice not found at {path}. Run setup.ps1 to download it.")
        self.voice = PiperVoice.load(str(path))
        self.length_scale = 1.0 / max(speed, 0.1)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            if hasattr(self.voice, "synthesize_wav"):  # piper-tts >= 1.3
                try:
                    from piper import SynthesisConfig

                    cfg = SynthesisConfig(length_scale=self.length_scale)
                    self.voice.synthesize_wav(text, wav, syn_config=cfg)
                except ImportError:
                    self.voice.synthesize_wav(text, wav)
            else:  # piper-tts 1.2
                self.voice.synthesize(text, wav, length_scale=self.length_scale)
        buf.seek(0)
        with wave.open(buf, "rb") as wav:
            sr = wav.getframerate()
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        return data.astype(np.float32) / 32768.0, sr


def trim_silence(audio: np.ndarray, sr: int, threshold: float = 0.01, keep_s: float = 0.06) -> np.ndarray:
    """Cut the silence some voices pad around every sentence (gaps between streamed sentences)."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if not len(loud):
        return audio[:0]
    keep = int(keep_s * sr)
    return audio[max(0, loud[0] - keep): loud[-1] + keep]


class NaturalVoice:
    """Microsoft's natural voices (e.g. "Microsoft Ava Online") through Windows speech (SAPI),
    made available by the NaturalVoiceSAPIAdapter add-on. The "Online" voices send the text of
    each reply to Microsoft to be spoken; everything else stays on the laptop. Falls back to the
    local Piper voice when offline, slow, or broken."""

    def __init__(self, name: str = "Microsoft Ava Online", speed: float = 1.0, fallback=None, timeout_s: float = 5.0):
        import concurrent.futures as cf

        self.name = name
        self.rate = max(-10, min(10, round((speed - 1.0) * 10)))
        self.fallback = fallback
        self.timeout_s = timeout_s
        self._local = threading.local()
        self._pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="natural-voice")
        audio, _ = self._pool.submit(self._speak, "Hello.").result(timeout=20)   # fails now if the voice is missing
        if not len(audio):
            raise RuntimeError(f"{name} returned no audio")

    def _voice(self):
        v = getattr(self._local, "voice", None)
        if v is None:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()                       # COM objects belong to the thread that made them
            v = comtypes.client.CreateObject("SAPI.SpVoice")
            for tok in v.GetVoices():
                desc = tok.GetDescription()
                if desc == self.name or desc.startswith(self.name + " "):
                    v.Voice = tok
                    break
            else:
                raise RuntimeError(f"voice {self.name!r} not found (is NaturalVoiceSAPIAdapter installed?)")
            v.Rate = self.rate
            self._local.voice = v
        return v

    def _speak(self, text: str) -> tuple[np.ndarray, int]:
        import comtypes.client

        voice = self._voice()
        stream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
        fmt = comtypes.client.CreateObject("SAPI.SpAudioFormat")
        fmt.Type = 26                                     # 24 kHz, 16-bit, mono
        stream.Format = fmt
        voice.AudioOutputStream = stream
        voice.Speak(text)
        audio = np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0
        return trim_silence(audio, 24000), 24000

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        try:
            audio, sr = self._pool.submit(self._speak, text).result(timeout=self.timeout_s)
            if len(audio):
                return audio, sr
            log.warning("%s returned silence; using the local voice", self.name)
        except Exception as exc:
            log.warning("%s unavailable (%s); using the local voice", self.name, exc)
        if self.fallback is None:
            raise RuntimeError(f"{self.name} failed and there is no fallback voice")
        return self.fallback.synthesize(text)


def make_voice(cfg_tts):
    """The configured voice as an object with synthesize(text) -> (audio, sample_rate)."""
    piper = None
    try:
        piper = PiperTTS(cfg_tts.get("piper_voice", ""), cfg_tts.get("speed", 1.0))
    except Exception as exc:
        log.warning("Piper unavailable (%s)", exc)
    if (cfg_tts.get("engine") or "piper") == "natural":
        try:
            voice = NaturalVoice(cfg_tts.get("natural_voice", "Microsoft Ava Online"), cfg_tts.get("speed", 1.0), fallback=piper)
            log.info("voice: %s (falls back to Piper)", voice.name)
            return voice
        except Exception as exc:
            log.warning("natural voice unavailable (%s); using Piper", exc)
    return piper


class SapiTTS:
    """Windows built-in voice. Zero setup, more robotic."""

    def __init__(self, speed: float = 1.0):
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", int(185 * speed))

    def say(self, text: str):
        self.engine.say(text)
        self.engine.runAndWait()


class Speaker:
    """Speaks text, muting the microphone meanwhile so Max doesn't hear itself."""

    def __init__(self, engine: str = "piper", voice_path: str = "", speed: float = 1.0, device=None, mic=None,
                 natural_voice: str = "Microsoft Ava Online"):
        self.device = device
        self.mic = mic
        self.piper = None     # any voice with synthesize(text) -> (audio, rate): Piper or a natural voice
        self.sapi = None
        if engine in ("piper", "natural"):
            self.piper = make_voice({"engine": engine, "piper_voice": voice_path, "speed": speed,
                                     "natural_voice": natural_voice})
        if self.piper is None:
            self.sapi = SapiTTS(speed)

    interrupts = None   # wakeword.InterruptSpotter: lets "stop" / "Hey Max" cut Max off

    def say(self, text: str) -> str | None:
        """Speak `text`. Returns "stop" or "wake" if the user interrupted, else None."""
        text = clean_for_speech(text)
        if not text:
            return None
        if self.piper and self.mic and self.interrupts is not None:
            return self._say_interruptible(text)
        if self.mic:
            self.mic.muted.set()
        try:
            if self.piper:
                from .audio import play

                audio, sr = self.piper.synthesize(text)
                play(audio, sr, self.device)
            else:
                self.sapi.say(text)
        finally:
            if self.mic:
                self.mic.flush()
                self.mic.muted.clear()
        return None

    def _say_interruptible(self, text: str) -> str | None:
        """Play without blocking and keep the mic open for "stop" / "Hey Max" meanwhile."""
        import sounddevice as sd

        from .speech import watch_for_interrupt

        audio, sr = self.piper.synthesize(text)
        self.mic.flush()
        self.interrupts.reset()
        sd.play(audio, sr, device=self.device)
        try:
            heard = watch_for_interrupt(self.mic.read, self.interrupts, len(audio) / sr + 0.15, text)
        finally:
            if heard:
                sd.stop()
            else:
                sd.wait()
            self.mic.flush()
        return heard
