"""Environment loading for OpenPine configuration."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path | None = None) -> None:
    env_path = path or Path("~/.openpine/env").expanduser()
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, _, value = item.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


__all__ = ["load_env_file"]
