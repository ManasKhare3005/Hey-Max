"""Speech-to-text with faster-whisper (offline)."""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class SpeechToText:
    def __init__(self, model: str = "small.en", device: str = "cpu", compute_type: str = "int8"):
        from faster_whisper import WhisperModel

        log.info("loading whisper %s on %s (%s)", model, device, compute_type)
        self.model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("heard: %r", text)
        return text
