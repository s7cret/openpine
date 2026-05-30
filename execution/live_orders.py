"""Shared order construction helpers for live execution adapters."""

from __future__ import annotations

from openpine.orders.models import Order, OrderIntent, OrderStatus


def rejected_live_order(
    intent: OrderIntent,
    *,
    order_id: str,
    now: int,
    error: str,
) -> Order:
    return Order(
        order_id=order_id,
        client_order_id=intent.client_order_id,
        strategy_id=intent.strategy_id,
        account_id=intent.account_id,
        symbol=intent.symbol,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        price=intent.price,
        stop_price=intent.stop_price,
        status=OrderStatus.REJECTED,
        error=error,
        created_at=intent.created_at,
        updated_at=now,
    )
