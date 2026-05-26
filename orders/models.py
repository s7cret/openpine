"""Order models for OpenPine — sections 30.7, 33.2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class OrderSide(StrEnum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    """Order type."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(StrEnum):
    """Order status — sections 30.7, 33.2."""

    PENDING = "pending"
    NEW = "new"  # Active on exchange, not yet filled
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


def generate_order_id() -> str:
    """Generate a stable order ID."""
    return f"ord_{uuid.uuid4().hex[:12]}"


def generate_client_order_id() -> str:
    """Generate a stable client order ID for duplicate protection."""
    return f"c_{uuid.uuid4().hex[:12]}"


@dataclass
class OrderIntent:
    """Section 30.7: order intent before RiskManager check.

    This is the pre-submission order representation.
    All orders must pass through RiskManager before execution.
    """

    client_order_id: str
    strategy_id: str
    account_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None
    stop_price: float | None = None
    created_at: int = field(default_factory=lambda: int(__import__("time").time() * 1000))


@dataclass
class Order:
    """Section 30.7: order after RiskManager and execution.

    Includes status, fill info, and error field for rejected orders.
    """

    order_id: str
    client_order_id: str
    strategy_id: str
    account_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None
    stop_price: float | None
    status: OrderStatus
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    created_at: int = field(default_factory=lambda: int(__import__("time").time() * 1000))
    updated_at: int = field(default_factory=lambda: int(__import__("time").time() * 1000))
    error: str | None = None
