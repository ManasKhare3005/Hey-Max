"""Text-to-speech: Piper (natural, offline) with the built-in Windows voice as fallback."""
from __future__ import annotations

import io
import logging
import re
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

    def __init__(self, engine: str = "piper", voice_path: str = "", speed: float = 1.0, device=None, mic=None):
        self.device = device
        self.mic = mic
        self.piper = None
        self.sapi = None
        if engine == "piper":
            try:
                self.piper = PiperTTS(voice_path, speed)
            except Exception as exc:
                log.warning("Piper unavailable (%s); falling back to Windows voice", exc)
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
