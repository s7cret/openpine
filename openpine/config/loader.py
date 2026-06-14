"""Config loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

from openpine.config.env import load_env_file
from openpine.config.model import OpenPineConfig

_TIMEZONE_ENV = "OPENPINE_TIMEZONE"
_STABLE_QUOTES_ONLY_ENV = "OPENPINE_MARKETDATA_STABLE_QUOTES_ONLY"
_STABLE_QUOTE_ASSETS_ENV = "OPENPINE_MARKETDATA_STABLE_QUOTE_ASSETS"
_SYMBOL_SEARCH_LIMIT_ENV = "OPENPINE_MARKETDATA_SYMBOL_SEARCH_LIMIT"
_TIMEFRAMES_ENV = "OPENPINE_MARKETDATA_TIMEFRAMES"
_DEFAULT_TIMEFRAME_ENV = "OPENPINE_MARKETDATA_DEFAULT_TIMEFRAME"


def _apply_env_overrides(data: dict) -> dict:
    merged = dict(data)
    if _TIMEZONE_ENV in os.environ:
        merged["timezone"] = os.environ[_TIMEZONE_ENV]
    if _STABLE_QUOTES_ONLY_ENV in os.environ:
        merged["marketdata_stable_quotes_only"] = os.environ[_STABLE_QUOTES_ONLY_ENV].strip().lower() in {"1", "true", "yes", "on"}
    if _STABLE_QUOTE_ASSETS_ENV in os.environ:
        merged["marketdata_stable_quote_assets"] = tuple(
            item.strip().upper()
            for item in os.environ[_STABLE_QUOTE_ASSETS_ENV].split(",")
            if item.strip()
        )
    if _SYMBOL_SEARCH_LIMIT_ENV in os.environ:
        merged["marketdata_symbol_search_limit"] = int(os.environ[_SYMBOL_SEARCH_LIMIT_ENV])
    if _TIMEFRAMES_ENV in os.environ:
        merged["marketdata_timeframes"] = tuple(
            item.strip()
            for item in os.environ[_TIMEFRAMES_ENV].split(",")
            if item.strip()
        )
    if _DEFAULT_TIMEFRAME_ENV in os.environ:
        merged["marketdata_default_timeframe"] = os.environ[_DEFAULT_TIMEFRAME_ENV]
    return merged


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
        if "config_dir" not in data:
            data["config_dir"] = config_path.parent
        return OpenPineConfig(**_apply_env_overrides(data))

    if explicit_config_dir is not None:
        return OpenPineConfig(**_apply_env_overrides({"config_dir": explicit_config_dir}))

    return OpenPineConfig(**_apply_env_overrides({}))


DEFAULT_CONFIG = load_config()


__all__ = ["DEFAULT_CONFIG", "default_config_path", "load_config"]
