"""OpenPine configuration models."""

from __future__ import annotations

from pathlib import Path

import pydantic

from openpine.timezones import DEFAULT_TIMEZONE, resolve_timezone

from openpine.notifications import TelegramPluginConfig


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
        "FDUSD",
        "BUSD",
        "TUSD",
        "USDP",
        "DAI",
        "USD",
    )
    marketdata_symbol_search_limit: int = 50
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
        return v

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
        import yaml

        self.config_dir.mkdir(parents=True, exist_ok=True)
        resolved = self.config_path()
        with open(resolved, "w") as f:
            yaml.safe_dump(self.model_dump(mode="json"), f, default_flow_style=False)

    @classmethod
    def load(cls, config_path: Path | None = None) -> OpenPineConfig:
        from openpine.config.loader import load_config

        return load_config(config_path)
