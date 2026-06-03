"""Orders and positions routes."""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from openpine.gateway.deps import GatewayState, get_state

log = structlog.get_logger(__name__)
router = APIRouter(tags=["orders-positions"])


@router.get("/orders")
async def list_orders(
    strategy_id: str | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    state: GatewayState = Depends(get_state),
) -> list[dict[str, object]]:
    """List orders, optionally filtered by strategy_id and status."""
    where_clauses = []
    params: list[object] = []
    if strategy_id:
        where_clauses.append("strategy_id = ?")
        params.append(strategy_id)
    if status:
        where_clauses.append("status = ?")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    sql = f"""
        SELECT order_id, strategy_id, account_id, client_order_id,
               symbol, side, order_type, qty, limit_price, stop_price,
               status, filled_quantity, avg_fill_price, error,
               created_at, updated_at
        FROM orders {where_sql}
        ORDER BY created_at DESC LIMIT ?
    """
    params.append(limit)

    rows = state.storage.execute(sql, tuple(params)).fetchall()
    return [
        {
            "order_id": r[0],
            "strategy_id": r[1],
            "account_id": r[2],
            "client_order_id": r[3],
            "symbol": r[4],
            "side": r[5],
            "order_type": r[6],
            "qty": r[7],
            "limit_price": r[8],
            "stop_price": r[9],
            "status": r[10],
            "filled_quantity": r[11],
            "avg_fill_price": r[12],
            "error": r[13],
            "created_at": r[14],
            "updated_at": r[15],
        }
        for r in rows
    ]


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get a specific order."""
    row = state.storage.execute(
        """SELECT order_id, strategy_id, account_id, client_order_id,
                  symbol, side, order_type, qty, limit_price, stop_price,
                  status, filled_quantity, avg_fill_price, error,
                  created_at, updated_at
           FROM orders WHERE order_id = ?""",
        (order_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Order not found: {order_id}")
    return {
        "order_id": row[0], "strategy_id": row[1], "account_id": row[2],
        "client_order_id": row[3], "symbol": row[4], "side": row[5],
        "order_type": row[6], "qty": row[7], "limit_price": row[8],
        "stop_price": row[9], "status": row[10], "filled_quantity": row[11],
        "avg_fill_price": row[12], "error": row[13],
        "created_at": row[14], "updated_at": row[15],
    }


@router.get("/positions")
async def list_positions(
    strategy_id: str | None = None,
    state: GatewayState = Depends(get_state),
) -> list[dict[str, object]]:
    """List positions from the strategy ledger."""
    try:
        from openpine.storage.strategy_ledger import StrategyLedger
        ledger = StrategyLedger(state.storage)
        if strategy_id:
            positions = ledger.get_positions(strategy_id)
        else:
            positions = ledger.get_all_positions()
        return [
            {
                "strategy_id": p.strategy_id,
                "symbol": p.symbol,
                "side": p.side.value if hasattr(p.side, 'value') else p.side,
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "unrealized_pnl": getattr(p, 'unrealized_pnl', None),
                "realized_pnl": getattr(p, 'realized_pnl', None),
                "updated_at": getattr(p, 'updated_at', None),
            }
            for p in positions
        ]
    except Exception as exc:
        log.warning("positions_error", error=str(exc))
        return []


@router.get("/positions/{strategy_id}")
async def get_strategy_positions(
    strategy_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get positions and recent trades for a strategy."""
    try:
        from openpine.storage.strategy_ledger import StrategyLedger
        ledger = StrategyLedger(state.storage)
        positions = ledger.get_positions(strategy_id)
        trades = ledger.get_trades(strategy_id, limit=50)
        return {
            "strategy_id": strategy_id,
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value if hasattr(p.side, 'value') else p.side,
                    "qty": p.qty,
                    "avg_entry_price": p.avg_entry_price,
                }
                for p in positions
            ],
            "recent_trades": [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "side": t.side.value if hasattr(t.side, 'value') else t.side,
                    "qty": t.qty,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                }
                for t in trades
            ],
        }
    except Exception as exc:
        log.warning("strategy_positions_error", strategy_id=strategy_id, error=str(exc))
        return {"strategy_id": strategy_id, "positions": [], "recent_trades": []}
