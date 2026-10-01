"""Phone access (Phase 4): the pairing token, who counts as "local", audio in/out, pairing QR.

The API stays bound to 127.0.0.1. The phone reaches it through `tailscale serve`, which
proxies https://<laptop>.<tailnet>.ts.net to 127.0.0.1:8765 and adds forwarding headers.
So a request is local only if it comes from loopback *without* those headers; everything
else must present the token (Authorization: Bearer <token>, or ?token= for WebSockets).
"""
from __future__ import annotations

import hmac
import io
import json
import logging
import secrets
import shutil
import subprocess
import wave
from pathlib import Path
from urllib.parse import quote

import numpy as np

from .config import ROOT

log = logging.getLogger(__name__)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = ("x-forwarded-for", "tailscale-user-login", "x-forwarded-host", "forwarded")


def load_token(path: str | Path = "data/phone_token.txt") -> str:
    """The phone's access token: created once, kept in data/ (git-ignored)."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if p.exists():
        token = p.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
    p.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    p.write_text(token, encoding="utf-8")
    return token


def is_local(client_host: str | None, headers) -> bool:
    if client_host not in LOOPBACK:
        return False
    return not any(h in headers for h in PROXY_HEADERS)


def token_ok(expected: str, headers, query) -> bool:
    got = ""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    got = got or query.get("token", "")
    return bool(got) and hmac.compare_digest(got.encode(), expected.encode())


# ----- audio -----

def decode_wav(data: bytes) -> np.ndarray:
    """WAV bytes (PCM16, any rate, mono or stereo) -> float32 mono 16 kHz."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM WAV")
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != 16000 and len(x):
        t = np.arange(0, len(x) / sr, 1 / 16000)
        x = np.interp(t, np.arange(len(x)) / sr, x).astype(np.float32)
    return x


def encode_wav(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# ----- pairing -----

def tailscale_url() -> str | None:
    """https://<machine>.<tailnet>.ts.net if Tailscale is installed and logged in."""
    exe = shutil.which("tailscale") or next((p for p in (r"C:\Program Files\Tailscale\tailscale.exe",)
                                             if Path(p).exists()), None)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=4,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        return f"https://{name}" if name else None
    except Exception as exc:
        log.debug("tailscale status failed: %s", exc)
        return None


def pairing_link(url: str, token: str) -> str:
    return f"max://pair?url={quote(url, safe='')}&token={quote(token, safe='')}"


def qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")
