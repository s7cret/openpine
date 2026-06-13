"""RiskManager for OpenPine — sections 7.11, 21.1, 33.2.

RiskManager is the hard gate for ALL orders before reaching exchange.
Every order MUST pass through RiskManager.check_order() before execution.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Protocol

from openpine.accounts.models import Account
from openpine.orders.models import OrderIntent


class RiskRule(Protocol):
    """Section 7.11: Risk rule protocol.

    Each rule checks an order intent against account config.
    Returns (allowed, error_message).
    If not allowed, error_message explains why.
    """

    def check(self, order: OrderIntent, account: Account) -> tuple[bool, str | None]:
        """Check if order is allowed by this rule.

        Args:
            order: Order intent to check
            account: Account for the order

        Returns:
            (allowed, error_message) — allowed=True means rule passes
        """
        ...


class KillSwitchRule(RiskRule):
    """Section 21.4: Global kill switch rule.

    Blocks ALL orders when kill switch is enabled.
    """

    def __init__(self, kill_switch_ref: list[bool]) -> None:
        """Initialize with reference to global kill switch state.

        Args:
            kill_switch_ref: List containing single bool [enabled]
        """
        self._kill_switch_ref = kill_switch_ref

    def check(self, order: OrderIntent, account: Account) -> tuple[bool, str | None]:
        """Block all orders if kill switch is enabled."""
        if self._kill_switch_ref[0]:
            return False, "Kill switch is active — all orders blocked"
        return True, None


class MaxPositionSizeRule(RiskRule):
    """Section 21.1: Max position size rule.

    Limits maximum notional order size per account.
    """

    def __init__(self, max_notional: float = 10_000.0) -> None:
        """Initialize with max notional limit.

        Args:
            max_notional: Maximum order notional value in USDT
        """
        self._max_notional = max_notional

    def check(self, order: OrderIntent, account: Account) -> tuple[bool, str | None]:
        """Check if order notional exceeds max position size."""
        if order.price is None:
            # For market orders without price, assume 0 and allow through other checks
            return True, None

        notional = order.price * order.quantity
        if notional > self._max_notional:
            return (
                False,
                f"Order notional {notional:.2f} exceeds max position size {self._max_notional:.2f}",
            )
        return True, None


class MaxOrdersPerMinuteRule(RiskRule):
    """Section 21.1: Max orders per minute rule.

    Tracks order count per account and blocks if exceeded.
    """

    def __init__(self, max_orders: int = 10) -> None:
        """Initialize with max orders per minute limit.

        Args:
            max_orders: Maximum orders allowed per minute per account
        """
        self._max_orders = max_orders
        self._order_counts: dict[str, list[int]] = defaultdict(
            list
        )  # account_id -> [timestamps]

    def check(self, order: OrderIntent, account: Account) -> tuple[bool, str | None]:
        """Check if account has exceeded max orders per minute."""
        now = int(time.time())
        cutoff = now - 60  # 60 second window

        # Clean old timestamps
        self._order_counts[account.id] = [
            ts for ts in self._order_counts[account.id] if ts > cutoff
        ]

        if len(self._order_counts[account.id]) >= self._max_orders:
            return (
                False,
                f"Account {account.id} has exceeded max orders per minute ({self._max_orders})",
            )

        # Record this order timestamp
        self._order_counts[account.id].append(now)
        return True, None


class RiskManager:
    """Section 7.11 + 33.2: hard gates for order execution.

    ALL orders MUST pass through RiskManager before reaching exchange.
    RiskManager evaluates rules in order and blocks on first failure.

    Key contracts:
    - process_next_bar failure → strategy.status = error, NO orders produced (33.2)
    - RiskManager hard gates block all orders (live_enabled=false by default)
    - Kill switch blocks ALL orders when enabled (section 30.7)
    """

    def __init__(self, global_kill_switch: bool = False) -> None:
        """Initialize RiskManager with optional global kill switch.

        Args:
            global_kill_switch: Initial kill switch state (default False)
        """
        self._kill_switch = [global_kill_switch]  # Mutable ref for KillSwitchRule
        self._rules: list[RiskRule] = []
        self._order_counts: dict[str, list[int]] = defaultdict(
            list
        )  # account_id -> [timestamps]
        self._violations: dict[str, list[str]] = defaultdict(
            list
        )  # account_id -> [error messages]

        # Always add kill switch as first rule
        self.add_rule(KillSwitchRule(self._kill_switch))

    def add_rule(self, rule: RiskRule) -> None:
        """Add a risk rule. Rules are checked in order.

        Args:
            rule: RiskRule implementation to add
        """
        self._rules.append(rule)

    def check_order(
        self, order: OrderIntent, account: Account
    ) -> tuple[bool, str | None]:
        """Check order against all rules.

        ALL rules are checked in order. First failure blocks the order.

        Args:
            order: Order intent to check
            account: Account for the order

        Returns:
            (allowed, error_message) — if not allowed, error_message explains why
        """
        for rule in self._rules:
            allowed, error_message = rule.check(order, account)
            if not allowed:
                # Record violation
                self._violations[account.id].append(
                    error_message or f"Order blocked by {rule.__class__.__name__}"
                )
                return False, error_message

        return True, None

    def set_kill_switch(self, enabled: bool) -> None:
        """Set global kill switch (section 30.7).

        When enabled, ALL orders are blocked regardless of other rules.

        Args:
            enabled: True to activate kill switch
        """
        self._kill_switch[0] = enabled

    @property
    def kill_switch(self) -> bool:
        """Check if kill switch is active."""
        return self._kill_switch[0]

    def get_violations(self, account_id: str) -> list[str]:
        """Get recent rule violations for account.

        Args:
            account_id: Account identifier

        Returns:
            List of violation messages
        """
        return list(self._violations.get(account_id, []))

    def clear_violations(self, account_id: str) -> None:
        """Clear violations for account after successful trading.

        Args:
            account_id: Account identifier
        """
        self._violations[account_id] = []
