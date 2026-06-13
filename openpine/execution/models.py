"""Execution models for OpenPine — instrument rules and live adapter types."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class ExecutionUnavailableError(RuntimeError):
    """Raised when live execution state cannot be reconciled with an exchange."""


@dataclass(frozen=True)
class InstrumentRules:
    """Instrument trading rules for precision and size validation.

    These rules enforce exchange-level constraints before order submission.
    They are fetched from the exchange (or cached) and validated locally
    before any order reaches the network.

    Attributes:
        symbol: Trading symbol, e.g. "BTCUSDT"
        tick_size: Price increment (e.g. 0.01 for BTCUSDT)
        step_size: Quantity increment (e.g. 0.001 for BTCUSDT)
        min_qty: Minimum order quantity
        min_notional: Minimum notional value (price * qty)
        max_qty: Maximum order quantity (optional, None = no limit)
        market_order_supported: Whether market orders are allowed
    """

    symbol: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float
    max_qty: float | None = None
    market_order_supported: bool = True

    def _is_aligned_float(self, value: float, step: float) -> bool:
        """Float-based alignment check (fallback, may have precision artifacts).

        Args:
            value: Value to check
            step: Step size

        Returns:
            True if aligned
        """
        if step <= 0:
            return False
        aligned = round(value / step) * step
        return abs(value - aligned) < 1e-9

    def _is_aligned_decimal(self, value: float, step: float) -> bool:
        """Decimal-based alignment check — avoids float precision artifacts.

        Uses Decimal for exact arithmetic. Handles common exchange increments
        like 0.01, 0.001, 0.0001 without float rounding errors.

        Args:
            value: Value to check
            step: Step size

        Returns:
            True if aligned
        """
        if step <= 0:
            return False
        try:
            d_value = Decimal(str(value))
            d_step = Decimal(str(step))
            # Check if value is an exact multiple of step: (value % step) == 0
            remainder = d_value % d_step
            return remainder == Decimal("0") or remainder < Decimal(str(1e-9))
        except (InvalidOperation, ValueError):
            # Fall back to float check
            return self._is_aligned_float(value, step)

    def _is_aligned(self, value: float, step: float) -> bool:
        """Check if value is aligned to step using Decimal for precision.

        Args:
            value: Value to check
            step: Step size

        Returns:
            True if aligned (multiple of step)
        """
        return self._is_aligned_decimal(value, step)

    def validate_price(self, price: float | None) -> tuple[bool, str | None]:
        """Validate price precision and bounds.

        Args:
            price: Price to validate (None for market orders)

        Returns:
            (is_valid, error_message)
        """
        if price is None:
            return True, None  # Market orders have no price

        if price <= 0:
            return False, f"Price must be positive, got {price}"

        # Check tick size precision
        if not self._is_aligned(price, self.tick_size):
            return (
                False,
                f"Price {price} does not align to tick_size {self.tick_size}",
            )
        return True, None

    def validate_quantity(self, quantity: float) -> tuple[bool, str | None]:
        """Validate quantity precision and size bounds.

        Args:
            quantity: Quantity to validate

        Returns:
            (is_valid, error_message)
        """
        if quantity <= 0:
            return False, f"Quantity must be positive, got {quantity}"

        # Check step size precision
        if not self._is_aligned(quantity, self.step_size):
            return (
                False,
                f"Quantity {quantity} does not align to step_size {self.step_size}",
            )

        # Check min qty
        if quantity < self.min_qty:
            return (
                False,
                f"Quantity {quantity} below min_qty {self.min_qty}",
            )

        # Check max qty if defined
        if self.max_qty is not None and quantity > self.max_qty:
            return (
                False,
                f"Quantity {quantity} above max_qty {self.max_qty}",
            )

        return True, None

    def validate_notional(
        self,
        quantity: float,
        price: float | None,
    ) -> tuple[bool, str | None]:
        """Validate notional value (price * quantity).

        Args:
            quantity: Order quantity
            price: Order price (None for market orders — skip notional check)

        Returns:
            (is_valid, error_message)
        """
        if price is None:
            # Market orders without price can't validate notional upfront
            return True, None

        notional = price * quantity
        if notional < self.min_notional:
            return (
                False,
                f"Notional {notional} below min_notional {self.min_notional}",
            )
        return True, None

    def validate_order(
        self,
        quantity: float,
        price: float | None,
        order_type: str,
    ) -> tuple[bool, str | None]:
        """Full validation of order parameters.

        Args:
            quantity: Order quantity
            price: Order price (None for market orders)
            order_type: Order type string

        Returns:
            (is_valid, error_message)
        """
        # Check market order support
        if order_type == "market" and not self.market_order_supported:
            return False, f"Market orders not supported for {self.symbol}"

        # Validate quantity first
        valid, err = self.validate_quantity(quantity)
        if not valid:
            return valid, err

        # Validate price
        valid, err = self.validate_price(price)
        if not valid:
            return valid, err

        # Validate notional
        valid, err = self.validate_notional(quantity, price)
        if not valid:
            return valid, err

        return True, None


# Default empty instrument rules — fail-closed (rejects all orders)
DEFAULT_INSTRUMENT_RULES: dict[str, InstrumentRules] = {}
"""Default empty rules — no symbols allowed without explicit rules."""


@dataclass
class LiveOrderResult:
    """Result from a live exchange order submission.

    Attributes:
        success: Whether the order was accepted by exchange
        order_id: Exchange-assigned order ID (if success)
        status: Order status string from exchange
        filled_qty: Filled quantity
        avg_fill_price: Average fill price
        error: Error message (if failure)
        raw_response: Raw exchange response (for debugging)
    """

    success: bool
    order_id: str | None = None
    status: str | None = None
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    error: str | None = None
    raw_response: Any = None


@dataclass
class CancelResult:
    """Result from a cancel request.

    Attributes:
        success: Whether cancel was accepted
        error: Error message (if failure)
    """

    success: bool
    error: str | None = None
