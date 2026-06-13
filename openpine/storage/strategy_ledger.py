"""Strategy position/trade ledger for history, paper, and live modes."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openpine.storage.sqlite_storage import SQLiteStorage


class LedgerSource(StrEnum):
    """Origin of a strategy accounting record."""

    HISTORY = "history"
    PAPER = "paper"
    LIVE = "live"


class PositionSide(StrEnum):
    """Current strategy position side."""

    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


class TradeStatus(StrEnum):
    """Strategy trade lifecycle status."""

    OPEN = "open"
    CLOSED = "closed"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return json.loads(value)


def generate_position_id() -> str:
    return f"pos_{uuid.uuid4().hex[:16]}"


def generate_trade_id() -> str:
    return f"trade_{uuid.uuid4().hex[:16]}"


@dataclass
class StrategyPosition:
    """Latest accounting position for a strategy/account/symbol/timeframe."""

    strategy_id: str
    exchange: str
    market_type: str
    symbol: str
    timeframe: str
    source: LedgerSource
    side: PositionSide
    qty: float
    position_id: str = field(default_factory=generate_position_id)
    account_id: str = ""
    price_type: str = "trade"
    avg_price: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float | None = None
    opened_at: int | None = None
    last_bar_time: int | None = None
    metadata: dict[str, Any] | None = None
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)


@dataclass
class StrategyTrade:
    """Strategy-level trade ledger row, separate from orders and backtest_trades."""

    strategy_id: str
    exchange: str
    market_type: str
    symbol: str
    timeframe: str
    source: LedgerSource
    status: TradeStatus
    direction: str
    entry_time: int
    entry_price: float
    qty: float
    trade_id: str = field(default_factory=generate_trade_id)
    account_id: str = ""
    run_id: str | None = None
    order_id: str | None = None
    price_type: str = "trade"
    entry_id: str | None = None
    exit_id: str | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    gross_pnl: float | None = None
    net_pnl: float | None = None
    fee: float | None = None
    bars_held: int | None = None
    metadata: dict[str, Any] | None = None
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)


class StrategyLedger:
    """SQLite-backed strategy accounting ledger.

    `orders`/`fills` record execution attempts and fills. This ledger records
    strategy accounting facts: historical seed trades, paper trades, live
    trades, and the current position checkpoint.
    """

    def __init__(self, storage: "SQLiteStorage") -> None:
        self.storage = storage

    def upsert_position(self, position: StrategyPosition) -> StrategyPosition:
        now = _now_ms()
        existing = self.get_position(
            strategy_id=position.strategy_id,
            account_id=position.account_id,
            exchange=position.exchange,
            market_type=position.market_type,
            symbol=position.symbol,
            price_type=position.price_type,
            timeframe=position.timeframe,
        )
        position_id = (
            existing.position_id if existing is not None else position.position_id
        )
        created_at = (
            existing.created_at if existing is not None else position.created_at
        )
        self.storage.execute(
            """
            INSERT INTO strategy_positions
              (position_id, strategy_id, account_id, exchange, market_type, symbol,
               price_type, timeframe, source, side, qty, avg_price, realized_pnl,
               unrealized_pnl, opened_at, last_bar_time, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id, account_id, exchange, market_type, symbol, price_type, timeframe)
            DO UPDATE SET
                source = excluded.source,
                side = excluded.side,
                qty = excluded.qty,
                avg_price = excluded.avg_price,
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                opened_at = excluded.opened_at,
                last_bar_time = excluded.last_bar_time,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                position_id,
                position.strategy_id,
                position.account_id,
                position.exchange.lower(),
                position.market_type.lower(),
                position.symbol.upper(),
                position.price_type.lower(),
                position.timeframe,
                position.source.value,
                position.side.value,
                position.qty,
                position.avg_price,
                position.realized_pnl,
                position.unrealized_pnl,
                position.opened_at,
                position.last_bar_time,
                _json_dumps(position.metadata),
                created_at,
                now,
            ),
        )
        self.storage.commit()
        position.position_id = position_id
        position.created_at = created_at
        position.updated_at = now
        return position

    def get_position(
        self,
        *,
        strategy_id: str,
        account_id: str = "",
        exchange: str,
        market_type: str,
        symbol: str,
        price_type: str = "trade",
        timeframe: str,
    ) -> StrategyPosition | None:
        row = self.storage.execute(
            """
            SELECT * FROM strategy_positions
            WHERE strategy_id = ? AND account_id = ? AND exchange = ?
              AND market_type = ? AND symbol = ? AND price_type = ? AND timeframe = ?
            """,
            (
                strategy_id,
                account_id,
                exchange.lower(),
                market_type.lower(),
                symbol.upper(),
                price_type.lower(),
                timeframe,
            ),
        ).fetchone()
        return _row_to_position(row) if row is not None else None

    def record_trade(self, trade: StrategyTrade) -> StrategyTrade:
        now = _now_ms()
        trade.updated_at = now
        self.storage.execute(
            """
            INSERT INTO strategy_trades
              (trade_id, strategy_id, account_id, run_id, order_id, exchange, market_type,
               symbol, price_type, timeframe, source, status, entry_id, exit_id, direction,
               entry_time, exit_time, entry_price, exit_price, qty, gross_pnl, net_pnl, fee,
               bars_held, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.strategy_id,
                trade.account_id,
                trade.run_id,
                trade.order_id,
                trade.exchange.lower(),
                trade.market_type.lower(),
                trade.symbol.upper(),
                trade.price_type.lower(),
                trade.timeframe,
                trade.source.value,
                trade.status.value,
                trade.entry_id,
                trade.exit_id,
                trade.direction,
                trade.entry_time,
                trade.exit_time,
                trade.entry_price,
                trade.exit_price,
                trade.qty,
                trade.gross_pnl,
                trade.net_pnl,
                trade.fee,
                trade.bars_held,
                _json_dumps(trade.metadata),
                trade.created_at,
                trade.updated_at,
            ),
        )
        self.storage.commit()
        return trade

    def list_trades(
        self,
        *,
        strategy_id: str | None = None,
        source: LedgerSource | None = None,
        status: TradeStatus | None = None,
    ) -> list[StrategyTrade]:
        conditions: list[str] = []
        params: list[object] = []
        if strategy_id is not None:
            conditions.append("strategy_id = ?")
            params.append(strategy_id)
        if source is not None:
            conditions.append("source = ?")
            params.append(source.value)
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.storage.execute(
            f"SELECT * FROM strategy_trades {where_clause} ORDER BY entry_time, trade_id",
            tuple(params),
        ).fetchall()
        return [_row_to_trade(row) for row in rows]


def _row_to_position(row: tuple) -> StrategyPosition:
    return StrategyPosition(
        position_id=row[0],
        strategy_id=row[1],
        account_id=row[2] or "",
        exchange=row[3],
        market_type=row[4],
        symbol=row[5],
        price_type=row[6],
        timeframe=row[7],
        source=LedgerSource(row[8]),
        side=PositionSide(row[9]),
        qty=row[10],
        avg_price=row[11],
        realized_pnl=row[12],
        unrealized_pnl=row[13],
        opened_at=row[14],
        last_bar_time=row[15],
        metadata=_json_loads(row[16]),
        created_at=row[17],
        updated_at=row[18],
    )


def _row_to_trade(row: tuple) -> StrategyTrade:
    return StrategyTrade(
        trade_id=row[0],
        strategy_id=row[1],
        account_id=row[2] or "",
        run_id=row[3],
        order_id=row[4],
        exchange=row[5],
        market_type=row[6],
        symbol=row[7],
        price_type=row[8],
        timeframe=row[9],
        source=LedgerSource(row[10]),
        status=TradeStatus(row[11]),
        entry_id=row[12],
        exit_id=row[13],
        direction=row[14],
        entry_time=row[15],
        exit_time=row[16],
        entry_price=row[17],
        exit_price=row[18],
        qty=row[19],
        gross_pnl=row[20],
        net_pnl=row[21],
        fee=row[22],
        bars_held=row[23],
        metadata=_json_loads(row[24]),
        created_at=row[25],
        updated_at=row[26],
    )


__all__ = [
    "LedgerSource",
    "PositionSide",
    "StrategyLedger",
    "StrategyPosition",
    "StrategyTrade",
    "TradeStatus",
    "generate_position_id",
    "generate_trade_id",
]
