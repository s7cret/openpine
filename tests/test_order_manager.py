from __future__ import annotations

import json

from openpine.orders.manager import OrderManager
from openpine.orders.models import OrderIntent, OrderSide, OrderStatus, OrderType
from openpine.storage import MigrationRunner, SQLiteStorage


def _storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def _intent(client_order_id: str = "client-1") -> OrderIntent:
    return OrderIntent(
        client_order_id=client_order_id,
        strategy_id="strategy-1",
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=2.0,
        price=101.5,
    )


def test_order_manager_rejects_duplicate_client_order_id(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        manager = OrderManager(storage)

        first = manager.submit_order(_intent())
        duplicate = manager.submit_order(_intent())

        assert first is not None
        assert duplicate is None
        assert len(manager.list_orders()) == 1
        row = storage.execute(
            "SELECT intent_json FROM orders WHERE order_id = ?", (first.order_id,)
        ).fetchone()
        payload = json.loads(row[0])
        assert payload["client_order_id"] == "client-1"
        assert payload["order_type"] == "limit"
    finally:
        storage.close()


def test_order_manager_updates_and_filters_status(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        manager = OrderManager(storage)
        order = manager.submit_order(_intent())
        assert order is not None

        manager.update_order_status(
            order.order_id,
            OrderStatus.FILLED,
            filled_quantity=2.0,
            avg_fill_price=101.5,
        )

        loaded = manager.get_order(order.order_id)
        assert loaded is not None
        assert loaded.status == OrderStatus.FILLED
        assert loaded.filled_quantity == 2.0
        assert loaded.avg_fill_price == 101.5
        assert [o.order_id for o in manager.list_orders(status=OrderStatus.FILLED)] == [
            order.order_id
        ]
    finally:
        storage.close()
