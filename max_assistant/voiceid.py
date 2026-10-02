"""Voice ID: does this spoken command sound like the enrolled user?

A speaker-embedding model (ERes2Net trained on VoxCeleb, sherpa-onnx, CPU, ~100 ms per command)
turns a recording into a voiceprint: a few hundred numbers that describe the voice, not the words.
Enrolment averages the voiceprints of a few sentences into data/voice_profile.npy (no audio
is kept). Each command is compared with it (cosine similarity). Risky actions asked for in a
voice that doesn't match need an Approve tap on the phone instead of a spoken "yes".
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .config import resolve_path

log = logging.getLogger(__name__)
SR = 16000


class VoiceID:
    def __init__(self, model: str | Path = "models/voiceid/eres2net_en_voxceleb.onnx",
                 profile: str | Path = "data/voice_profile.npy", threshold: float = 0.42, min_seconds: float = 0.8):
        import sherpa_onnx

        path = resolve_path(model)
        if not path.exists():
            raise FileNotFoundError(f"voice ID model not found at {path} (run setup.ps1)")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=1))
        self.profile_path = resolve_path(profile)
        self.threshold = threshold
        self.min_seconds = min_seconds
        self.profile = np.load(self.profile_path) if self.profile_path.exists() else None

    @property
    def enrolled(self) -> bool:
        return self.profile is not None

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """float32 16 kHz mono audio -> unit-length voiceprint."""
        x = audio.astype(np.float32)
        if x.dtype != np.float32 or np.abs(x).max() > 1.5:
            x = x / 32768.0
        stream = self.extractor.create_stream()
        stream.accept_waveform(SR, x)
        stream.input_finished()
        v = np.asarray(self.extractor.compute(stream), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def score(self, audio: np.ndarray) -> float | None:
        """Similarity to the enrolled voice (measured: 0.63+ for the user, 0.21 or less for others),
        or None when there's no profile or too little speech to judge."""
        if self.profile is None or len(audio) < self.min_seconds * SR:
            return None
        return float(self.embed(audio) @ self.profile)

    def is_owner(self, audio: np.ndarray) -> bool | None:
        s = self.score(audio)
        if s is None:
            return None
        log.info("voice ID: similarity %.2f (threshold %.2f)", s, self.threshold)
        return s >= self.threshold

    def enroll(self, recordings: list[np.ndarray]) -> np.ndarray:
        good = [r for r in recordings if len(r) >= self.min_seconds * SR]
        if len(good) < 3:
            raise ValueError("need at least 3 clear recordings of a second or more")
        mean = np.mean([self.embed(r) for r in good], axis=0)
        self.profile = mean / np.linalg.norm(mean)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.profile_path, self.profile)
        return self.profile

    def forget(self):
        self.profile = None
        self.profile_path.unlink(missing_ok=True)
