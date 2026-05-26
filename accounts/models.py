"""Account models for OpenPine — section 30.4."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class AccountType(StrEnum):
    """Account execution mode type."""

    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


def generate_id(prefix: str) -> str:
    """Generate a stable prefixed ID.

    Args:
        prefix: ID prefix (e.g. 'acct', 'ord')

    Returns:
        ID string like 'acct_abc123def'
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(init=False)
class Account:
    """Section 30.4: Account configuration.

    Account represents a trading account with execution provider.
    live_enabled=False by default (section 30.4 critical contract).
    """

    account_id: str
    name: str
    provider: str
    exchange: str
    market_type: str
    mode: AccountType = AccountType.PAPER
    live_enabled: bool = False  # default False (section 30.4)
    api_key_ref: str | None = None
    api_secret_ref: str | None = None
    permissions: str | None = None
    api_key_hash: str | None = None  # legacy compatibility; never raw key
    config: dict = field(default_factory=dict)
    created_at: int = 0
    updated_at: int = 0

    def __init__(
        self,
        account_id: str | None = None,
        name: str = "",
        provider: str = "unknown",
        exchange: str = "",
        market_type: str = "spot",
        mode: AccountType | str = AccountType.PAPER,
        live_enabled: bool = False,
        api_key_ref: str | None = None,
        api_secret_ref: str | None = None,
        permissions: str | None = None,
        api_key_hash: str | None = None,
        config: dict | None = None,
        created_at: int = 0,
        updated_at: int = 0,
        id: str | None = None,
        account_type: AccountType | str | None = None,
    ) -> None:
        self.account_id = account_id or id or generate_id("acct")
        self.name = name
        self.provider = provider
        self.exchange = exchange
        self.market_type = market_type
        resolved_mode = account_type if account_type is not None else mode
        self.mode = resolved_mode if isinstance(resolved_mode, AccountType) else AccountType(resolved_mode)
        self.live_enabled = live_enabled
        self.api_key_ref = api_key_ref
        self.api_secret_ref = api_secret_ref
        self.permissions = permissions
        self.api_key_hash = api_key_hash
        self.config = config or {}
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def id(self) -> str:
        """Compatibility alias for older callers."""
        return self.account_id

    @property
    def account_type(self) -> AccountType:
        """Compatibility alias for execution/risk modules."""
        return self.mode
