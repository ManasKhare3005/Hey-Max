"""Speech-to-text, offline. Two engines, picked per recording:

- Moonshine (sherpa-onnx): ~0.2 s for a short command. Its cost scales with the length of
  what you said; Whisper always processes a fixed 30 s window (~1.8 s on this laptop).
- Whisper small.en (faster-whisper): much better on quiet or noisy speech.

Measured on test commands: equal accuracy down to ~10 dB signal-to-noise, then Moonshine
falls apart (42% word errors vs 15% at ~5 dB). So clear recordings go to Moonshine and
quiet/noisy ones to Whisper.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from .config import resolve_path

log = logging.getLogger(__name__)


def snr_db(audio: np.ndarray, frame: int = 1280) -> float:
    """Rough signal-to-noise of a recording: loud 80 ms frames vs quiet ones. Recordings
    always include some lead-in/trailing silence, so both ends are present."""
    n = len(audio) // frame
    if n < 5:
        return 0.0
    energy = np.sqrt((audio[: n * frame].reshape(n, frame).astype(np.float64) ** 2).mean(axis=1)) + 1e-9
    return float(20 * np.log10(np.percentile(energy, 90) / np.percentile(energy, 10)))


class Moonshine:
    def __init__(self, model_dir: str | Path, threads: int = 4):
        import sherpa_onnx

        d = resolve_path(model_dir)
        if not (d / "tokens.txt").exists():
            raise FileNotFoundError(f"Moonshine model not found in {d}")
        self.rec = sherpa_onnx.OfflineRecognizer.from_moonshine(
            preprocessor=str(d / "preprocess.onnx"), encoder=str(d / "encode.int8.onnx"),
            uncached_decoder=str(d / "uncached_decode.int8.onnx"), cached_decoder=str(d / "cached_decode.int8.onnx"),
            tokens=str(d / "tokens.txt"), num_threads=threads)

    def transcribe(self, audio: np.ndarray) -> str:
        s = self.rec.create_stream()
        s.accept_waveform(16000, audio)
        self.rec.decode_stream(s)
        return s.result.text.strip()


class SpeechToText:
    def __init__(self, model: str = "small.en", device: str = "cpu", compute_type: str = "int8",
                 fast_model_dir: str | None = None, fast_min_snr_db: float = 12.0):
        from faster_whisper import WhisperModel

        log.info("loading whisper %s on %s (%s)", model, device, compute_type)
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.fast = None
        self.fast_min_snr_db = fast_min_snr_db
        if fast_model_dir:
            try:
                self.fast = Moonshine(fast_model_dir)
                log.info("fast speech-to-text: Moonshine (%s) for clear speech", Path(fast_model_dir).name)
            except Exception as exc:
                log.warning("Moonshine unavailable (%s); using Whisper only", exc)

    def whisper(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        start = time.perf_counter()
        snr = snr_db(audio)
        engine, text = "whisper", ""
        if self.fast is not None and snr >= self.fast_min_snr_db:
            engine, text = "moonshine", self.fast.transcribe(audio)
        if not text:
            engine, text = "whisper", self.whisper(audio)
        log.info("heard (%s, %.1f dB, %.2fs): %r", engine, snr, time.perf_counter() - start, text)
        return text
