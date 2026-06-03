"""Gateway configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpine.config import OpenPineConfig


@dataclass(frozen=True)
class GatewayConfig:
    """Web gateway server configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    api_prefix: str = "/api"
    ws_prefix: str = "/ws"
    reload: bool = False
    workers: int = 1

    @classmethod
    def from_openpine_config(cls, openpine: OpenPineConfig | None = None) -> "GatewayConfig":
        """Build gateway config from OpenPine config (future YAML override)."""
        # In future, read from openpine config YAML section [gateway].
        # For now, use defaults.
        return cls()
