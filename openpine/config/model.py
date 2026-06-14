"""OpenPine configuration models."""

from __future__ import annotations

from pathlib import Path

import pydantic

from openpine.timezones import DEFAULT_TIMEZONE, resolve_timezone

from openpine.notifications import TelegramPluginConfig


SUPPORTED_MARKETDATA_TIMEFRAMES: tuple[str, ...] = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "45m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "1w",
    "1M",
)
MARKETDATA_SYMBOL_SEARCH_LIMIT_MAX = 500


class PluginsConfig(pydantic.BaseModel):
    """Plugin configuration container."""

    telegram: TelegramPluginConfig = TelegramPluginConfig()


class OpenPineConfig(pydantic.BaseModel):
    """Root configuration for OpenPine."""

    workspace_root: Path = Path(".").resolve()
    data_dir: Path = Path(".openpine/data")
    data_cache_root: Path | None = None
    output_root: Path | None = None
    config_dir: Path = Path(".openpine")
    sqlite_path: Path = Path(".openpine/openpine.sqlite")
    db_path: Path | None = None
    duckdb_path: Path = Path(".openpine/openpine.duckdb")
    log_level: str = "INFO"
    timezone: str = DEFAULT_TIMEZONE
    live_enabled: bool = False
    kill_switch: bool = False
    marketdata_stable_quotes_only: bool = True
    marketdata_stable_quote_assets: tuple[str, ...] = (
        "USDT",
        "USDC",
        "USD",
        "FDUSD",
        "BUSD",
        "TUSD",
        "USDP",
        "DAI",
    )
    marketdata_symbol_search_limit: int = 50
    marketdata_timeframes: tuple[str, ...] = (
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "1d",
    )
    marketdata_default_timeframe: str = "1h"
    plugins: PluginsConfig = PluginsConfig()

    @pydantic.field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        return resolve_timezone(v).name

    @pydantic.field_validator("marketdata_stable_quote_assets")
    @classmethod
    def _normalize_stable_quote_assets(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip().upper() for item in v if item.strip()))

    @pydantic.field_validator("marketdata_symbol_search_limit")
    @classmethod
    def _validate_symbol_search_limit(cls, v: int) -> int:
        if v < 1:
            raise ValueError("marketdata_symbol_search_limit must be positive")
        if v > MARKETDATA_SYMBOL_SEARCH_LIMIT_MAX:
            raise ValueError(
                f"marketdata_symbol_search_limit must be <= {MARKETDATA_SYMBOL_SEARCH_LIMIT_MAX}"
            )
        return v

    @pydantic.field_validator("marketdata_timeframes", mode="before")
    @classmethod
    def _normalize_marketdata_timeframes(cls, v: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
        if isinstance(v, str):
            raw_items = [item.strip() for item in v.split(",")]
        else:
            raw_items = [str(item).strip() for item in v]
        supported = set(SUPPORTED_MARKETDATA_TIMEFRAMES)
        normalized: list[str] = []
        for item in raw_items:
            if not item:
                continue
            candidate = "1M" if item in {"M", "1M", "1mo", "1MO"} else item.lower()
            if candidate not in supported:
                raise ValueError(f"unsupported marketdata timeframe: {item}")
            if candidate not in normalized:
                normalized.append(candidate)
        if not normalized:
            raise ValueError("marketdata_timeframes must not be empty")
        return tuple(normalized)

    @pydantic.field_validator("marketdata_default_timeframe")
    @classmethod
    def _normalize_default_timeframe(cls, v: str) -> str:
        item = str(v).strip()
        candidate = "1M" if item in {"M", "1M", "1mo", "1MO"} else item.lower()
        if candidate not in set(SUPPORTED_MARKETDATA_TIMEFRAMES):
            raise ValueError(f"unsupported marketdata default timeframe: {v}")
        return candidate

    @pydantic.model_validator(mode="after")
    def _validate_default_timeframe_in_list(self) -> OpenPineConfig:
        if self.marketdata_default_timeframe not in self.marketdata_timeframes:
            raise ValueError("marketdata_default_timeframe must be listed in marketdata_timeframes")
        return self

    @pydantic.field_validator(
        "workspace_root",
        "data_dir",
        "data_cache_root",
        "output_root",
        "config_dir",
        "sqlite_path",
        "db_path",
        "duckdb_path",
        mode="before",
    )
    @classmethod
    def _expand_user_paths(cls, v: Path | str | None) -> Path | None:
        """Ensure user home paths are expanded before validation."""
        if v is None:
            return None
        if isinstance(v, str):
            return Path(v).expanduser()
        if isinstance(v, Path):
            return v.expanduser()
        return v

    def model_post_init(self, __context: object) -> None:
        self.workspace_root = self.workspace_root.expanduser().resolve()
        for field_name in ("data_dir", "config_dir", "sqlite_path", "duckdb_path"):
            value = getattr(self, field_name)
            if not value.is_absolute():
                setattr(self, field_name, self.workspace_root / value)
        if self.data_cache_root is None:
            self.data_cache_root = self.data_dir / "cache"
        elif not self.data_cache_root.is_absolute():
            self.data_cache_root = self.workspace_root / self.data_cache_root
        if self.output_root is None:
            self.output_root = self.data_dir / "outputs"
        elif not self.output_root.is_absolute():
            self.output_root = self.workspace_root / self.output_root
        if self.db_path is None:
            self.db_path = self.sqlite_path
        elif not self.db_path.is_absolute():
            self.db_path = self.workspace_root / self.db_path

    def config_path(self) -> Path:
        """Path to the YAML config file (resolved from config_dir)."""
        return self.config_dir / "config.yaml"

    def save(self) -> None:
        """Save the current config to config_dir/config.yaml.

        Creates the config directory and all parent directories if needed.
        """
        import os
        import tempfile

        import yaml

        self.config_dir.mkdir(parents=True, exist_ok=True)
        resolved = self.config_path()
        fd, tmp_name = tempfile.mkstemp(dir=self.config_dir, prefix="config.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as f:
                yaml.safe_dump(self.model_dump(mode="json"), f, default_flow_style=False)
            tmp_path.replace(resolved)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, config_path: Path | None = None) -> OpenPineConfig:
        from openpine.config.loader import load_config

        return load_config(config_path)
