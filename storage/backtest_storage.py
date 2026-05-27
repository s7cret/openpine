"""Backtest result storage for OpenPine.

Stores summary metrics in SQLite and large time-series as Parquet artifacts.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from openpine.config import DEFAULT_CONFIG
from openpine.storage.sqlite_storage import SQLiteStorage


class BacktestStorage:
    """Storage for backtest runs, trades, and artifacts."""

    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        if storage is None:
            # Use the real ~/.openpine path, not workspace-relative
            from pathlib import Path
            real_path = Path("~/.openpine/openpine.sqlite").expanduser()
            self._storage = SQLiteStorage(real_path)
        else:
            self._storage = storage
        self._data_dir = Path("~/.openpine/data/backtests").expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, strategy_id: str, run_id: str) -> Path:
        return self._data_dir / strategy_id / run_id

    def save_run(
        self,
        strategy_id: str,
        pine_id: str,
        artifact_id: str,
        params_hash: str,
        symbol: str,
        timeframe: str,
        exchange: str,
        market_type: str,
        start_ms: int,
        end_ms: int,
        raw_result: Any,
        status: str = "completed",
        error_message: str | None = None,
    ) -> str:
        """Save a backtest run. Returns run_id."""
        run_id = f"run_{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"
        now = int(time.time() * 1000)

        # Extract metrics from backtest result
        metrics = {
            "initial_capital": getattr(raw_result, "initial_capital", None),
            "final_equity": getattr(raw_result, "final_equity", None),
            "net_profit": getattr(raw_result, "net_profit", None),
            "net_profit_percent": getattr(raw_result, "net_profit_percent", None),
            "gross_profit": getattr(raw_result, "gross_profit", None),
            "gross_loss": getattr(raw_result, "gross_loss", None),
            "profit_factor": getattr(raw_result, "profit_factor", None),
            "max_drawdown": getattr(raw_result, "max_drawdown", None),
            "max_drawdown_percent": getattr(raw_result, "max_drawdown_percent", None),
            "sharpe_ratio": getattr(raw_result, "sharpe_ratio", None),
            "sortino_ratio": getattr(raw_result, "sortino_ratio", None),
            "win_rate": getattr(raw_result, "win_rate", None),
            "total_trades": getattr(raw_result, "total_trades", None),
            "winning_trades": getattr(raw_result, "winning_trades", None),
            "losing_trades": getattr(raw_result, "losing_trades", None),
            "avg_trade": getattr(raw_result, "avg_trade", None),
            "avg_win": getattr(raw_result, "avg_win", None),
            "avg_loss": getattr(raw_result, "avg_loss", None),
            "largest_win": getattr(raw_result, "largest_win", None),
            "largest_loss": getattr(raw_result, "largest_loss", None),
            "avg_bars_in_trade": getattr(raw_result, "avg_bars_in_trade", None),
            "commission_total": getattr(raw_result, "commission_total", None),
            "expectancy": getattr(raw_result, "expectancy", None),
        }

        with self._storage.transaction():
            self._storage.execute(
                """
                INSERT INTO backtest_runs
                (run_id, strategy_id, pine_id, artifact_id, params_hash,
                 exchange, market_type, symbol, price_type, timeframe,
                 from_time, to_time, warmup_bars, status, started_at, finished_at,
                 initial_capital, final_equity, net_profit, net_profit_percent,
                 gross_profit, gross_loss, profit_factor, max_drawdown,
                 max_drawdown_percent, sharpe_ratio, sortino_ratio, win_rate,
                 total_trades, winning_trades, losing_trades, avg_trade,
                 avg_win, avg_loss, largest_win, largest_loss, avg_bars_in_trade,
                 commission_total, expectancy, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, strategy_id, pine_id, artifact_id, params_hash,
                    exchange, market_type, symbol, "trade", timeframe,
                    start_ms, end_ms, 0, status, now, now,
                    metrics["initial_capital"], metrics["final_equity"],
                    metrics["net_profit"], metrics["net_profit_percent"],
                    metrics["gross_profit"], metrics["gross_loss"],
                    metrics["profit_factor"], metrics["max_drawdown"],
                    metrics["max_drawdown_percent"], metrics["sharpe_ratio"],
                    metrics["sortino_ratio"], metrics["win_rate"],
                    metrics["total_trades"], metrics["winning_trades"],
                    metrics["losing_trades"], metrics["avg_trade"],
                    metrics["avg_win"], metrics["avg_loss"],
                    metrics["largest_win"], metrics["largest_loss"],
                    metrics["avg_bars_in_trade"], metrics["commission_total"],
                    metrics["expectancy"], error_message,
                ),
            )

            # Save trades
            trades = getattr(raw_result, "closed_trades", []) + getattr(raw_result, "open_trades", [])
            if trades:
                trade_rows = []
                for i, t in enumerate(trades):
                    trade_rows.append((
                        f"{run_id}_trade_{i}",
                        run_id,
                        t.direction,
                        t.entry_time,
                        t.entry_price,
                        getattr(t, "exit_time", None),
                        getattr(t, "exit_price", None),
                        t.qty,
                        getattr(t, "profit", None),
                        getattr(t, "profit_percent", None),
                        getattr(t, "mfe", None),
                        getattr(t, "mae", None),
                        getattr(t, "exit_reason", None),
                        getattr(t, "bars_held", None),
                        getattr(t, "is_open", False),
                    ))
                self._storage.execute_many(
                    """
                    INSERT INTO backtest_trades
                    (trade_id, run_id, direction, entry_time, entry_price,
                     exit_time, exit_price, qty, profit, profit_percent,
                     mfe, mae, exit_reason, bars_held, is_open)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    trade_rows,
                )

        # Save artifacts (Parquet files)
        run_dir = self._run_dir(strategy_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Equity curve
        equity_curve = getattr(raw_result, "equity_curve", None)
        if equity_curve:
            eq_records = []
            for e in equity_curve:
                eq_records.append({
                    "time": e.time,
                    "equity": e.equity,
                    "cash": e.cash,
                    "position_size": e.position_size,
                    "position_avg_price": e.position_avg_price,
                    "open_profit": e.open_profit,
                    "realized_profit": e.realized_profit,
                    "drawdown": e.drawdown,
                    "drawdown_percent": e.drawdown_percent,
                })
            eq_df = pd.DataFrame(eq_records)
            eq_path = run_dir / "equity_curve.parquet"
            pq.write_table(pa.Table.from_pandas(eq_df), str(eq_path), compression="zstd")
            eq_checksum = hashlib.sha256(eq_path.read_bytes()).hexdigest()
            with self._storage.transaction():
                self._storage.execute(
                    """
                    INSERT INTO backtest_artifacts
                    (artifact_id, run_id, artifact_type, file_path, file_size, row_count, checksum, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"{run_id}_equity", run_id, "equity_curve", str(eq_path),
                     eq_path.stat().st_size, len(eq_records), eq_checksum, now),
                )

        # Trades artifact (optional, for convenience)
        if trades:
            trade_records = []
            for t in trades:
                trade_records.append({
                    "trade_id": t.id,
                    "direction": t.direction,
                    "entry_time": t.entry_time,
                    "entry_price": t.entry_price,
                    "exit_time": getattr(t, "exit_time", None),
                    "exit_price": getattr(t, "exit_price", None),
                    "qty": t.qty,
                    "profit": getattr(t, "profit", None),
                    "profit_percent": getattr(t, "profit_percent", None),
                    "mfe": getattr(t, "mfe", None),
                    "mae": getattr(t, "mae", None),
                    "exit_reason": getattr(t, "exit_reason", None),
                    "bars_held": getattr(t, "bars_held", None),
                    "is_open": getattr(t, "is_open", False),
                })
            trades_df = pd.DataFrame(trade_records)
            trades_path = run_dir / "trades.parquet"
            pq.write_table(pa.Table.from_pandas(trades_df), str(trades_path), compression="zstd")
            trades_checksum = hashlib.sha256(trades_path.read_bytes()).hexdigest()
            with self._storage.transaction():
                self._storage.execute(
                    """
                    INSERT INTO backtest_artifacts
                    (artifact_id, run_id, artifact_type, file_path, file_size, row_count, checksum, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (f"{run_id}_trades", run_id, "trades", str(trades_path),
                     trades_path.stat().st_size, len(trade_records), trades_checksum, now),
                )

        # Report JSON
        report = {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "started_at": now,
            "status": status,
            **{k: v for k, v in metrics.items() if v is not None},
        }
        report_path = run_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2, default=str))
        with self._storage.transaction():
            self._storage.execute(
                """
                INSERT INTO backtest_artifacts
                (artifact_id, run_id, artifact_type, file_path, file_size, row_count, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"{run_id}_report", run_id, "report_json", str(report_path),
                 report_path.stat().st_size, 0, "", now),
            )

        return run_id

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Get a backtest run by ID."""
        row = self._storage.execute(
            "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self._storage.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).description]
        return dict(zip(cols, row))

    def list_runs(self, strategy_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List backtest runs, optionally filtered by strategy."""
        if strategy_id:
            rows = self._storage.execute(
                "SELECT * FROM backtest_runs WHERE strategy_id = ? ORDER BY started_at DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
        else:
            rows = self._storage.execute(
                "SELECT * FROM backtest_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._storage.execute("SELECT * FROM backtest_runs LIMIT 1").description]
        return [dict(zip(cols, row)) for row in rows]

    def get_trades(self, run_id: str) -> list[dict[str, Any]]:
        """Get trades for a run."""
        rows = self._storage.execute(
            "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_time",
            (run_id,),
        ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._storage.execute("SELECT * FROM backtest_trades LIMIT 1").description]
        return [dict(zip(cols, row)) for row in rows]

    def get_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Get artifacts for a run."""
        rows = self._storage.execute(
            "SELECT * FROM backtest_artifacts WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self._storage.execute("SELECT * FROM backtest_artifacts LIMIT 1").description]
        return [dict(zip(cols, row)) for row in rows]

    def close(self) -> None:
        self._storage.close()
