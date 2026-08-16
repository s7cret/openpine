"""StrategyRegistry — in-memory + SQLite persistence for StrategyInstances."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, Protocol, TypeVar, cast

from openpine.config import DEFAULT_CONFIG


def _is_missing_optional_schema_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "no such table" in message or "no such column" in message


class WorkerCircuitOpenError(RuntimeError):
    """Raised when durable worker fail-safe state forbids strategy activation."""


class ArchivedStrategyActivationError(RuntimeError):
    """Raised when an archived strategy is activated through any ingress."""


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
    market_type: str = "spot"
    price_type: str = "trade"
    mode: str = "paper"
    enabled: bool = False
    archived: bool = False
    status: str = "pending"
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))
    semantic_profile: str | None = None

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
            "price_type": self.price_type,
            "mode": self.mode,
            "enabled": self.enabled,
            "archived": self.archived,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "semantic_profile": self.semantic_profile,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StrategyInstance:
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
            market_type=data.get("market_type", "spot"),
            price_type=data.get("price_type", "trade"),
            mode=data.get("mode", "paper"),
            enabled=data.get("enabled", False),
            archived=data.get("archived", False),
            status=data.get("status", "pending"),
            created_at=data.get("created_at", int(time.time() * 1000)),
            updated_at=data.get("updated_at", int(time.time() * 1000)),
            semantic_profile=data.get("semantic_profile"),
        )


def _make_params_hash(params: dict) -> str:
    """Derive a stable hash from params dict."""
    normalized = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


class StrategyRegistry(Protocol):
    """Protocol for strategy registry — section 7.3."""

    def register_strategy(
        self,
        artifact_id: str,
        symbol: str,
        timeframe: str,
        params: dict,
        name: str | None = None,
        exchange: str = "binance",
        market_type: str = "spot",
        price_type: str = "trade",
        mode: str = "paper",
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


_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S")


def _serialized_connection(
    method: Callable[Concatenate[_S, _P], _R],
) -> Callable[Concatenate[_S, _P], _R]:
    """Hold the registry RLock for every shared SQLite connection operation."""

    @wraps(method)
    def locked(self: _S, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        with cast(Any, self)._lock:
            return method(self, *args, **kwargs)

    return locked


class SQLiteStrategyRegistry:
    """StrategyRegistry backed by in-memory dict + SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = DEFAULT_CONFIG.sqlite_path
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, StrategyInstance] = {}
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._init_db()

    @_serialized_connection
    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_instances (
                id TEXT PRIMARY KEY,
                strategy_id TEXT UNIQUE,
                name TEXT UNIQUE,
                pine_id TEXT,
                artifact_id TEXT NOT NULL,
                params_json TEXT NOT NULL DEFAULT '{}',
                params_hash TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT DEFAULT 'BINANCE',
                market_type TEXT NOT NULL DEFAULT 'spot',
                price_type TEXT NOT NULL DEFAULT 'trade',
                timeframe TEXT NOT NULL,
                data_provider TEXT NOT NULL DEFAULT 'local',
                execution_provider TEXT NOT NULL DEFAULT 'paper',
                mode TEXT NOT NULL DEFAULT 'disabled',
                enabled INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                live_enabled INTEGER NOT NULL DEFAULT 0,
                risk_profile_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                semantic_profile TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (pine_id) REFERENCES pine_sources(id),
                FOREIGN KEY (artifact_id) REFERENCES compile_artifacts(id)
            )
        """)
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(strategy_instances)").fetchall()
        }
        if "archived" not in columns:
            self._conn.execute(
                "ALTER TABLE strategy_instances ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
        if "semantic_profile" not in columns:
            self._conn.execute(
                "ALTER TABLE strategy_instances ADD COLUMN semantic_profile TEXT"
            )
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_instances_pine_id
            ON strategy_instances(pine_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_instances_status
            ON strategy_instances(status)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_circuits (
                name TEXT PRIMARY KEY,
                is_open INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            """INSERT OR IGNORE INTO runtime_circuits(name, is_open, reason, updated_at)
               VALUES ('background_worker', 0, NULL, ?)""",
            (int(time.time() * 1000),),
        )
        self._conn.commit()
        self._reload_from_db()

    @_serialized_connection
    def _reload_from_db(self) -> None:
        """Refresh the in-memory view from SQLite.

        The gateway background worker is forked from the API process. Without
        reloading, it never sees strategies created after worker startup.
        """
        rows = self._conn.execute(
            """SELECT strategy_id, name, pine_id, artifact_id, params_json, params_hash,
                      symbol, timeframe, exchange, market_type, price_type, mode, enabled, archived, status,
                      created_at, updated_at, semantic_profile
               FROM strategy_instances
               WHERE strategy_id IS NOT NULL"""
        ).fetchall()
        self._mem = {
            row[0]: StrategyInstance(
                strategy_id=row[0],
                name=row[1],
                pine_id=row[2] or "",
                artifact_id=row[3],
                params_json=row[4],
                params_hash=row[5],
                symbol=row[6],
                timeframe=row[7],
                exchange=row[8],
                market_type=row[9],
                price_type=row[10],
                mode=row[11],
                enabled=bool(row[12]),
                archived=bool(row[13]),
                status=row[14],
                created_at=row[15],
                updated_at=row[16],
                semantic_profile=row[17],
            )
            for row in rows
        }

    @_serialized_connection
    def register_strategy(
        self,
        artifact_id: str,
        symbol: str,
        timeframe: str,
        params: dict,
        name: str | None = None,
        pine_id: str = "",
        exchange: str = "binance",
        market_type: str = "spot",
        price_type: str = "trade",
        mode: str = "paper",
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
            exchange=exchange,
            market_type=market_type,
            price_type=price_type,
            mode=mode,
            created_at=now,
            updated_at=now,
        )

        self._conn.execute(
            """INSERT INTO strategy_instances
               (id, strategy_id, name, pine_id, artifact_id, params_json, params_hash,
                symbol, exchange, market_type, price_type, timeframe, data_provider,
                execution_provider, mode, enabled, archived, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy_id,
                si.strategy_id,
                si.name,
                si.pine_id,
                si.artifact_id,
                si.params_json,
                si.params_hash,
                si.symbol,
                si.exchange,
                si.market_type,
                si.price_type,
                si.timeframe,
                "local",
                "paper",
                si.mode,
                int(si.enabled),
                int(si.archived),
                si.status,
                si.created_at,
                si.updated_at,
            ),
        )
        self._conn.commit()
        self._mem[si.strategy_id] = si
        return si

    @_serialized_connection
    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        """Get a strategy instance by id."""
        self._reload_from_db()
        if strategy_id in self._mem:
            return self._mem[strategy_id]
        raise KeyError(f"StrategyInstance not found: {strategy_id!r}")

    @_serialized_connection
    def list_strategies(self, status: str | None = None) -> list[StrategyInstance]:
        """List strategy instances, optionally filtered by status."""
        self._reload_from_db()
        if status is None:
            return list(self._mem.values())
        return [s for s in self._mem.values() if s.status == status]

    @_serialized_connection
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

    @_serialized_connection
    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @_serialized_connection
    def create_strategy(
        self,
        *,
        name: str,
        pine_id: str,
        artifact_id: str,
        symbol: str,
        timeframe: str,
        exchange: str = "binance",
        market_type: str = "spot",
        params_json: str = "{}",
        params_hash: str | None = None,
        mode: str = "paper",
    ) -> StrategyInstance:
        """Create a new strategy instance (gateway-facing API)."""
        import hashlib

        now = int(time.time() * 1000)
        if params_hash is None:
            params_hash = hashlib.sha256(params_json.encode()).hexdigest()[:16]
        strategy_id = f"strat_{hashlib.sha256(f'{artifact_id}{params_hash}{now}'.encode()).hexdigest()[:16]}_{now}"
        si = StrategyInstance(
            strategy_id=strategy_id,
            name=name,
            pine_id=pine_id,
            artifact_id=artifact_id,
            params_json=params_json,
            params_hash=params_hash,
            symbol=symbol,
            timeframe=timeframe,
            exchange=exchange,
            market_type=market_type,
            mode=mode,
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            """INSERT INTO strategy_instances
               (id, strategy_id, name, pine_id, artifact_id, params_json, params_hash,
                symbol, exchange, market_type, price_type, timeframe, data_provider,
                execution_provider, mode, enabled, archived, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy_id,
                si.strategy_id,
                si.name,
                si.pine_id,
                si.artifact_id,
                si.params_json,
                si.params_hash,
                si.symbol,
                si.exchange,
                si.market_type,
                si.price_type,
                si.timeframe,
                "local",
                "paper",
                si.mode,
                int(si.enabled),
                int(si.archived),
                si.status,
                si.created_at,
                si.updated_at,
            ),
        )
        self._conn.commit()
        self._mem[si.strategy_id] = si
        return si

    @_serialized_connection
    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        """Enable or disable a strategy."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                _current_enabled, archived, _status, _mode = (
                    self._strategy_execution_state_locked(strategy_id)
                )
                if enabled and archived:
                    raise ArchivedStrategyActivationError(
                        "Archived strategy cannot be enabled"
                    )
                if enabled and self._worker_circuit_state_locked()["open"]:
                    raise WorkerCircuitOpenError("Background worker circuit is open")
                now = int(time.time() * 1000)
                self._conn.execute(
                    "UPDATE strategy_instances SET enabled = ?, updated_at = ? WHERE strategy_id = ?",
                    (int(enabled), now, strategy_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()

    @_serialized_connection
    def activate_strategy(
        self,
        strategy_id: str,
        *,
        status: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Atomically check the durable circuit and activate one strategy."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                _enabled, archived, current_status, current_mode = (
                    self._strategy_execution_state_locked(strategy_id)
                )
                if archived:
                    raise ArchivedStrategyActivationError(
                        "Archived strategy cannot be activated"
                    )
                if self._worker_circuit_state_locked()["open"]:
                    raise WorkerCircuitOpenError("Background worker circuit is open")
                now = int(time.time() * 1000)
                next_status = status or current_status
                next_mode = mode or current_mode
                self._conn.execute(
                    """UPDATE strategy_instances
                       SET enabled = 1, status = ?, mode = ?, updated_at = ?
                       WHERE strategy_id = ?""",
                    (next_status, next_mode, now, strategy_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()

    @_serialized_connection
    def transition_strategy(
        self,
        strategy_id: str,
        *,
        enabled: bool | None = None,
        status: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Apply execution state fields in one transaction and one memory update."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                current_enabled, archived, current_status, current_mode = (
                    self._strategy_execution_state_locked(strategy_id)
                )
                next_enabled = current_enabled if enabled is None else enabled
                if next_enabled and archived:
                    raise ArchivedStrategyActivationError(
                        "Archived strategy cannot be enabled"
                    )
                if next_enabled and self._worker_circuit_state_locked()["open"]:
                    raise WorkerCircuitOpenError("Background worker circuit is open")
                next_status = current_status if status is None else status
                next_mode = current_mode if mode is None else mode
                now = int(time.time() * 1000)
                self._conn.execute(
                    """UPDATE strategy_instances
                       SET enabled = ?, status = ?, mode = ?, updated_at = ?
                       WHERE strategy_id = ?""",
                    (int(next_enabled), next_status, next_mode, now, strategy_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()

    @_serialized_connection
    def patch_strategy_atomic(
        self, strategy_id: str, updates: dict[str, object]
    ) -> None:
        """Apply one public PATCH as a single durable registry transaction."""
        allowed = {
            "enabled",
            "archived",
            "mode",
            "status",
            "name",
            "symbol",
            "timeframe",
            "exchange",
            "market_type",
            "params_json",
            "params_hash",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"Unsupported strategy fields: {sorted(unknown)}")
        with self._lock:
            values = dict(updates)
            if not values:
                return
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                current_enabled, current_archived, current_status, _mode = (
                    self._strategy_execution_state_locked(strategy_id)
                )
                next_archived = bool(
                    values.get("archived", current_archived)
                )
                requested_enabled = values.get("enabled")
                if requested_enabled and next_archived:
                    raise ArchivedStrategyActivationError(
                        "Archived strategy cannot be enabled"
                    )
                next_enabled = (
                    False
                    if values.get("archived")
                    else bool(values.get("enabled", current_enabled))
                )
                if next_enabled and self._worker_circuit_state_locked()["open"]:
                    raise WorkerCircuitOpenError("Background worker circuit is open")
                if values.get("archived"):
                    values["enabled"] = False
                    if values.get("status", current_status) in {
                        "running",
                        "pending",
                    }:
                        values["status"] = "paused"
                values["updated_at"] = int(time.time() * 1000)
                columns = list(values)
                set_clause = ", ".join(f"{column} = ?" for column in columns)
                self._conn.execute(
                    f"UPDATE strategy_instances SET {set_clause} WHERE strategy_id = ?",
                    tuple(values[column] for column in columns) + (strategy_id,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()

    def _strategy_execution_state_locked(
        self, strategy_id: str
    ) -> tuple[bool, bool, str, str]:
        row = self._conn.execute(
            """SELECT enabled, archived, status, mode
               FROM strategy_instances WHERE strategy_id = ?""",
            (strategy_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Strategy not found: {strategy_id}")
        return bool(row[0]), bool(row[1]), str(row[2]), str(row[3])

    @_serialized_connection
    def _worker_circuit_state_locked(self) -> dict[str, object]:
        row = self._conn.execute(
            "SELECT is_open, reason, updated_at FROM runtime_circuits WHERE name = ?",
            ("background_worker",),
        ).fetchone()
        return {
            "open": bool(row and row[0]),
            "reason": None if row is None else row[1],
            "updated_at": None if row is None else row[2],
        }

    @_serialized_connection
    def worker_circuit_state(self) -> dict[str, object]:
        with self._lock:
            return self._worker_circuit_state_locked()

    @_serialized_connection
    def reset_worker_circuit(self) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE runtime_circuits
                   SET is_open = 0, reason = NULL, updated_at = ?
                   WHERE name = 'background_worker'""",
                (int(time.time() * 1000),),
            )
            self._conn.commit()

    @_serialized_connection
    def trip_worker_circuit(self, reason: str) -> int:
        """Durably open the circuit and pause strategies in one transaction."""
        with self._lock:
            now = int(time.time() * 1000)
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """UPDATE runtime_circuits
                       SET is_open = 1, reason = ?, updated_at = ?
                       WHERE name = 'background_worker'""",
                    (reason, now),
                )
                cursor = self._conn.execute(
                    """UPDATE strategy_instances
                       SET enabled = 0, status = 'paused', updated_at = ?
                       WHERE enabled = 1""",
                    (now,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()
            return max(0, int(cursor.rowcount))

    @_serialized_connection
    def pause_all_enabled(self) -> int:
        """Atomically pause and disable every enabled strategy."""
        with self._lock:
            now = int(time.time() * 1000)
            cursor = self._conn.execute(
                """UPDATE strategy_instances
                   SET enabled = 0, status = 'paused', updated_at = ?
                   WHERE enabled = 1""",
                (now,),
            )
            self._conn.commit()
            self._reload_from_db()
            return max(0, int(cursor.rowcount))

    @_serialized_connection
    def set_archived(self, strategy_id: str, archived: bool) -> None:
        """Archive/unarchive a strategy. Archiving always disables it."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                enabled, _current_archived, status, _mode = (
                    self._strategy_execution_state_locked(strategy_id)
                )
                if archived:
                    enabled = False
                    if status in {"running", "pending"}:
                        status = "paused"
                now = int(time.time() * 1000)
                self._conn.execute(
                    """UPDATE strategy_instances
                       SET archived = ?, enabled = ?, status = ?, updated_at = ?
                       WHERE strategy_id = ?""",
                    (int(archived), int(enabled), status, now, strategy_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            self._reload_from_db()


    @_serialized_connection
    def update_mode(self, strategy_id: str, mode: str) -> None:
        """Update strategy execution mode (paper/live/observe)."""
        si = self.get_strategy(strategy_id)
        si.mode = mode
        si.updated_at = int(time.time() * 1000)
        self._conn.execute(
            "UPDATE strategy_instances SET mode = ?, updated_at = ? WHERE strategy_id = ?",
            (mode, si.updated_at, strategy_id),
        )
        self._conn.commit()

    @_serialized_connection
    def set_semantic_profile(self, strategy_id: str, semantic_profile: str) -> None:
        """Persist the admitted semantic profile across reload and restart."""
        si = self.get_strategy(strategy_id)
        si.semantic_profile = semantic_profile
        si.updated_at = int(time.time() * 1000)
        self._conn.execute(
            "UPDATE strategy_instances SET semantic_profile = ?, updated_at = ? WHERE strategy_id = ?",
            (semantic_profile, si.updated_at, strategy_id),
        )
        self._conn.commit()

    @_serialized_connection
    def delete_strategy(self, strategy_id: str) -> None:
        """Delete a strategy instance."""
        self.get_strategy(strategy_id)  # raises if not found
        import shutil

        # Cascade: delete backtest runs, trades, artifacts
        for table in ("backtest_trades", "backtest_artifacts", "backtest_runs"):
            try:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE strategy_id = ?", (strategy_id,)
                )
            except sqlite3.OperationalError as exc:
                if not _is_missing_optional_schema_error(exc):
                    raise
        # Delete orders
        try:
            order_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT order_id FROM orders WHERE strategy_id = ?", (strategy_id,)
                ).fetchall()
            ]
            if order_ids:
                placeholders = ",".join("?" for _ in order_ids)
                self._conn.execute(
                    f"DELETE FROM fills WHERE order_id IN ({placeholders})",
                    tuple(order_ids),
                )
            self._conn.execute(
                "DELETE FROM orders WHERE strategy_id = ?", (strategy_id,)
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_optional_schema_error(exc):
                raise
        # Delete positions
        try:
            self._conn.execute(
                "DELETE FROM strategy_positions WHERE strategy_id = ?", (strategy_id,)
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_optional_schema_error(exc):
                raise
        for table in ("strategy_trades", "strategy_state_snapshots", "jobs"):
            try:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE strategy_id = ?", (strategy_id,)
                )
            except sqlite3.OperationalError as exc:
                if not _is_missing_optional_schema_error(exc):
                    raise
        # Delete backtest data directory
        from openpine.config import DEFAULT_CONFIG

        bt_dir = DEFAULT_CONFIG.data_dir / "backtests" / strategy_id
        if bt_dir.exists():
            try:
                shutil.rmtree(bt_dir)
            except Exception as exc:
                self._conn.rollback()
                raise RuntimeError(
                    f"Failed to delete backtest data directory {bt_dir}: {exc}"
                ) from exc
        self._conn.execute(
            "DELETE FROM strategy_instances WHERE strategy_id = ?",
            (strategy_id,),
        )
        self._conn.commit()
        self._mem.pop(strategy_id, None)

    def _storage(self):
        """Expose internal connection for direct SQL (used by gateway PATCH)."""
        return self._conn
