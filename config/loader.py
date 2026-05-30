"""Config loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

from openpine.config.env import load_env_file
from openpine.config.model import OpenPineConfig


def default_config_path() -> tuple[Path, Path | None]:
    config_dir_env = os.environ.get("OPENPINE_CONFIG_DIR")
    if config_dir_env:
        config_dir = Path(config_dir_env).expanduser()
        return config_dir / "config.yaml", config_dir
    return Path(".openpine/config.yaml"), Path(".openpine")


def load_config(config_path: Path | None = None) -> OpenPineConfig:
    load_env_file()
    explicit_config_dir: Path | None = None
    if config_path is None:
        config_path, explicit_config_dir = default_config_path()

    if config_path.exists():
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        loaded = OpenPineConfig(**data)
        if "config_dir" in data:
            loaded.config_dir = Path(data["config_dir"]).expanduser()
        return loaded

    if explicit_config_dir is not None:
        return OpenPineConfig(config_dir=explicit_config_dir)

    return OpenPineConfig()


DEFAULT_CONFIG = load_config()


__all__ = ["DEFAULT_CONFIG", "default_config_path", "load_config"]
