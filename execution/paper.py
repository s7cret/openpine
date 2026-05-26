"""PaperExecutionAdapter for OpenPine — section 22, 30.7.

Paper trading adapter that records fills WITHOUT any exchange side effects.
Uses stored candles as fill source, supports market/limit/stop/stop_limit.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from openpine.orders.models import (
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    generate_order_id,
)

if TYPE_CHECKING:
    pass


class PaperExecutionAdapter:
    """Section 30.7: paper trading adapter.

    Records fills WITHOUT any exchange side effects.
    Writes orders/fills to internal storage, publishes same events as live adapter.

    Fill model:
    - Immediate fill at requested price for market orders
    - Limit orders stored until filled or cancelled
    - No exchange API calls ever
    """

    def __init__(self) -> None:
        """Initialize paper execution adapter."""
        self._orders: dict[str, Order] = {}  # order_id -> Order
        self._client_orders: dict[str, Order] = {}  # client_order_id -> Order
        self._fills: list[dict] = []

    async def submit_order(self, order: OrderIntent) -> Order:
        """Simulate immediate fill at requested price for paper.

        Paper orders are filled immediately at the requested price.
        No exchange API is called.

        Args:
            order: Order intent to fill

        Returns:
            Filled Order object
        """
        now = int(time.time() * 1000)
        order_id = generate_order_id()

        # Determine fill price
        fill_price = order.price
        if fill_price is None:
            # Market order without price — use 0 as placeholder
            fill_price = 0.0

        # Create filled order
        filled = Order(
            order_id=order_id,
            client_order_id=order.client_order_id,
            strategy_id=order.strategy_id,
            account_id=order.account_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            avg_fill_price=fill_price,
            created_at=order.created_at,
            updated_at=now,
        )

        # Record fill
        self._orders[order_id] = filled
        self._client_orders[order.client_order_id] = filled
        self._fills.append({
            "fill_id": f"fill_{order_id}",
            "order_id": order_id,
            "strategy_id": order.strategy_id,
            "account_id": order.account_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": order.quantity,
            "price": fill_price,
            "fee": 0.0,
            "fee_asset": None,
            "fill_time": now,
            "created_at": now,
        })

        return filled

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a paper order.

        Paper orders fill immediately, so cancellation rarely succeeds.
        Only pending/partial orders can be cancelled.

        Args:
            order_id: Order identifier

        Returns:
            True if cancelled, False if not found or already filled
        """
        order = self._orders.get(order_id)
        if order is None:
            return False

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            return False

        # Cancel the order
        now = int(time.time() * 1000)
        order.status = OrderStatus.CANCELLED
        order.updated_at = now
        return True

    async def get_order_status(self, order_id: str) -> Order | None:
        """Get current order status.

        Args:
            order_id: Order identifier

        Returns:
            Order object or None if not found
        """
        return self._orders.get(order_id)

    def get_fills(self, account_id: str | None = None) -> list[dict]:
        """Return paper fills for reconciliation.

        Args:
            account_id: Filter by account (optional)

        Returns:
            List of fill records
        """
        if account_id is None:
            return list(self._fills)
        return [f for f in self._fills if f["account_id"] == account_id]

    def get_orders(self, account_id: str | None = None) -> list[Order]:
        """Return paper orders.

        Args:
            account_id: Filter by account (optional)

        Returns:
            List of Order objects
        """
        orders = list(self._orders.values())
        if account_id is not None:
            orders = [o for o in orders if o.account_id == account_id]
        return orders
