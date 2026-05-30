from __future__ import annotations

import pytest

from openpine.accounts.models import Account, AccountType
from openpine.execution import ExecutionRouter, ExecutionUnavailableError
from openpine.orders.models import Order, OrderIntent, OrderSide, OrderStatus, OrderType


class _AccountManager:
    def __init__(self, account: Account | None) -> None:
        self._account = account

    def get_account(self, account_id: str) -> Account | None:
        if self._account is None or self._account.account_id != account_id:
            return None
        return self._account


class _RiskManager:
    def check_order(self, order: OrderIntent, account: Account) -> tuple[bool, str | None]:
        return True, None


class _WorkingAdapter:
    async def submit_order(self, order: OrderIntent) -> Order:
        return Order(
            order_id="order-1",
            client_order_id=order.client_order_id,
            strategy_id=order.strategy_id,
            account_id=order.account_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            status=OrderStatus.NEW,
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_order_status(self, order_id: str) -> Order | None:
        return None


class _FailingCancelAdapter(_WorkingAdapter):
    async def cancel_order(self, order_id: str) -> bool:
        raise RuntimeError("exchange unavailable")


def _live_account() -> Account:
    return Account(
        account_id="acct-1",
        name="Live",
        provider="binance",
        exchange="binance",
        account_type=AccountType.LIVE,
        live_enabled=True,
    )


@pytest.mark.asyncio
async def test_router_cancel_fails_closed_when_adapter_missing() -> None:
    router = ExecutionRouter(_RiskManager(), _AccountManager(_live_account()))

    with pytest.raises(ExecutionUnavailableError, match="No adapter registered"):
        await router.cancel_order("order-1", "acct-1")


@pytest.mark.asyncio
async def test_router_cancel_fails_closed_on_adapter_error() -> None:
    router = ExecutionRouter(_RiskManager(), _AccountManager(_live_account()))
    router.register_adapter(AccountType.LIVE, _FailingCancelAdapter())

    with pytest.raises(ExecutionUnavailableError, match="Adapter cancel failed"):
        await router.cancel_order("order-1", "acct-1")


@pytest.mark.asyncio
async def test_router_cancel_preserves_adapter_false_for_known_order_state() -> None:
    class _KnownFalseAdapter(_WorkingAdapter):
        async def cancel_order(self, order_id: str) -> bool:
            return False

    router = ExecutionRouter(_RiskManager(), _AccountManager(_live_account()))
    router.register_adapter(AccountType.LIVE, _KnownFalseAdapter())

    assert await router.cancel_order("order-1", "acct-1") is False
