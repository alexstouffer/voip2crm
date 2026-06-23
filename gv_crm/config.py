"""Load config.yaml, resolve ${ENV_VAR} references, expose typed access."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (no dependency). KEY=VALUE per line; existing
    environment variables win, so real exports always override the file."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${VAR} with os.environ values."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


@dataclass
class Config:
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        _load_dotenv()
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Config not found: {path}. Copy config.example.yaml to config.yaml."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(raw=_resolve_env(data))

    def section(self, name: str) -> dict:
        return self.raw.get(name, {}) or {}

    def get(self, *keys, default=None):
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def ensure_dirs(self) -> None:
        for key in ("audio_dir", "transcript_dir"):
            d = self.get("storage", key)
            if d:
                Path(d).mkdir(parents=True, exist_ok=True)
        db = self.get("storage", "state_db")
        if db:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
