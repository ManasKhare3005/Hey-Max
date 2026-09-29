"""Load config.yaml into a nested, attribute-accessible object."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Section(dict):
    """dict that also allows cfg.llm.host style access."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc
        return Section(value) if isinstance(value, dict) and not isinstance(value, Section) else value

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value


def load_config(path: str | Path | None = None) -> Section:
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Section(data)


def resolve_path(p: str | Path) -> Path:
    """Paths in config are relative to the project root."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
