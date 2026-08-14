"""Gateway configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from ipaddress import ip_network

from openpine.config import OpenPineConfig

DEFAULT_CORS_ORIGINS = [
    "http://localhost:1888",
    "http://127.0.0.1:1888",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _auth_token_from_env() -> str | None:
    """Return the configured API token without inventing a default secret."""

    value = os.environ.get("OPENPINE_API_TOKEN", "").strip()
    return value or None


def _auth_principal_from_env() -> str:
    return os.environ.get("OPENPINE_API_PRINCIPAL", "lan-operator").strip() or "lan-operator"


def _trusted_proxy_cidrs_from_env() -> tuple[str, ...]:
    raw = os.environ.get("OPENPINE_TRUSTED_PROXY_CIDRS", "")
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _environment_from_env() -> str:
    value = os.environ.get("OPENPINE_ENV", "development").strip().lower()
    if value not in {"development", "test", "production"}:
        raise ValueError(
            "OPENPINE_ENV must be one of: development, test, production"
        )
    return value


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
    environment: str = field(default_factory=_environment_from_env)
    auth_token: str | None = field(default_factory=_auth_token_from_env, repr=False)
    auth_principal: str = field(default_factory=_auth_principal_from_env)
    trusted_proxy_cidrs: tuple[str, ...] = field(default_factory=_trusted_proxy_cidrs_from_env)

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError(
                "environment must be one of: development, test, production"
            )
        if self.auth_token is not None and not self.auth_token.strip():
            raise ValueError("OPENPINE_API_TOKEN must not be blank")
        if self.environment == "production" and self.auth_token is None:
            raise ValueError(
                "OPENPINE_API_TOKEN is required when OPENPINE_ENV=production"
            )
        for value in self.trusted_proxy_cidrs:
            ip_network(value, strict=False)

    @classmethod
    def from_openpine_config(
        cls, openpine: OpenPineConfig | None = None
    ) -> GatewayConfig:
        """Build gateway config from OpenPine config (future YAML override)."""
        # In future, read from openpine config YAML section [gateway].
        # For now, use defaults.
        return cls()
