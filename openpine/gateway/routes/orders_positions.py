"""Orders and positions routes."""

from __future__ import annotations


from openpine._compat import structlog
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
               take_profit_price, status, filled_quantity, avg_fill_price, error,
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
            "take_profit_price": r[10],
            "status": r[11],
            "filled_quantity": r[12],
            "avg_fill_price": r[13],
            "error": r[14],
            "created_at": r[15],
            "updated_at": r[16],
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
                  take_profit_price, status, filled_quantity, avg_fill_price, error,
                  created_at, updated_at
           FROM orders WHERE order_id = ?""",
        (order_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Order not found: {order_id}")
    return {
        "order_id": row[0],
        "strategy_id": row[1],
        "account_id": row[2],
        "client_order_id": row[3],
        "symbol": row[4],
        "side": row[5],
        "order_type": row[6],
        "qty": row[7],
        "limit_price": row[8],
        "stop_price": row[9],
        "take_profit_price": row[10],
        "status": row[11],
        "filled_quantity": row[12],
        "avg_fill_price": row[13],
        "error": row[14],
        "created_at": row[15],
        "updated_at": row[16],
    }


@router.get("/positions")
async def list_positions(
    strategy_id: str | None = None,
    state: GatewayState = Depends(get_state),
) -> list[dict[str, object]]:
    """List positions from the strategy ledger (raw SQL)."""
    where_sql = "WHERE strategy_id = ?" if strategy_id else ""
    params = (strategy_id,) if strategy_id else ()
    try:
        rows = state.storage.execute(
            f"""SELECT strategy_id, symbol, side, qty, avg_price,
                       realized_pnl, unrealized_pnl, opened_at, updated_at
                FROM strategy_positions {where_sql} ORDER BY updated_at DESC""",
            params,
        ).fetchall()
        return [
            {
                "strategy_id": r[0],
                "symbol": r[1],
                "side": r[2],
                "qty": r[3],
                "avg_entry_price": r[4],
                "realized_pnl": r[5],
                "unrealized_pnl": r[6],
                "opened_at": r[7],
                "updated_at": r[8],
            }
            for r in rows
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
        # Positions via raw SQL (ledger has no list-all method)
        pos_rows = state.storage.execute(
            """SELECT strategy_id, symbol, side, qty, avg_price,
                      realized_pnl, unrealized_pnl, opened_at, updated_at
               FROM strategy_positions WHERE strategy_id = ? ORDER BY updated_at DESC""",
            (strategy_id,),
        ).fetchall()
        positions = [
            {
                "strategy_id": r[0],
                "symbol": r[1],
                "side": r[2],
                "qty": r[3],
                "avg_entry_price": r[4],
                "realized_pnl": r[5],
                "unrealized_pnl": r[6],
                "opened_at": r[7],
                "updated_at": r[8],
            }
            for r in pos_rows
        ]
        # Trades via ledger.list_trades (keyword-only)
        trades = ledger.list_trades(strategy_id=strategy_id)
        return {
            "strategy_id": strategy_id,
            "positions": positions,
            "recent_trades": [
                {
                    "trade_id": t.trade_id,
                    "symbol": t.symbol,
                    "side": t.side.value if hasattr(t.side, "value") else str(t.side),
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
