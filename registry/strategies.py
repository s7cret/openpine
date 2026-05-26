"""StrategyRegistry — in-memory + SQLite persistence for StrategyInstances."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Protocol

from openpine.config import DEFAULT_CONFIG


@dataclass
class StrategyInstance:
    """Strategy instance — section 5.3."""

    strategy_id: str
    name: str
    pine_id: str
    artifact_id: str
    params_json: str
    params_hash: str
    symbol: str
    timeframe: str
    exchange: str = "binance"
    market_type: str = "usdm"
    mode: str = "paper"
    enabled: bool = False
    status: str = "pending"
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "pine_id": self.pine_id,
            "artifact_id": self.artifact_id,
            "params_json": self.params_json,
            "params_hash": self.params_hash,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "exchange": self.exchange,
            "market_type": self.market_type,
            "mode": self.mode,
            "enabled": self.enabled,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StrategyInstance":
        return cls(
            strategy_id=data["strategy_id"],
            name=data["name"],
            pine_id=data["pine_id"],
            artifact_id=data["artifact_id"],
            params_json=data["params_json"],
            params_hash=data["params_hash"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            exchange=data.get("exchange", "binance"),
            market_type=data.get("market_type", "usdm"),
            mode=data.get("mode", "paper"),
            enabled=data.get("enabled", False),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", int(time.time() * 1000)),
            updated_at=data.get("updated_at", int(time.time() * 1000)),
        )


def _make_params_hash(params: dict) -> str:
    """Derive a stable hash from params dict."""
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


class StrategyRegistry(Protocol):
    """Protocol for strategy registry — section 7.3."""

    def register_strategy(
        self, artifact_id: str, symbol: str, timeframe: str, params: dict, name: str | None = None
    ) -> StrategyInstance:
        """Register a new strategy instance."""
        ...

    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        """Get a strategy instance by id."""
        ...

    def list_strategies(self, status: str | None = None) -> list[StrategyInstance]:
        """List strategy instances, optionally filtered by status."""
        ...

    def update_status(self, strategy_id: str, status: str) -> None:
        """Update the status of a strategy."""
        ...


class SQLiteStrategyRegistry:
    """StrategyRegistry backed by in-memory dict + SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = DEFAULT_CONFIG.sqlite_path
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, StrategyInstance] = {}
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_instances (
                strategy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                pine_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                params_json TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'binance',
                market_type TEXT NOT NULL DEFAULT 'usdm',
                mode TEXT NOT NULL DEFAULT 'paper',
                enabled INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_instances_pine_id
            ON strategy_instances(pine_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_instances_status
            ON strategy_instances(status)
        """)
        self._conn.commit()
        # Load existing rows
        for row in self._conn.execute("SELECT strategy_id FROM strategy_instances"):
            (sid,) = row
            row_data = self._conn.execute(
                "SELECT * FROM strategy_instances WHERE strategy_id = ?", (sid,)
            ).fetchone()
            if row_data:
                self._mem[sid] = StrategyInstance(
                    strategy_id=row_data[0],
                    name=row_data[1],
                    pine_id=row_data[2],
                    artifact_id=row_data[3],
                    params_json=row_data[4],
                    params_hash=row_data[5],
                    symbol=row_data[6],
                    timeframe=row_data[7],
                    exchange=row_data[8],
                    market_type=row_data[9],
                    mode=row_data[10],
                    enabled=bool(row_data[11]),
                    status=row_data[12],
                    created_at=row_data[13],
                    updated_at=row_data[14],
                )

    def register_strategy(
        self,
        artifact_id: str,
        symbol: str,
        timeframe: str,
        params: dict,
        name: str | None = None,
        pine_id: str = "",
    ) -> StrategyInstance:
        """Register a new strategy instance."""
        now = int(time.time() * 1000)
        params_json = json.dumps(params, sort_keys=True)
        params_hash = _make_params_hash(params)
        strategy_id = f"strat_{hashlib.sha256(f'{artifact_id}{params_hash}{now}'.encode()).hexdigest()[:16]}_{now}"
        strategy_name = name or f"{symbol}_{timeframe}_{params_hash[:6]}"

        si = StrategyInstance(
            strategy_id=strategy_id,
            name=strategy_name,
            pine_id=pine_id,
            artifact_id=artifact_id,
            params_json=params_json,
            params_hash=params_hash,
            symbol=symbol,
            timeframe=timeframe,
            created_at=now,
            updated_at=now,
        )

        self._conn.execute(
            """INSERT INTO strategy_instances
               (strategy_id, name, pine_id, artifact_id, params_json, params_hash,
                symbol, timeframe, exchange, market_type, mode, enabled, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (si.strategy_id, si.name, si.pine_id, si.artifact_id, si.params_json,
             si.params_hash, si.symbol, si.timeframe, si.exchange, si.market_type,
             si.mode, int(si.enabled), si.status, si.created_at, si.updated_at),
        )
        self._conn.commit()
        self._mem[si.strategy_id] = si
        return si

    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        """Get a strategy instance by id."""
        if strategy_id in self._mem:
            return self._mem[strategy_id]
        raise KeyError(f"StrategyInstance not found: {strategy_id!r}")

    def list_strategies(self, status: str | None = None) -> list[StrategyInstance]:
        """List strategy instances, optionally filtered by status."""
        if status is None:
            return list(self._mem.values())
        return [s for s in self._mem.values() if s.status == status]

    def update_status(self, strategy_id: str, status: str) -> None:
        """Update the status of a strategy."""
        si = self.get_strategy(strategy_id)
        si.status = status
        si.updated_at = int(time.time() * 1000)
        self._conn.execute(
            "UPDATE strategy_instances SET status = ?, updated_at = ? WHERE strategy_id = ?",
            (status, si.updated_at, si.strategy_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# Needed for type hint
from pathlib import Path
