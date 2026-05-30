from __future__ import annotations

import pytest

from openpine.execution.paper import PaperExecutionAdapter
from openpine.orders.models import OrderIntent, OrderSide, OrderStatus, OrderType


def _intent(account_id: str = "acct-1", price: float | None = None) -> OrderIntent:
    return OrderIntent(
        client_order_id="client-1",
        strategy_id="strategy-1",
        account_id=account_id,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2.0,
        price=price,
    )


@pytest.mark.asyncio
async def test_paper_market_order_without_price_fills_at_placeholder_zero() -> None:
    adapter = PaperExecutionAdapter()

    order = await adapter.submit_order(_intent())

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 2.0
    assert order.avg_fill_price == 0.0
    fills = adapter.get_fills()
    assert fills[0]["order_id"] == order.order_id
    assert fills[0]["price"] == 0.0


@pytest.mark.asyncio
async def test_paper_fills_can_be_filtered_by_account() -> None:
    adapter = PaperExecutionAdapter()

    await adapter.submit_order(_intent(account_id="acct-1", price=101.5))
    await adapter.submit_order(_intent(account_id="acct-2", price=102.5))

    fills = adapter.get_fills(account_id="acct-2")

    assert len(fills) == 1
    assert fills[0]["account_id"] == "acct-2"
    assert fills[0]["price"] == 102.5
