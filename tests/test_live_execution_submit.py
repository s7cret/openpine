from __future__ import annotations

import pytest

from openpine.execution import BinanceLiveExecutionAdapter, BybitLiveExecutionAdapter
from openpine.orders.models import OrderIntent, OrderSide, OrderStatus, OrderType


def _intent() -> OrderIntent:
    return OrderIntent(
        client_order_id="client-1",
        strategy_id="strategy-1",
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        created_at=123,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (BinanceLiveExecutionAdapter(), "Binance adapter: no client injected"),
        (BybitLiveExecutionAdapter(), "Bybit adapter: no client injected"),
    ],
)
async def test_live_submit_without_client_rejects_without_network(adapter, error_text: str) -> None:
    order = await adapter.submit_order(_intent())

    assert order.status == OrderStatus.REJECTED
    assert error_text in order.error
    assert order.client_order_id == "client-1"
    assert order.created_at == 123
