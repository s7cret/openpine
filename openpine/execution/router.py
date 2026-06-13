"""ExecutionRouter for OpenPine — section 7.10.

Routes order intents to execution adapters after RiskManager approval.
Every order MUST pass through RiskManager before reaching any adapter.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

from openpine.accounts.models import AccountType
from openpine.execution.models import ExecutionUnavailableError
from openpine.orders.models import (
    Order,
    OrderIntent,
    OrderStatus,
    generate_order_id,
)
from openpine.risk.manager import RiskManager

if TYPE_CHECKING:
    from openpine.accounts.manager import AccountManager


class ExecutionAdapter(Protocol):
    """Section 7.10: Execution adapter protocol.

    Adapters handle order submission to specific execution providers.
    """

    async def submit_order(self, order: OrderIntent) -> Order:
        """Submit order to execution provider.

        Args:
            order: Order intent to submit

        Returns:
            Order with updated status
        """
        ...

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order identifier

        Returns:
            True if cancelled, False otherwise
        """
        ...

    async def get_order_status(self, order_id: str) -> Order | None:
        """Get current order status.

        Args:
            order_id: Order identifier

        Returns:
            Order object or None
        """
        ...


class ExecutionRouter:
    """Section 7.10: routes order intents to execution adapters.

    Key contracts:
    - ALL orders MUST pass through RiskManager before reaching exchange (7.11)
    - RiskManager check comes FIRST, before any adapter
    - If rejected, return Order with status=REJECTED and error
    - NEVER reaches exchange without RiskManager approval
    - PaperExecutionAdapter: fills recorded, NO exchange calls
    """

    def __init__(
        self,
        risk_manager: RiskManager,
        account_manager: "AccountManager",
    ) -> None:
        """Initialize ExecutionRouter.

        Args:
            risk_manager: RiskManager instance for order approval
            account_manager: AccountManager for account lookups
        """
        self.risk_manager = risk_manager
        self.account_manager = account_manager
        self._adapters: dict[AccountType, ExecutionAdapter] = {}

    def register_adapter(
        self, account_type: AccountType, adapter: ExecutionAdapter
    ) -> None:
        """Register an execution adapter for an account type.

        Args:
            account_type: Type of account (LIVE, PAPER, BACKTEST)
            adapter: ExecutionAdapter implementation
        """
        self._adapters[account_type] = adapter

    @staticmethod
    def _rejected_order(order: OrderIntent, error: str, updated_at: int) -> Order:
        return Order(
            order_id=generate_order_id(),
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
            error=error,
            created_at=order.created_at,
            updated_at=updated_at,
        )

    async def submit_order(self, order: OrderIntent) -> Order:
        """Submit order: RiskManager check first.

        This is the critical path:
        1. Look up account
        2. RiskManager evaluates order
        3. If blocked, return REJECTED order with error
        4. If approved, route to appropriate adapter
        5. NEVER reaches exchange without RiskManager approval

        Args:
            order: Order intent to submit

        Returns:
            Order with status=FILLED (approved) or REJECTED (blocked)
        """
        now = int(time.time() * 1000)

        # Step 1: Look up account
        account = self.account_manager.get_account(order.account_id)
        if account is None:
            return self._rejected_order(
                order, f"Account not found: {order.account_id}", now
            )

        # Step 2: RiskManager check — ALL orders must pass
        allowed, error_message = self.risk_manager.check_order(order, account)
        if not allowed:
            return self._rejected_order(
                order,
                error_message or "RiskManager blocked order",
                now,
            )

        # Step 3: Route to adapter
        adapter = self._adapters.get(account.account_type)
        if adapter is None:
            return self._rejected_order(
                order,
                f"No adapter registered for account type: {account.account_type}",
                now,
            )

        # Step 4: Submit to adapter (paper or live)
        try:
            return await adapter.submit_order(order)
        except Exception as e:
            return self._rejected_order(order, f"Adapter error: {e}", now)

    async def cancel_order(self, order_id: str, account_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Order identifier
            account_id: Account identifier

        Returns:
            True if cancelled, False otherwise
        """
        account = self.account_manager.get_account(account_id)
        if account is None:
            return False

        adapter = self._adapters.get(account.account_type)
        if adapter is None:
            raise ExecutionUnavailableError(
                f"No adapter registered for account type: {account.account_type}"
            )

        try:
            return await adapter.cancel_order(order_id)
        except ExecutionUnavailableError:
            raise
        except Exception as exc:
            raise ExecutionUnavailableError(f"Adapter cancel failed: {exc}") from exc
