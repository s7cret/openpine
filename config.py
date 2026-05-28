"""OpenPine configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pydantic

from openpine.notifications import TelegramPluginConfig


# Auto-load ~/.openpine/env if it exists
_ENV_FILE = Path("~/.openpine/env").expanduser()
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


class PluginsConfig(pydantic.BaseModel):
    """Plugin configuration container."""

    telegram: TelegramPluginConfig = TelegramPluginConfig()


class OpenPineConfig(pydantic.BaseModel):
    """Root configuration for OpenPine."""

    data_dir: Path = Path("~/.openpine/data").expanduser()
    config_dir: Path = Path("~/.openpine").expanduser()
    sqlite_path: Path = Path("~/.openpine/openpine.sqlite").expanduser()
    duckdb_path: Path = Path("~/.openpine/openpine.duckdb").expanduser()
    log_level: str = "INFO"
    live_enabled: bool = False
    kill_switch: bool = False
    plugins: PluginsConfig = PluginsConfig()

    @pydantic.field_validator("data_dir", "config_dir", "sqlite_path", "duckdb_path", mode="before")
    @classmethod
    def _expand_user_paths(cls, v: Path | str) -> Path:
        """Ensure user home paths are expanded before validation."""
        if isinstance(v, str):
            return Path(v).expanduser()
        if isinstance(v, Path):
            return v.expanduser()
        return v

    def config_path(self) -> Path:
        """Path to the YAML config file (resolved from config_dir)."""
        return Path(os.path.expanduser(str(self.config_dir / "config.yaml")))

    def save(self) -> None:
        """Save the current config to config_dir/config.yaml.

        Creates the config directory and all parent directories if needed.
        """
        import yaml

        self.config_dir.mkdir(parents=True, exist_ok=True)
        resolved = Path(os.path.expanduser(str(self.config_path())))
        with open(resolved, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json"), f, default_flow_style=False)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> OpenPineConfig:
        """Load configuration from file or environment.

        Args:
            config_path: Explicit path to a YAML file. When None, uses
                         OPENPINE_CONFIG_DIR env var if set, otherwise
                         ~/.openpine/config.yaml.
        """
        explicit_config_dir: Path | None = None
        if config_path is None:
            config_dir_env = os.environ.get("OPENPINE_CONFIG_DIR")
            if config_dir_env:
                explicit_config_dir = Path(config_dir_env).expanduser()
                config_path = explicit_config_dir / "config.yaml"
            else:
                config_path = Path("~/.openpine/config.yaml").expanduser()

        if config_path.exists():
            import yaml

            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            loaded = cls(**data)
            # Use the config_dir from the file as the default path basis,
            # so saves go back to the same file.
            if "config_dir" in data:
                loaded.config_dir = Path(data["config_dir"])
            return loaded

        if explicit_config_dir is not None:
            return cls(config_dir=explicit_config_dir)

        return cls()


DEFAULT_CONFIG = OpenPineConfig.load()
