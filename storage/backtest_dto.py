"""DTOs for backtest persistence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestRunRequest:
    """Request to start a backtest run."""

    strategy_id: str
    pine_id: str
    artifact_id: str
    params_hash: str
    symbol: str
    timeframe: str
    exchange: str = "binance"
    market_type: str = "usdm"
    price_type: str = "trade"
    from_time: int | None = None
    to_time: int | None = None
    warmup_bars: int = 0


@dataclass
class BacktestMetricsSummary:
    """Summary metrics from a backtest run."""

    initial_capital: float | None = None
    final_equity: float | None = None
    net_profit: float | None = None
    net_profit_pct: float | None = None
    gross_profit: float | None = None
    gross_loss: float | None = None
    profit_factor: float | None = None
    max_drawdown: float | None = None
    max_drawdown_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    win_rate: float | None = None
    trades_total: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_trade: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    largest_win: float | None = None
    largest_loss: float | None = None
    avg_bars_in_trade: float | None = None
    commission_total: float | None = None
    expectancy: float | None = None


@dataclass
class BacktestRun:
    """A persisted backtest run record."""

    run_id: str
    strategy_id: str
    pine_id: str
    artifact_id: str
    params_hash: str
    exchange: str
    market_type: str
    symbol: str
    price_type: str
    timeframe: str
    from_time: int | None
    to_time: int | None
    warmup_bars: int
    status: str
    started_at: int
    finished_at: int | None
    metrics: BacktestMetricsSummary = field(default_factory=BacktestMetricsSummary)
    result_json: str | None = None
    report_path: str | None = None
    equity_curve_path: str | None = None
    bar_outputs_path: str | None = None
    plot_outputs_path: str | None = None
    error_message: str | None = None
    traceback_id: str | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class BacktestTrade:
    """A trade from a backtest run."""

    trade_id: str
    run_id: str
    strategy_id: str
    direction: str
    entry_time: int
    entry_price: float
    qty: float
    entry_id: str | None = None
    exit_id: str | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    gross_pnl: float | None = None
    net_pnl: float | None = None
    net_pnl_pct: float | None = None
    fee: float | None = None
    slippage: float | None = None
    bars_held: int | None = None
    exit_reason: str | None = None
    created_at: int = 0


@dataclass
class BacktestArtifact:
    """An artifact (Parquet/JSON file) from a backtest run."""

    artifact_row_id: str
    run_id: str
    strategy_id: str
    artifact_type: str
    path: str
    format: str
    row_count: int | None = None
    min_time: int | None = None
    max_time: int | None = None
    schema_hash: str | None = None
    created_at: int = 0


ARTIFACT_TYPE_PLOT_OUTPUTS = "plot_outputs"
ARTIFACT_TYPE_REPORT_MD = "report_md"

# Artifact type constants
ARTIFACT_TYPE_EQUITY_CURVE = "equity_curve"
ARTIFACT_TYPE_BAR_OUTPUTS = "bar_outputs"
ARTIFACT_TYPE_TRADES = "trades"
ARTIFACT_TYPE_REPORT_JSON = "report_json"
ARTIFACT_TYPE_REPORT_MD = "report_md"
