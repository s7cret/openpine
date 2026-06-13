"""Gateway configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from openpine.config import OpenPineConfig


DEFAULT_CORS_ORIGINS = [
    "http://localhost:1888",
    "http://127.0.0.1:1888",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@dataclass(frozen=True)
class GatewayConfig:
    """Web gateway server configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=lambda: list(DEFAULT_CORS_ORIGINS))
    api_prefix: str = "/api"
    ws_prefix: str = "/ws"
    reload: bool = False
    workers: int = 1

    @classmethod
    def from_openpine_config(
        cls, openpine: OpenPineConfig | None = None
    ) -> "GatewayConfig":
        """Build gateway config from OpenPine config (future YAML override)."""
        # In future, read from openpine config YAML section [gateway].
        # For now, use defaults.
        return cls()
