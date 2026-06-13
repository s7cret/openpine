"""OrderManager for OpenPine — sections 30.7, 33.2.

Manages order lifecycle with duplicate client_order_id protection.
All orders pass through RiskManager before reaching execution.
"""

from __future__ import annotations

import json
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
    from openpine.storage.sqlite_storage import SQLiteStorage


def _intent_payload(order: OrderIntent) -> dict:
    return {
        "client_order_id": order.client_order_id,
        "strategy_id": order.strategy_id,
        "account_id": order.account_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": order.quantity,
        "price": order.price,
        "stop_price": order.stop_price,
        "created_at": order.created_at,
    }


class OrderManager:
    """Manages order lifecycle with duplicate client_order_id protection.

    Key contracts:
    - Duplicate client_order_id protection (section 21.1)
    - No live trading (section 2.1)
    - All orders pass through RiskManager before execution
    """

    def __init__(self, storage: "SQLiteStorage") -> None:
        """Initialize OrderManager.

        Args:
            storage: SQLiteStorage instance
        """
        self.storage = storage
        self._seen_client_ids: set[str] = set()
        self._load_existing_ids()

    def _load_existing_ids(self) -> None:
        """Load existing client_order_ids to detect duplicates."""
        cursor = self.storage.execute("SELECT client_order_id FROM orders")
        for row in cursor.fetchall():
            self._seen_client_ids.add(row[0])

    def submit_order(self, order: OrderIntent) -> Order | None:
        """Submit order with duplicate client_order_id protection.

        Rejects if client_order_id already exists (duplicate protection).

        Args:
            order: Order intent to submit

        Returns:
            Order object if accepted, None if rejected as duplicate
        """
        # Check for duplicate client_order_id
        if order.client_order_id in self._seen_client_ids:
            return None

        # Mark as seen immediately to prevent race conditions
        self._seen_client_ids.add(order.client_order_id)

        # Create order record
        now = int(time.time() * 1000)
        order_id = generate_order_id()
        intent_json = json.dumps(_intent_payload(order))

        self.storage.execute(
            """
            INSERT INTO orders
              (order_id, strategy_id, account_id, provider_order_id, client_order_id,
               symbol, side, order_type, qty, limit_price, stop_price, status,
               reduce_only, filled_quantity, avg_fill_price, intent_json,
               risk_decision_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                order.strategy_id,
                order.account_id,
                None,  # provider_order_id
                order.client_order_id,
                order.symbol,
                order.side.value,
                order.order_type.value,
                order.quantity,
                order.price,
                order.stop_price,
                OrderStatus.PENDING.value,
                0,  # reduce_only
                0.0,  # filled_quantity
                None,  # avg_fill_price
                intent_json,
                None,  # risk_decision_json
                None,  # error
                order.created_at,
                now,
            ),
        )
        self.storage.commit()

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
            status=OrderStatus.PENDING,
            created_at=order.created_at,
            updated_at=now,
        )

    def get_order(self, order_id: str) -> Order | None:
        """Get order by ID.

        Args:
            order_id: Order identifier

        Returns:
            Order object or None
        """
        cursor = self.storage.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_order(row)

    def list_orders(
        self,
        account_id: str | None = None,
        status: OrderStatus | None = None,
    ) -> list[Order]:
        """List orders with optional filters.

        Args:
            account_id: Filter by account (optional)
            status: Filter by status (optional)

        Returns:
            List of Order objects
        """
        conditions = []
        params = []

        if account_id is not None:
            conditions.append("account_id = ?")
            params.append(account_id)

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = ""
        if conditions:
            where_clause = " WHERE " + " AND ".join(conditions)

        cursor = self.storage.execute(
            f"SELECT * FROM orders{where_clause} ORDER BY created_at DESC",
            tuple(params),
        )
        return [self._row_to_order(row) for row in cursor.fetchall()]

    def update_order_status(
        self,
        order_id: str,
        status: OrderStatus,
        filled_quantity: float = 0.0,
        avg_fill_price: float | None = None,
        error: str | None = None,
    ) -> None:
        """Update order status after execution.

        Args:
            order_id: Order identifier
            status: New status
            filled_quantity: Filled quantity
            avg_fill_price: Average fill price
            error: Error message if rejected
        """
        now = int(time.time() * 1000)
        self.storage.execute(
            """
            UPDATE orders
            SET status = ?, filled_quantity = ?, avg_fill_price = ?,
                error = ?, updated_at = ?
            WHERE order_id = ?
            """,
            (status.value, filled_quantity, avg_fill_price, error, now, order_id),
        )
        self.storage.commit()

    def _row_to_order(self, row: tuple) -> Order:
        """Convert database row to Order object.

        Schema: order_id(0), strategy_id(1), account_id(2), provider_order_id(3),
                client_order_id(4), symbol(5), side(6), order_type(7), qty(8),
                limit_price(9), stop_price(10), status(11), reduce_only(12),
                filled_quantity(13), avg_fill_price(14), intent_json(15),
                risk_decision_json(16), error(17), created_at(18), updated_at(19)
        """
        return Order(
            order_id=row[0],
            strategy_id=row[1],
            account_id=row[2] if row[2] else "",
            client_order_id=row[4],
            symbol=row[5],
            side=OrderSide(row[6]),
            order_type=OrderType(row[7]),
            quantity=row[8],
            price=row[9],
            stop_price=row[10],
            status=OrderStatus(row[11]),
            filled_quantity=row[13] if row[13] is not None else 0.0,
            avg_fill_price=row[14],
            created_at=row[18],
            updated_at=row[19],
            error=row[17],
        )
