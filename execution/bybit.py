"""Bybit live execution adapter for OpenPine — Phase 7.

Fail-closed live exchange adapter for Bybit.
Requires injected async client — REJECTS all orders without one.
Enforces instrument precision rules before any network call.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from openpine.execution.models import (
    CancelResult,
    ExecutionUnavailableError,
    InstrumentRules,
    LiveOrderResult,
)
from openpine.orders.models import (
    Order,
    OrderIntent,
    OrderStatus,
    generate_order_id,
)

if TYPE_CHECKING:
    from openpine.execution.router import ExecutionAdapter


# Default empty rules — no symbols allowed without explicit rules
_DEFAULT_RULES: dict[str, InstrumentRules] = {}

# Bybit status mapping — Bybit uses specific uppercase status strings
_BYBIT_STATUS_MAP: dict[str, str] = {
    "Created": "new",
    "New": "new",
    "Filled": "filled",
    "PartiallyFilled": "partial",
    "Cancelled": "cancelled",
    "Canceled": "cancelled",
    "Rejected": "rejected",
    "Rejected": "rejected",
    "Expired": "cancelled",
    "pending": "new",
}


def _map_bybit_status(raw: str) -> str:
    """Map Bybit exchange status to normalized status string."""
    return _BYBIT_STATUS_MAP.get(raw, raw.lower())


class BybitLiveExecutionAdapter:
    """Bybit live execution adapter — fail-closed by default.

    Key contracts:
    - NO injected client => REJECTED order (fail-closed)
    - Instrument rules validated BEFORE any network call
    - submit_order only calls client after local precision checks pass
    - cancel/get_status/reconcile use injected client if present
    - NEVER makes network calls in tests (uses injected fake client)

    Order tracking:
    - After successful submit, order_id -> symbol is tracked locally
    - cancel_order(order_id) and get_order_status(order_id) use tracked symbol
    - Also available: cancel_order_for_symbol(order_id, symbol) and
      get_order_status_for_symbol(order_id, symbol) for explicit symbol

    This adapter does NOT implement ExecutionAdapter directly —
    it is wrapped by ExecutionRouter which provides RiskManager gate.
    """

    def __init__(
        self,
        client: Any | None = None,
        instrument_rules: dict[str, InstrumentRules] | None = None,
    ) -> None:
        """Initialize Bybit adapter.

        Args:
            client: Async Bybit client (e.g., ccxt-bybit async).
                    If None, all orders are REJECTED.
            instrument_rules: Dict of symbol -> InstrumentRules.
                             If None or empty, all orders are REJECTED
                             (no symbols allowed without rules).
        """
        self._client = client
        self._rules: dict[str, InstrumentRules] = instrument_rules or _DEFAULT_RULES
        # Track order_id -> symbol for cancel/status calls
        self._order_symbols: dict[str, str] = {}

    @property
    def client(self) -> Any | None:
        """Return the injected client (may be None for fail-closed)."""
        return self._client

    def add_instrument_rules(self, rules: InstrumentRules) -> None:
        """Add or update instrument rules for a symbol.

        Args:
            rules: InstrumentRules to add
        """
        self._rules[rules.symbol] = rules

    def get_instrument_rules(self, symbol: str) -> InstrumentRules | None:
        """Get instrument rules for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            InstrumentRules or None if not found
        """
        return self._rules.get(symbol)

    def _track_order_symbol(self, order_id: str, symbol: str) -> None:
        """Track exchange order_id -> symbol mapping for later cancel/status.

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol
        """
        self._order_symbols[order_id] = symbol

    def _get_tracked_symbol(self, order_id: str) -> str | None:
        """Get tracked symbol for an order_id.

        Args:
            order_id: Exchange order ID

        Returns:
            Symbol or None if not tracked
        """
        return self._order_symbols.get(order_id)

    def _validate_order(self, order: OrderIntent) -> tuple[bool, str | None]:
        """Validate order locally against instrument rules.

        This is called BEFORE any network interaction.

        Args:
            order: Order to validate

        Returns:
            (is_valid, error_message)
        """
        # Check if we have rules for this symbol
        rules = self._rules.get(order.symbol)
        if rules is None:
            return False, f"No instrument rules for {order.symbol}"

        # Validate the order
        return rules.validate_order(
            quantity=order.quantity,
            price=order.price,
            order_type=order.order_type.value,
        )

    async def submit_order(self, order: OrderIntent) -> Order:
        """Submit order to Bybit via injected client.

        FAIL-ClOSED contracts:
        1. No client => REJECTED
        2. No rules for symbol => REJECTED
        3. Precision/min_notional violation => REJECTED

        Only calls client after ALL local checks pass.

        Args:
            order: Order intent

        Returns:
            Order with updated status
        """
        now = int(time.time() * 1000)
        order_id = generate_order_id()

        # FAIL-CLOSED: No client => REJECTED
        if self._client is None:
            return Order(
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
                status=OrderStatus.REJECTED,
                error="Bybit adapter: no client injected (fail-closed)",
                created_at=order.created_at,
                updated_at=now,
            )

        # Local precision and notional validation BEFORE network call
        valid, error_message = self._validate_order(order)
        if not valid:
            return Order(
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
                status=OrderStatus.REJECTED,
                error=f"Bybit adapter validation failed: {error_message}",
                created_at=order.created_at,
                updated_at=now,
            )

        # All local checks passed — call the injected client
        try:
            result = await self._call_create_order(order)
            return self._result_to_order(result, order, order_id, now)
        except Exception as e:
            return Order(
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
                status=OrderStatus.REJECTED,
                error=f"Bybit adapter error: {e}",
                created_at=order.created_at,
                updated_at=now,
            )

    async def _call_create_order(self, order: OrderIntent) -> LiveOrderResult:
        """Call the injected client's create_order method.

        Passes client_order_id via ccxt's clientOrderId param.
        Passes stop_price for stop/stop_limit orders via stopPrice param.

        This is the ONLY place where the client is called for order creation.
        Subclasses or test fakes can override this method.

        Args:
            order: Order to create

        Returns:
            LiveOrderResult
        """
        params: dict[str, Any] = {}

        # Pass client_order_id via ccxt's standard clientOrderId param
        if order.client_order_id:
            params["clientOrderId"] = order.client_order_id

        # Pass stop_price for stop orders
        # ccxt uses stopPrice for stop-limit and stop-market orders
        order_type_val = order.order_type.value
        if order_type_val in ("stop", "stop_limit", "STOP", "STOP_LIMIT") and order.stop_price is not None:
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

    def _parse_client_response(
        self, response: Any, symbol: str | None = None
    ) -> LiveOrderResult:
        """Parse client response into LiveOrderResult.

        Tracks order_id -> symbol mapping for later cancel/status use.

        Args:
            response: Raw client response
            symbol: Symbol to track (optional, extracted from response if not provided)

        Returns:
            LiveOrderResult
        """
        if response is None:
            return LiveOrderResult(success=False, error="Empty response")

        # Bybit/ccxt response fields
        order_id = (
            response.get("id")
            or response.get("orderId")
            or response.get("order_id")
        )
        raw_status = response.get("status", "unknown")
        mapped_status = _map_bybit_status(raw_status)
        filled = float(response.get("filled", 0) or 0)
        avg_price = response.get("average") or response.get("avgPrice")

        # Track this order for cancel/status
        if order_id and symbol:
            self._track_order_symbol(str(order_id), symbol)

        is_success = mapped_status in ("new", "filled", "partial")
        err_msg = None if is_success else f"Unknown exchange status '{raw_status}'"

        return LiveOrderResult(
            success=is_success,
            order_id=str(order_id) if order_id else None,
            status=mapped_status,
            filled_qty=filled,
            avg_fill_price=float(avg_price) if avg_price else None,
            error=err_msg,
            raw_response=response,
        )

    def _result_to_order(
        self,
        result: LiveOrderResult,
        intent: OrderIntent,
        order_id: str,
        now: int,
    ) -> Order:
        """Convert LiveOrderResult to Order.

        Args:
            result: Live order result
            intent: Original order intent
            order_id: OpenPine order ID
            now: Timestamp

        Returns:
            Order with appropriate status
        """
        if result.success:
            status = OrderStatus.FILLED
            if result.status == "new":
                status = OrderStatus.NEW
            elif result.status == "partial":
                status = OrderStatus.PARTIAL

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
                status=status,
                filled_quantity=result.filled_qty,
                avg_fill_price=result.avg_fill_price,
                created_at=intent.created_at,
                updated_at=now,
            )
        else:
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
                error=result.error,
                created_at=intent.created_at,
                updated_at=now,
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order via injected client (protocol-compatible, no symbol required).

        Uses tracked symbol if available, otherwise fails-closed.
        For explicit symbol, use cancel_order_for_symbol(order_id, symbol).

        FAIL-CLOSED: No client => False
        FAIL-CLOSED: No tracked symbol and no explicit symbol => False

        Args:
            order_id: Exchange order ID

        Returns:
            True if cancelled, False otherwise
        """
        symbol = self._get_tracked_symbol(order_id)
        if symbol is None:
            return False  # Need symbol, fail-closed
        return await self.cancel_order_for_symbol(order_id, symbol)

    async def cancel_order_for_symbol(self, order_id: str, symbol: str) -> bool:
        """Cancel order via injected client with explicit symbol.

        FAIL-CLOSED: No client => False

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol

        Returns:
            True if cancelled, False otherwise
        """
        if self._client is None:
            return False

        try:
            result = await self._call_cancel_order(order_id, symbol)
            return result.success
        except Exception:
            return False

    async def _call_cancel_order(
        self, order_id: str, symbol: str
    ) -> CancelResult:
        """Call injected client to cancel order.

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol (required by ccxt)

        Returns:
            CancelResult
        """
        response = await self._client.cancel_order(symbol=symbol, id=order_id)
        if response is None:
            return CancelResult(success=False, error="Empty response")
        raw_status = response.get("status", "")
        mapped_status = _map_bybit_status(raw_status)
        return CancelResult(
            success=mapped_status == "cancelled",
        )

    async def get_order_status(self, order_id: str) -> Order | None:
        """Get order status from exchange via injected client (protocol-compatible).

        Uses tracked symbol if available, otherwise fails-closed.
        For explicit symbol, use get_order_status_for_symbol(order_id, symbol).

        FAIL-CLOSED: No client => None
        FAIL-CLOSED: No tracked symbol => None

        Args:
            order_id: Exchange order ID

        Returns:
            Order or None if not found
        """
        symbol = self._get_tracked_symbol(order_id)
        if symbol is None:
            return None  # Need symbol, fail-closed
        return await self.get_order_status_for_symbol(order_id, symbol)

    async def get_order_status_for_symbol(
        self, order_id: str, symbol: str
    ) -> Order | None:
        """Get order status from exchange via injected client with explicit symbol.

        FAIL-CLOSED: No client => None

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol (required by ccxt)

        Returns:
            Order or None if not found
        """
        if self._client is None:
            return None

        try:
            return await self._call_get_order(order_id, symbol)
        except Exception:
            return None

    async def _call_get_order(self, order_id: str, symbol: str) -> Order | None:
        """Call injected client to get order status.

        Args:
            order_id: Exchange order ID
            symbol: Trading symbol

        Returns:
            Order or None
        """
        response = await self._client.fetch_order(symbol=symbol, id=order_id)
        if response is None:
            return None
        return self._response_to_order_status(response)

    def _response_to_order_status(self, response: Any) -> Order:
        """Convert client response to Order with graceful unknown status handling.

        Unknown exchange statuses are returned as OrderStatus.REJECTED
        with an error message rather than crashing.

        Args:
            response: Raw client response

        Returns:
            Order (status may be REJECTED with error for unknown statuses)
        """
        now = int(time.time() * 1000)

        raw_status = response.get("status", "unknown")
        mapped_status = _map_bybit_status(raw_status)

        # Handle unknown statuses gracefully — don't crash
        try:
            status = OrderStatus(mapped_status)
            error_msg = None
        except ValueError:
            status = OrderStatus.REJECTED
            error_msg = f"Unknown exchange status '{raw_status}'"

        return Order(
            order_id=response.get("id") or response.get("orderId") or "",
            client_order_id=response.get("clientOrderId", ""),
            strategy_id="",
            account_id=response.get("account_id", ""),
            symbol=response.get("symbol", ""),
            side=response.get("side", "buy"),
            order_type=response.get("type", "limit"),
            quantity=float(response.get("amount", 0)),
            price=float(response.get("price", 0)) or None,
            stop_price=float(response.get("stopPrice", 0)) or None,
            status=status,
            filled_quantity=float(response.get("filled", 0) or 0),
            avg_fill_price=float(response.get("average", 0)) or None,
            created_at=response.get("timestamp", 0),
            updated_at=now,
            error=error_msg,
        )

    async def reconcile(self, account_id: str) -> list[Order]:
        """Reconcile orders with exchange via injected client.

        FAIL-CLOSED: exchange unavailability raises, never returns a synthetic empty list.

        Args:
            account_id: Account to reconcile

        Returns:
            List of orders from exchange
        """
        if self._client is None:
            raise ExecutionUnavailableError("Bybit reconcile requires an injected client")

        try:
            return await self._call_reconcile(account_id)
        except Exception as exc:
            raise ExecutionUnavailableError(f"Bybit reconcile failed: {exc}") from exc

    async def _call_reconcile(self, account_id: str) -> list[Order]:
        """Call injected client to fetch open orders.

        Args:
            account_id: Account to fetch orders for

        Returns:
            List of orders
        """
        response = await self._client.fetch_open_orders()
        orders = []
        for o in response:
            order = self._response_to_order_status(o)
            # Track order_id -> symbol for later cancel/status
            if order.order_id and order.symbol:
                self._track_order_symbol(order.order_id, order.symbol)
            orders.append(order)
        return orders
