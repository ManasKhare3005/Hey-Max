"""Minimal Ollama client over HTTP (chat with tool calling, load/unload)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class OllamaError(RuntimeError):
    pass


def _calls(msg: dict) -> list[ToolCall]:
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append(ToolCall(fn.get("name", ""), args))
    return calls


class OllamaClient:
    def __init__(self, host: str, keep_alive: str = "15m", temperature: float = 0.3,
                 think: bool = False, timeout: float = 180, num_ctx: int | None = None,
                 num_predict: int | None = None, gpu_layers: dict[str, int] | None = None):
        self.host = host.rstrip("/")
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.think = think
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        # model -> layers to force onto the GPU (Ollama's num_gpu). Dropped for a model if
        # loading fails, e.g. when a game is holding VRAM.
        self.gpu_layers = dict(gpu_layers or {})

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             options: dict[str, Any] | None = None, on_text: Callable[[str], None] | None = None) -> ChatReply:
        """`options` overrides per call, e.g. {"num_predict": 1000} for long notes.
        `on_text` streams the reply: called with each piece of text as the model writes it
        (voice mode starts speaking the first sentence before the rest exists)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": on_text is not None,
            "keep_alive": self.keep_alive,
            "think": self.think,
            "options": {**self._options(model), **(options or {})},
        }
        if tools:
            payload["tools"] = tools
        extra = {"stream": True} if payload["stream"] else {}
        post = lambda: requests.post(f"{self.host}/api/chat", json=payload, timeout=self.timeout, **extra)
        try:
            r = post()
        except requests.ConnectionError as exc:
            raise OllamaError("Can't reach Ollama. Is it running? Start the Ollama app.") from exc
        if r.status_code == 404:
            raise OllamaError(f"Model '{model}' isn't downloaded. Run: ollama pull {model}")
        if r.status_code >= 400 and "think" in r.text and payload.pop("think", None) is not None:
            # Older Ollama or a model without thinking support: retry without the flag
            r = post()
        if r.status_code >= 500 and self.gpu_layers.pop(model, None) is not None:
            log.warning("Loading %s with all layers on the GPU failed (%s); retrying with Ollama's default split",
                        model, r.text[:200])
            payload["options"] = self._options(model)
            r = post()
        if r.status_code >= 400:
            raise OllamaError(f"Ollama error {r.status_code}: {r.text[:300]}")
        if on_text is None:
            data = r.json()
            msg = data.get("message", {})
            return ChatReply(content=(msg.get("content") or "").strip(), tool_calls=_calls(msg), raw=data)
        # Streamed: one JSON object per line; text arrives in pieces, tool calls whole
        parts: list[str] = []
        calls: list[ToolCall] = []
        data: dict = {}
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message") or {}
            piece = msg.get("content") or ""
            if piece:
                parts.append(piece)
                on_text(piece)
            calls += _calls(msg)
            if chunk.get("done"):
                data = chunk
                break
        return ChatReply(content="".join(parts).strip(), tool_calls=calls, raw=data)

    def _options(self, model: str | None = None) -> dict[str, Any]:
        opts: dict[str, Any] = {"temperature": self.temperature}
        if model in self.gpu_layers:
            opts["num_gpu"] = self.gpu_layers[model]
        if self.num_ctx:
            opts["num_ctx"] = self.num_ctx          # fixed size so the model's VRAM use is predictable
        if self.num_predict:
            opts["num_predict"] = self.num_predict  # hard cap on reply length
        return opts

    def unload(self, model: str):
        """Free the model's VRAM immediately."""
        try:
            requests.post(f"{self.host}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
        except requests.RequestException as exc:
            log.warning("unload failed: %s", exc)

    def preload(self, model: str):
        """Load a model into memory ahead of time so the first answer is fast."""
        try:
            requests.post(f"{self.host}/api/generate",
                          json={"model": model, "keep_alive": self.keep_alive, "options": self._options(model)},
                          timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("preload failed: %s", exc)

    def available_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])]
        except requests.RequestException:
            return []
