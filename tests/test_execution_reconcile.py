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
