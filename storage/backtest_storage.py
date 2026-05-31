"""Backtest result storage for OpenPine.

Implements the BacktestResultStore contract.
Stores summary metrics in SQLite, large time-series as Parquet artifacts.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from openpine.config import OpenPineConfig
from openpine.storage.backtest_dto import (
    ARTIFACT_TYPE_BAR_OUTPUTS,
    ARTIFACT_TYPE_EQUITY_CURVE,
    ARTIFACT_TYPE_PLOT_OUTPUTS,
    ARTIFACT_TYPE_REPORT_JSON,
    ARTIFACT_TYPE_REPORT_MD,
    ARTIFACT_TYPE_TRADES,
    BacktestArtifact,
    BacktestMetricsSummary,
    BacktestRun,
    BacktestRunRequest,
    BacktestTrade,
)
from openpine.storage.sqlite_storage import SQLiteStorage


class BacktestResultStore:
    """Storage for backtest runs, trades, and artifacts."""

    def __init__(self, storage: SQLiteStorage | None = None) -> None:
        config = OpenPineConfig.load()
        if storage is None:
            self._storage = SQLiteStorage(config.sqlite_path)
        else:
            self._storage = storage
        self._data_dir = config.data_dir / "backtests"
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _run_dir(self, strategy_id: str, run_id: str) -> Path:
        return self._data_dir / strategy_id / run_id

    def create_run(self, request: BacktestRunRequest) -> str:
        """Create a new backtest run record. Returns run_id."""
        run_id = f"run_{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"
        now = int(time.time() * 1000)

        self._storage.execute(
            """
            INSERT INTO backtest_runs
            (run_id, strategy_id, pine_id, artifact_id, params_hash,
             exchange, market_type, symbol, price_type, timeframe,
             from_time, to_time, warmup_bars, status, started_at, finished_at,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, request.strategy_id, request.pine_id, request.artifact_id,
                request.params_hash, request.exchange, request.market_type,
                request.symbol, request.price_type, request.timeframe,
                request.from_time, request.to_time, request.warmup_bars,
                "running", now, None, now, now,
            ),
        )
        self._storage.commit()
        return run_id

    def mark_running(self, run_id: str) -> None:
        """Mark a run as running."""
        self._storage.execute(
            "UPDATE backtest_runs SET status = ? WHERE run_id = ?",
            ("running", run_id),
        )
        self._storage.commit()

    def save_result(
        self,
        run_id: str,
        result: Any,
        trades: list[Any],
        equity_curve: list[Any] | None = None,
        bar_outputs: list[dict] | None = None,
        plots: Any = None,
    ) -> None:
        """Save backtest result, trades, and artifacts atomically."""
        now = int(time.time() * 1000)
        strategy_id = self._get_strategy_id(run_id)
        run_dir = self._run_dir(strategy_id, run_id)
        tmp_dir = run_dir.with_suffix(".tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Extract metrics
            metrics = self._metrics_from_result(result)

            result_json = json.dumps(self._result_json_payload(metrics), default=str)

            artifact_paths = self._write_result_artifacts(
                tmp_dir=tmp_dir,
                run_dir=run_dir,
                run_id=run_id,
                strategy_id=strategy_id,
                result=result,
                metrics=metrics,
                equity_curve=equity_curve,
                trades=trades,
                bar_outputs=bar_outputs,
                plots=plots,
                now=now,
            )

            self._publish_result_artifacts(
                tmp_dir=tmp_dir,
                run_dir=run_dir,
                artifact_paths=artifact_paths,
            )
            self._save_result_db_records(
                run_id=run_id,
                strategy_id=strategy_id,
                run_dir=run_dir,
                metrics=metrics,
                result_json=result_json,
                trades=trades,
                artifact_paths=artifact_paths,
                has_equity_curve=equity_curve is not None,
                has_bar_outputs=bar_outputs is not None,
                now=now,
            )

        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise

    def _publish_result_artifacts(
        self,
        *,
        tmp_dir: Path,
        run_dir: Path,
        artifact_paths: dict[str, Path],
    ) -> None:
        for atype, path in artifact_paths.items():
            if not path.exists():
                raise RuntimeError(f"Artifact validation failed: {atype} at {path}")
        if run_dir.exists():
            shutil.rmtree(run_dir)
        os.rename(str(tmp_dir), str(run_dir))

    def _save_result_db_records(
        self,
        *,
        run_id: str,
        strategy_id: str,
        run_dir: Path,
        metrics: BacktestMetricsSummary,
        result_json: str,
        trades: list[Any],
        artifact_paths: dict[str, Path],
        has_equity_curve: bool,
        has_bar_outputs: bool,
        now: int,
    ) -> None:
        with self._storage.transaction():
            self._update_run_result_row(
                run_id=run_id,
                run_dir=run_dir,
                metrics=metrics,
                result_json=result_json,
                has_equity_curve=has_equity_curve,
                has_bar_outputs=has_bar_outputs,
                has_plot_outputs=ARTIFACT_TYPE_PLOT_OUTPUTS in artifact_paths,
                now=now,
            )
            self._insert_trade_db_rows(
                run_id=run_id,
                strategy_id=strategy_id,
                trades=trades,
                now=now,
            )
            self._insert_artifact_db_rows(
                run_id=run_id,
                strategy_id=strategy_id,
                run_dir=run_dir,
                artifact_paths=artifact_paths,
                now=now,
            )
        self._storage.commit()

    def _insert_trade_db_rows(
        self,
        *,
        run_id: str,
        strategy_id: str,
        trades: list[Any],
        now: int,
    ) -> None:
        if not trades:
            return
        self._storage.execute_many(
            """
            INSERT INTO backtest_trades
            (trade_id, run_id, strategy_id, entry_id, exit_id, direction,
             entry_time, exit_time, entry_price, exit_price, qty,
             gross_pnl, net_pnl, net_pnl_pct, fee, slippage,
             bars_held, exit_reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._trade_db_rows(
                run_id=run_id,
                strategy_id=strategy_id,
                trades=trades,
                now=now,
            ),
        )

    def _insert_artifact_db_rows(
        self,
        *,
        run_id: str,
        strategy_id: str,
        run_dir: Path,
        artifact_paths: dict[str, Path],
        now: int,
    ) -> None:
        for atype in artifact_paths:
            self._storage.execute(
                """
                INSERT INTO backtest_artifacts
                (artifact_row_id, run_id, strategy_id, artifact_type, path, format, row_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._artifact_db_row(
                    run_id=run_id,
                    strategy_id=strategy_id,
                    artifact_type=atype,
                    path=run_dir / artifact_paths[atype].name,
                    now=now,
                ),
            )

    def _update_run_result_row(
        self,
        *,
        run_id: str,
        run_dir: Path,
        metrics: BacktestMetricsSummary,
        result_json: str,
        has_equity_curve: bool,
        has_bar_outputs: bool,
        has_plot_outputs: bool,
        now: int,
    ) -> None:
        self._storage.execute(
            """
            UPDATE backtest_runs SET
                status = ?,
                finished_at = ?,
                initial_capital = ?,
                final_equity = ?,
                net_profit = ?,
                net_profit_pct = ?,
                gross_profit = ?,
                gross_loss = ?,
                profit_factor = ?,
                max_drawdown = ?,
                max_drawdown_pct = ?,
                sharpe = ?,
                sortino = ?,
                calmar = ?,
                win_rate = ?,
                trades_total = ?,
                winning_trades = ?,
                losing_trades = ?,
                avg_trade = ?,
                avg_win = ?,
                avg_loss = ?,
                largest_win = ?,
                largest_loss = ?,
                avg_bars_in_trade = ?,
                commission_total = ?,
                expectancy = ?,
                result_json = ?,
                report_path = ?,
                equity_curve_path = ?,
                bar_outputs_path = ?,
                plot_outputs_path = ?,
                updated_at = ?
            WHERE run_id = ?
            """,
            (
                "done",
                now,
                metrics.initial_capital,
                metrics.final_equity,
                metrics.net_profit,
                metrics.net_profit_pct,
                metrics.gross_profit,
                metrics.gross_loss,
                metrics.profit_factor,
                metrics.max_drawdown,
                metrics.max_drawdown_pct,
                metrics.sharpe,
                metrics.sortino,
                metrics.calmar,
                metrics.win_rate,
                metrics.trades_total,
                metrics.winning_trades,
                metrics.losing_trades,
                metrics.avg_trade,
                metrics.avg_win,
                metrics.avg_loss,
                metrics.largest_win,
                metrics.largest_loss,
                metrics.avg_bars_in_trade,
                metrics.commission_total,
                metrics.expectancy,
                result_json,
                str(run_dir / "report.json"),
                str(run_dir / "equity_curve.parquet") if has_equity_curve else None,
                str(run_dir / "bar_outputs.parquet") if has_bar_outputs else None,
                str(run_dir / "plot_outputs.parquet") if has_plot_outputs else None,
                now,
                run_id,
            ),
        )

    @staticmethod
    def _trade_db_rows(
        *,
        run_id: str,
        strategy_id: str,
        trades: list[Any],
        now: int,
    ) -> list[tuple[Any, ...]]:
        return [
            (
                f"{run_id}_trade_{i}",
                run_id,
                strategy_id,
                t.id,
                getattr(t, "exit_id", None),
                t.direction,
                t.entry_time,
                getattr(t, "exit_time", None),
                t.entry_price,
                getattr(t, "exit_price", None),
                t.qty,
                getattr(t, "profit", None),
                getattr(t, "profit", None),
                getattr(t, "profit_percent", None),
                getattr(t, "commission_entry", 0) + getattr(t, "commission_exit", 0),
                0.0,
                getattr(t, "bars_held", None),
                getattr(t, "exit_reason", None),
                now,
            )
            for i, t in enumerate(trades)
        ]

    @staticmethod
    def _artifact_db_row(
        *,
        run_id: str,
        strategy_id: str,
        artifact_type: str,
        path: Path,
        now: int,
    ) -> tuple[Any, ...]:
        row_count = None
        if artifact_type in (
            ARTIFACT_TYPE_EQUITY_CURVE,
            ARTIFACT_TYPE_TRADES,
            ARTIFACT_TYPE_BAR_OUTPUTS,
            ARTIFACT_TYPE_PLOT_OUTPUTS,
        ):
            try:
                row_count = pq.read_table(str(path)).num_rows
            except Exception:
                pass
        return (
            f"{run_id}_{artifact_type}",
            run_id,
            strategy_id,
            artifact_type,
            str(path),
            "parquet" if path.suffix == ".parquet" else "json",
            row_count,
            now,
        )

    def mark_failed(
        self,
        run_id: str,
        error_message: str,
        traceback_id: str | None = None,
    ) -> None:
        """Mark a run as failed."""
        now = int(time.time() * 1000)
        self._storage.execute(
            """
            UPDATE backtest_runs SET
                status = ?,
                finished_at = ?,
                error_message = ?,
                traceback_id = ?,
                updated_at = ?
            WHERE run_id = ?
            """,
            ("failed", now, error_message, traceback_id, now, run_id),
        )
        self._storage.commit()

    def get_run(self, run_id: str) -> BacktestRun | None:
        """Get a backtest run by ID."""
        row = self._storage.execute(
            "SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def get_latest_run(self, strategy_id: str) -> BacktestRun | None:
        """Get the latest run for a strategy."""
        row = self._storage.execute(
            """
            SELECT * FROM backtest_runs
            WHERE strategy_id = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_run(row)

    def list_runs(self, strategy_id: str, limit: int = 20) -> list[BacktestRun]:
        """List backtest runs for a strategy."""
        rows = self._storage.execute(
            """
            SELECT * FROM backtest_runs
            WHERE strategy_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (strategy_id, limit),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def list_trades(self, run_id: str) -> list[BacktestTrade]:
        """List trades for a run."""
        rows = self._storage.execute(
            """
            SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_time
            """,
            (run_id,),
        ).fetchall()
        return [self._row_to_trade(row) for row in rows]

    def list_artifacts(self, run_id: str) -> list[BacktestArtifact]:
        """List artifacts for a run."""
        rows = self._storage.execute(
            """
            SELECT * FROM backtest_artifacts WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def _get_strategy_id(self, run_id: str) -> str:
        row = self._storage.execute(
            "SELECT strategy_id FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row:
            return row[0]
        return ""

    @staticmethod
    def _metrics_from_result(result: Any) -> BacktestMetricsSummary:
        return BacktestMetricsSummary(
            initial_capital=getattr(result, "initial_capital", None),
            final_equity=getattr(result, "final_equity", None),
            net_profit=getattr(result, "net_profit", None),
            net_profit_pct=getattr(result, "net_profit_percent", None),
            gross_profit=getattr(result, "gross_profit", None),
            gross_loss=getattr(result, "gross_loss", None),
            profit_factor=getattr(result, "profit_factor", None),
            max_drawdown=getattr(result, "max_drawdown", None),
            max_drawdown_pct=getattr(result, "max_drawdown_percent", None),
            sharpe=getattr(result, "sharpe_ratio", None),
            sortino=getattr(result, "sortino_ratio", None),
            calmar=None,
            win_rate=getattr(result, "win_rate", None),
            trades_total=getattr(result, "total_trades", 0),
            winning_trades=getattr(result, "winning_trades", 0),
            losing_trades=getattr(result, "losing_trades", 0),
            avg_trade=getattr(result, "avg_trade", None),
            avg_win=getattr(result, "avg_win", None),
            avg_loss=getattr(result, "avg_loss", None),
            largest_win=getattr(result, "largest_win", None),
            largest_loss=getattr(result, "largest_loss", None),
            avg_bars_in_trade=getattr(result, "avg_bars_in_trade", None),
            commission_total=getattr(result, "commission_total", None),
            expectancy=getattr(result, "expectancy", None),
        )

    @staticmethod
    def _equity_curve_records(equity_curve: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "time": e.time,
                "equity": e.equity,
                "cash": e.cash,
                "position_size": e.position_size,
                "position_avg_price": e.position_avg_price,
                "open_profit": e.open_profit,
                "realized_profit": e.realized_profit,
                "drawdown": e.drawdown,
                "drawdown_percent": e.drawdown_percent,
            }
            for e in equity_curve
        ]

    @staticmethod
    def _trade_artifact_records(trades: list[Any]) -> list[dict[str, Any]]:
        return [
            {
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
            }
            for t in trades
        ]

    @staticmethod
    def _plot_value(value: Any) -> Any:
        value = getattr(value, "_current", value)
        if value is not None and type(value).__name__ in ("PineNASentinel", "na"):
            return None
        return value

    @classmethod
    def _plot_records(cls, plots: Any) -> list[dict[str, Any]]:
        from pinelib.plot import PlotRecorder

        records = plots.get_records() if isinstance(plots, PlotRecorder) else plots
        plot_records: list[dict[str, Any]] = []
        for rec in records:
            if isinstance(rec, tuple) and len(rec) >= 4:
                plot_records.append(
                    {
                        "bar_time": rec[0],
                        "bar_index": rec[1],
                        "value": cls._plot_value(rec[2]),
                        "title": rec[3],
                    }
                )
            elif hasattr(rec, "bar_time"):
                plot_records.append(
                    {
                        "bar_time": rec.bar_time,
                        "bar_index": getattr(rec, "bar_index", None),
                        "value": cls._plot_value(rec.value),
                        "title": rec.title,
                    }
                )
        return plot_records

    @staticmethod
    def _write_parquet_artifact(tmp_dir: Path, filename: str, records: list[dict[str, Any]]) -> Path:
        path = tmp_dir / filename
        pq.write_table(pa.Table.from_pandas(pd.DataFrame(records)), str(path), compression="zstd")
        return path

    def _write_result_artifacts(
        self,
        *,
        tmp_dir: Path,
        run_dir: Path,
        run_id: str,
        strategy_id: str,
        result: Any,
        metrics: BacktestMetricsSummary,
        equity_curve: list[Any] | None,
        trades: list[Any],
        bar_outputs: list[dict] | None,
        plots: Any,
        now: int,
    ) -> dict[str, Path]:
        artifact_paths: dict[str, Path] = {}
        if equity_curve:
            artifact_paths[ARTIFACT_TYPE_EQUITY_CURVE] = self._write_parquet_artifact(
                tmp_dir, "equity_curve.parquet", self._equity_curve_records(equity_curve)
            )
        if trades:
            artifact_paths[ARTIFACT_TYPE_TRADES] = self._write_parquet_artifact(
                tmp_dir, "trades.parquet", self._trade_artifact_records(trades)
            )
        if bar_outputs:
            artifact_paths[ARTIFACT_TYPE_BAR_OUTPUTS] = self._write_parquet_artifact(
                tmp_dir, "bar_outputs.parquet", bar_outputs
            )
        if plots is not None:
            plot_records = self._plot_records(plots)
            if plot_records:
                plots_df = pd.DataFrame(plot_records)
                plots_tmp = tmp_dir / "plot_outputs.parquet"
                pq.write_table(pa.Table.from_pandas(plots_df), str(plots_tmp), compression="zstd")
                artifact_paths[ARTIFACT_TYPE_PLOT_OUTPUTS] = plots_tmp
                plots_df.to_csv(str(tmp_dir / "plot_outputs.csv"), index=False)

        report = self._report_payload(
            run_id,
            strategy_id,
            result,
            metrics,
            run_dir,
            has_plot_outputs=ARTIFACT_TYPE_PLOT_OUTPUTS in artifact_paths,
            now=now,
        )
        report_tmp = tmp_dir / "report.json"
        report_tmp.write_text(json.dumps(report, indent=2, default=str))
        artifact_paths[ARTIFACT_TYPE_REPORT_JSON] = report_tmp

        md_tmp = tmp_dir / "report.md"
        md_tmp.write_text(self._report_markdown(run_id, strategy_id, result, metrics))
        artifact_paths[ARTIFACT_TYPE_REPORT_MD] = md_tmp
        return artifact_paths

    @staticmethod
    def _result_json_payload(metrics: BacktestMetricsSummary) -> dict[str, Any]:
        return {
            "net_profit": metrics.net_profit,
            "net_profit_pct": metrics.net_profit_pct,
            "profit_factor": metrics.profit_factor,
            "max_drawdown": metrics.max_drawdown,
            "sharpe": metrics.sharpe,
            "sortino": metrics.sortino,
            "win_rate": metrics.win_rate,
            "total_trades": metrics.trades_total,
        }

    @staticmethod
    def _report_payload(
        run_id: str,
        strategy_id: str,
        result: Any,
        metrics: BacktestMetricsSummary,
        run_dir: Path,
        *,
        has_plot_outputs: bool,
        now: int,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "symbol": getattr(result, "symbol", ""),
            "timeframe": getattr(result, "timeframe", ""),
            "started_at": now,
            "status": "done",
            "metrics": {
                "initial_capital": metrics.initial_capital,
                "final_equity": metrics.final_equity,
                "net_profit": metrics.net_profit,
                "net_profit_pct": metrics.net_profit_pct,
                "profit_factor": metrics.profit_factor,
                "max_drawdown": metrics.max_drawdown,
                "sharpe": metrics.sharpe,
                "sortino": metrics.sortino,
                "win_rate": metrics.win_rate,
                "total_trades": metrics.trades_total,
            },
            "plot_outputs_path": str(run_dir / "plot_outputs.parquet") if has_plot_outputs else None,
        }

    @staticmethod
    def _report_markdown(
        run_id: str,
        strategy_id: str,
        result: Any,
        metrics: BacktestMetricsSummary,
    ) -> str:
        md_lines = [
            f"# Backtest Report: {run_id}",
            "",
            f"- **Strategy**: {strategy_id}",
            f"- **Symbol**: {getattr(result, 'symbol', 'N/A')} {getattr(result, 'timeframe', 'N/A')}",
            f"- **Status**: done",
            "",
            "## Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Initial Capital | {metrics.initial_capital} |",
            f"| Final Equity | {metrics.final_equity} |",
            f"| Net Profit | {metrics.net_profit} |",
            f"| Net Profit % | {metrics.net_profit_pct} |",
            f"| Profit Factor | {metrics.profit_factor} |",
            f"| Max Drawdown | {metrics.max_drawdown} |",
            f"| Sharpe | {metrics.sharpe} |",
            f"| Win Rate | {metrics.win_rate} |",
            f"| Total Trades | {metrics.trades_total} |",
            "",
        ]
        return "\n".join(md_lines)

    def _row_to_run(self, row: tuple) -> BacktestRun:
        cursor = self._storage.execute("SELECT * FROM backtest_runs LIMIT 0")
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))

        metrics = BacktestMetricsSummary(
            initial_capital=data.get("initial_capital"),
            final_equity=data.get("final_equity"),
            net_profit=data.get("net_profit"),
            net_profit_pct=data.get("net_profit_pct"),
            gross_profit=data.get("gross_profit"),
            gross_loss=data.get("gross_loss"),
            profit_factor=data.get("profit_factor"),
            max_drawdown=data.get("max_drawdown"),
            max_drawdown_pct=data.get("max_drawdown_pct"),
            sharpe=data.get("sharpe"),
            sortino=data.get("sortino"),
            calmar=data.get("calmar"),
            win_rate=data.get("win_rate"),
            trades_total=data.get("trades_total", 0),
            winning_trades=data.get("winning_trades", 0),
            losing_trades=data.get("losing_trades", 0),
            avg_trade=data.get("avg_trade"),
            avg_win=data.get("avg_win"),
            avg_loss=data.get("avg_loss"),
            largest_win=data.get("largest_win"),
            largest_loss=data.get("largest_loss"),
            avg_bars_in_trade=data.get("avg_bars_in_trade"),
            commission_total=data.get("commission_total"),
            expectancy=data.get("expectancy"),
        )

        return BacktestRun(
            run_id=data["run_id"],
            strategy_id=data["strategy_id"],
            pine_id=data["pine_id"],
            artifact_id=data["artifact_id"],
            params_hash=data["params_hash"],
            exchange=data["exchange"],
            market_type=data["market_type"],
            symbol=data["symbol"],
            price_type=data["price_type"],
            timeframe=data["timeframe"],
            from_time=data.get("from_time"),
            to_time=data.get("to_time"),
            warmup_bars=data.get("warmup_bars", 0),
            status=data["status"],
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            metrics=metrics,
            result_json=data.get("result_json"),
            report_path=data.get("report_path"),
            equity_curve_path=data.get("equity_curve_path"),
            bar_outputs_path=data.get("bar_outputs_path"),
            plot_outputs_path=data.get("plot_outputs_path"),
            error_message=data.get("error_message"),
            traceback_id=data.get("traceback_id"),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
        )

    def _row_to_trade(self, row: tuple) -> BacktestTrade:
        cursor = self._storage.execute("SELECT * FROM backtest_trades LIMIT 0")
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        return BacktestTrade(
            trade_id=data["trade_id"],
            run_id=data["run_id"],
            strategy_id=data["strategy_id"],
            direction=data["direction"],
            entry_time=data["entry_time"],
            entry_price=data["entry_price"],
            qty=data["qty"],
            entry_id=data.get("entry_id"),
            exit_id=data.get("exit_id"),
            exit_time=data.get("exit_time"),
            exit_price=data.get("exit_price"),
            gross_pnl=data.get("gross_pnl"),
            net_pnl=data.get("net_pnl"),
            net_pnl_pct=data.get("net_pnl_pct"),
            fee=data.get("fee"),
            slippage=data.get("slippage"),
            bars_held=data.get("bars_held"),
            exit_reason=data.get("exit_reason"),
            created_at=data.get("created_at", 0),
        )

    def _row_to_artifact(self, row: tuple) -> BacktestArtifact:
        cursor = self._storage.execute("SELECT * FROM backtest_artifacts LIMIT 0")
        cols = [d[0] for d in cursor.description]
        data = dict(zip(cols, row))
        return BacktestArtifact(
            artifact_row_id=data["artifact_row_id"],
            run_id=data["run_id"],
            strategy_id=data["strategy_id"],
            artifact_type=data["artifact_type"],
            path=data["path"],
            format=data["format"],
            row_count=data.get("row_count"),
            min_time=data.get("min_time"),
            max_time=data.get("max_time"),
            schema_hash=data.get("schema_hash"),
            created_at=data.get("created_at", 0),
        )

    def close(self) -> None:
        self._storage.close()
