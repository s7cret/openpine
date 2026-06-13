"""Shared fail-closed helpers for ccxt-style live execution adapters."""

from __future__ import annotations

from typing import Any, Protocol

from openpine.execution.models import InstrumentRules, LiveOrderResult
from openpine.orders.models import Order, OrderIntent


class _CcxtAdapterProtocol(Protocol):
    _client: Any | None
    _rules: dict[str, InstrumentRules]

    def _track_order_symbol(self, order_id: str, symbol: str) -> None: ...

    def _parse_client_response(
        self, response: Any, symbol: str | None = None
    ) -> LiveOrderResult: ...

    def _response_to_order_status(self, response: Any) -> Order: ...


class CcxtOrderClientMixin:
    """Common order-client operations shared by exchange adapters.

    Exchange-specific adapters keep status mapping, response parsing, cancel
    semantics, and user-facing error messages locally. The repeated ccxt order
    request plumbing lives here so Binance/Bybit do not drift independently.
    """

    _client: Any | None
    _rules: dict[str, InstrumentRules]

    def _validate_order(self, order: OrderIntent) -> tuple[bool, str | None]:
        rules = self._rules.get(order.symbol)
        if rules is None:
            return False, f"No instrument rules for {order.symbol}"
        return rules.validate_order(
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type.value,
        )

    async def _call_create_order(self: _CcxtAdapterProtocol, order: OrderIntent) -> LiveOrderResult:
        params: dict[str, Any] = {}
        if order.client_order_id:
            params["clientOrderId"] = order.client_order_id
        order_type_val = order.order_type.value
        if (
            order_type_val in ("stop", "stop_limit", "STOP", "STOP_LIMIT")
            and order.stop_price is not None
        ):
            params["stopPrice"] = str(order.stop_price)
        client_order = await self._client.create_order(
            symbol=order.symbol,
            type=order_type_val,
            side=order.side.value,
            amount=str(order.quantity),
            price=str(order.price) if order.price else None,
            params=params,
        )
        return self._parse_client_response(client_order, order.symbol)

    async def _call_get_order(self: _CcxtAdapterProtocol, order_id: str, symbol: str) -> Order | None:
        response = await self._client.fetch_order(symbol=symbol, id=order_id)
        if response is None:
            return None
        return self._response_to_order_status(response)

    async def _call_reconcile(self: _CcxtAdapterProtocol, account_id: str) -> list[Order]:
        response = await self._client.fetch_open_orders()
        orders = []
        for raw in response:
            order = self._response_to_order_status(raw)
            if order.order_id and order.symbol:
                self._track_order_symbol(order.order_id, order.symbol)
            orders.append(order)
        return orders
