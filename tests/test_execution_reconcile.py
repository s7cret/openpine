from __future__ import annotations

import pytest

from openpine.execution import (
    BinanceLiveExecutionAdapter,
    BybitLiveExecutionAdapter,
    ExecutionUnavailableError,
)


class _FailingClient:
    async def fetch_open_orders(self):
        raise RuntimeError("exchange unavailable")

    async def cancel_order(self, *, symbol: str, id: str):
        raise RuntimeError("exchange unavailable")

    async def fetch_order(self, *, symbol: str, id: str):
        raise RuntimeError("exchange unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (BinanceLiveExecutionAdapter(), "Binance reconcile requires an injected client"),
        (BybitLiveExecutionAdapter(), "Bybit reconcile requires an injected client"),
        (BinanceLiveExecutionAdapter(client=_FailingClient()), "Binance reconcile failed"),
        (BybitLiveExecutionAdapter(client=_FailingClient()), "Bybit reconcile failed"),
    ],
)
async def test_live_reconcile_fails_closed_without_synthetic_empty_orders(
    adapter,
    error_text: str,
) -> None:
    with pytest.raises(ExecutionUnavailableError, match=error_text):
        await adapter.reconcile("acct-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (BinanceLiveExecutionAdapter(), "Binance cancel requires an injected client"),
        (BybitLiveExecutionAdapter(), "Bybit cancel requires an injected client"),
        (BinanceLiveExecutionAdapter(client=_FailingClient()), "Binance cancel failed"),
        (BybitLiveExecutionAdapter(client=_FailingClient()), "Bybit cancel failed"),
    ],
)
async def test_live_cancel_fails_closed_without_synthetic_false(
    adapter,
    error_text: str,
) -> None:
    with pytest.raises(ExecutionUnavailableError, match=error_text):
        await adapter.cancel_order_for_symbol("order-1", "BTCUSDT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (
            BinanceLiveExecutionAdapter(client=_FailingClient()),
            "Binance cancel requires a tracked symbol",
        ),
        (
            BybitLiveExecutionAdapter(client=_FailingClient()),
            "Bybit cancel requires a tracked symbol",
        ),
    ],
)
async def test_live_protocol_cancel_fails_closed_without_tracked_symbol(
    adapter,
    error_text: str,
) -> None:
    with pytest.raises(ExecutionUnavailableError, match=error_text):
        await adapter.cancel_order("order-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (BinanceLiveExecutionAdapter(), "Binance order status requires an injected client"),
        (BybitLiveExecutionAdapter(), "Bybit order status requires an injected client"),
        (BinanceLiveExecutionAdapter(client=_FailingClient()), "Binance order status failed"),
        (BybitLiveExecutionAdapter(client=_FailingClient()), "Bybit order status failed"),
    ],
)
async def test_live_status_fails_closed_without_synthetic_none(
    adapter,
    error_text: str,
) -> None:
    with pytest.raises(ExecutionUnavailableError, match=error_text):
        await adapter.get_order_status_for_symbol("order-1", "BTCUSDT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error_text",
    [
        (
            BinanceLiveExecutionAdapter(client=_FailingClient()),
            "Binance order status requires a tracked symbol",
        ),
        (
            BybitLiveExecutionAdapter(client=_FailingClient()),
            "Bybit order status requires a tracked symbol",
        ),
    ],
)
async def test_live_protocol_status_fails_closed_without_tracked_symbol(
    adapter,
    error_text: str,
) -> None:
    with pytest.raises(ExecutionUnavailableError, match=error_text):
        await adapter.get_order_status("order-1")
