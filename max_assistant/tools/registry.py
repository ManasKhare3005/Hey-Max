"""Tool registry: tools describe themselves to the LLM and declare whether they're risky."""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)


DECLINED = "The user declined, so this action was NOT performed."


class ForLLM(str):
    """A result the LLM must read and act on even though the tool is `direct`
    (e.g. a list of page elements to choose from)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, dict]      # name -> JSON-schema property
    required: list[str]
    func: Callable[..., Any]
    risky: bool = False
    confirm: str | None = None       # spoken confirmation template, e.g. "Shut down the laptop?"
    direct: bool = False             # result is already a spoken sentence; no LLM rephrasing needed

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }

    def confirmation_prompt(self, args: dict) -> str:
        if self.confirm:
            try:
                return self.confirm.format(**args)
            except (KeyError, IndexError):
                pass
        return f"Should I run {self.name.replace('_', ' ')}?"


class ToolRegistry:
    def __init__(self, context: Any = None):
        self.tools: dict[str, Tool] = {}
        self.context = context

    def tool(self, description: str, params: dict[str, dict] | None = None,
             required: list[str] | None = None, risky: bool = False,
             confirm: str | None = None, name: str | None = None, direct: bool = False):
        """Decorator. If the function has a `ctx` parameter, the registry context is injected.
        `direct=True` means the tool returns a finished spoken reply (e.g. "Opened Spotify."),
        so the agent can say it without a second LLM call."""

        def wrap(func):
            tname = name or func.__name__
            props = params or {}
            self.tools[tname] = Tool(
                name=tname,
                description=description,
                parameters=props,
                required=required if required is not None else list(props),
                func=func,
                risky=risky,
                confirm=confirm,
                direct=direct,
            )
            return func

        return wrap

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def run(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        allowed = set(tool.parameters)
        clean = {k: v for k, v in (args or {}).items() if k in allowed}
        missing = [r for r in tool.required if r not in clean]
        if missing:
            return f"Error: missing argument(s) {', '.join(missing)} for {name}."
        kwargs = dict(clean)
        if "ctx" in inspect.signature(tool.func).parameters:
            kwargs["ctx"] = self.context
        try:
            result = tool.func(**kwargs)
        except Exception as exc:
            log.exception("tool %s failed", name)
            return f"Error running {name}: {exc}"
        if result is None:
            return "Done."
        return result if isinstance(result, str) else str(result)   # keeps ForLLM
