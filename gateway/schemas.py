"""Pydantic schemas for gateway API request/response models.

These are the public contract — API consumers depend on these shapes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────


class StrategyStatus(str, Enum):
    PENDING = "pending"
    PAUSED = "paused"
    RUNNING = "running"
    DISABLED = "disabled"
    ERROR = "error"


class StrategyMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"
    OBSERVE = "observe"
    BACKTEST = "backtest"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    PENDING = "pending"
    NEW = "new"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ── Pine Sources ───────────────────────────────────────────────────────────────


class PineSourceCreate(BaseModel):
    """Create a new Pine source file."""

    name: str = Field(..., min_length=1, max_length=256)
    source_text: str = Field(..., min_length=1)
    source_type: str = Field(default="strategy", pattern="^(strategy|indicator|library|unknown)$")


class PineSourceUpdate(BaseModel):
    """Update an existing Pine source."""

    name: str | None = None
    source_text: str | None = None
    source_type: str | None = None


class PineSourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    version: str
    active_artifact_id: str | None = None
    created_at: int
    updated_at: int


class PineSourceDetailResponse(PineSourceResponse):
    source_text: str


# ── Strategies ─────────────────────────────────────────────────────────────────


class StrategyCreate(BaseModel):
    """Create a new strategy instance from a compiled Pine source."""

    name: str = Field(..., min_length=1, max_length=256)
    pine_id: str = Field(..., description="Pine source id (from /pine-sources)")
    artifact_id: str = Field(..., description="Compiled artifact id")
    symbol: str = Field(..., min_length=1, max_length=32)
    timeframe: str = Field(..., min_length=1, max_length=16)
    exchange: str = Field(default="binance")
    market_type: str = Field(default="spot")
    params_json: str = Field(default="{}")
    mode: StrategyMode = StrategyMode.PAPER


class StrategyUpdate(BaseModel):
    """Partial update for a strategy."""

    name: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    exchange: str | None = None
    market_type: str | None = None
    params_json: str | None = None
    mode: StrategyMode | None = None
    enabled: bool | None = None


class StrategyResponse(BaseModel):
    strategy_id: str
    name: str
    pine_id: str
    artifact_id: str
    symbol: str
    timeframe: str
    exchange: str
    market_type: str
    params_json: str
    params_hash: str
    mode: str
    enabled: bool
    status: str
    created_at: int
    updated_at: int
    health: dict[str, Any] | None = None


class StrategyAction(BaseModel):
    """Start/stop/pause/error-clear actions."""

    action: str = Field(..., pattern="^(start|stop|pause|enable|clear_error)$")


# ── Backtest ───────────────────────────────────────────────────────────────────


class BacktestRunRequest(BaseModel):
    """Request to run a backtest."""

    strategy_id: str
    from_time: str = Field(..., description="ISO date or ms timestamp")
    to_time: str = Field(..., description="ISO date or ms timestamp")
    params_override: dict[str, Any] | None = None
    warmup_bars: int = 0
    capture_plots: bool = False


class BacktestRunResponse(BaseModel):
    run_id: str
    strategy_id: str
    status: str
    started_at: int | None = None
    finished_at: int | None = None


class BacktestRunDetail(BacktestRunResponse):
    symbol: str
    timeframe: str
    from_time: int
    to_time: int
    bars_processed: int | None = None
    metrics: dict[str, Any] | None = None
    strategy_name: str | None = None
    version: int | None = None


class BacktestTradeResponse(BaseModel):
    trade_id: str
    run_id: str
    entry_time: int
    exit_time: int | None = None
    direction: str
    entry_price: float
    exit_price: float | None = None
    qty: float
    net_profit: float | None = None
    bars_held: int | None = None
    exit_reason: str | None = None


class BacktestProgress(BaseModel):
    """Progress update for a running backtest."""

    run_id: str
    status: str
    bars_processed: int
    total_bars: int
    pct: float
    message: str = ""


class BacktestEstimateResponse(BaseModel):
    """Estimated market data range and workload for a backtest."""

    strategy_id: str
    symbol: str
    timeframe: str
    requested_from: int
    requested_to: int
    effective_from: int
    effective_to: int
    earliest_available: int | None = None
    adjusted: bool = False
    estimated_bars: int = 0
    estimated_pages: int = 0


# ── Live / Paper ──────────────────────────────────────────────────────────────


class PaperStartRequest(BaseModel):
    strategy_id: str


class LiveStartRequest(BaseModel):
    strategy_id: str


class TradingStatusResponse(BaseModel):
    strategy_id: str
    mode: str
    status: str
    last_bar_time: int | None = None
    position_qty: float | None = None
    position_side: str | None = None


# ── Data ──────────────────────────────────────────────────────────────────────


class DataBackfillRequest(BaseModel):
    symbol: str
    timeframe: str
    from_time: str
    to_time: str
    exchange: str = "binance"
    market_type: str = "spot"


class DataCoverageResponse(BaseModel):
    symbol: str
    timeframe: str
    earliest_ms: int | None = None
    latest_ms: int | None = None
    bar_count: int
    gaps: list[dict[str, Any]] = Field(default_factory=list)


class CacheStatusResponse(BaseModel):
    cache_dir: str
    total_size_bytes: int
    instruments: list[str]
    timeframes: list[str]


# ── Events ────────────────────────────────────────────────────────────────────


class EventResponse(BaseModel):
    event_id: str
    event_type: str
    timestamp_ms: int
    payload: dict[str, Any]


# ── Accounts ──────────────────────────────────────────────────────────────────


class AccountResponse(BaseModel):
    account_id: str
    name: str
    exchange: str
    market_type: str
    mode: str
    live_enabled: bool
    created_at: int


# ── Dashboard ─────────────────────────────────────────────────────────────────


class StrategySummary(BaseModel):
    strategy_id: str
    name: str
    symbol: str
    timeframe: str
    mode: str
    status: str
    enabled: bool
    last_job_status: str | None = None
    health: dict[str, Any] | None = None


class JobSummary(BaseModel):
    pending: int
    running: int
    done: int
    failed: int
    recent: list[dict[str, Any]] = Field(default_factory=list)


class DashboardResponse(BaseModel):
    strategies: list[StrategySummary]
    jobs: JobSummary
    kill_switch: bool
    uptime_seconds: float
    last_event_time: int | None = None
    last_bar_update: int | None = None


# ── Risk ──────────────────────────────────────────────────────────────────────


class KillSwitchRequest(BaseModel):
    enabled: bool


class RiskStatusResponse(BaseModel):
    kill_switch: bool
    rules: list[str]


# ── Progress tracking ─────────────────────────────────────────────────────────


class ProgressUpdate(BaseModel):
    """Generic progress update sent over WebSocket."""

    operation_id: str
    operation_type: str  # backtest, compile, optimizer, backfill
    status: str  # queued, running, completed, failed
    pct: float = 0.0
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


# ── Optimizer ──────────────────────────────────────────────────────────────────


class OptimizerDryRunRequest(BaseModel):
    strategy_id: str
    trials: int = Field(ge=1, le=10000)


class OptimizerDryRunResponse(BaseModel):
    strategy_id: str
    trials_requested: int
    status: str
    reason: str | None = None


# ── Replay ────────────────────────────────────────────────────────────────────


class ReplayRequest(BaseModel):
    """Replay a strategy over a date range (re-run backtest on historical data)."""
    from_date: str | None = None  # ISO date or timestamp
    to_date: str | None = None


class ReplayResponse(BaseModel):
    run_id: str
    strategy_id: str
    status: str
    bars_processed: int
    message: str = ""


# ── Compare TV ────────────────────────────────────────────────────────────────


class CompareTvRequest(BaseModel):
    """Compare OpenPine plots against TradingView chart export."""
    openpine_plots_path: str  # path to OpenPine plots CSV
    tv_chart_path: str  # path to TradingView chart CSV
    abs_tol: float = 1e-6
    rel_tol: float = 1e-9
    include_base_columns: bool = False


class CompareTvResponse(BaseModel):
    strategy_id: str
    status: str  # match, mismatch, error
    classification: str = ""
    mismatch_cells: int = 0
    total_cells: int = 0
    max_abs_delta: float = 0.0
    worst_column: str = ""
    report_path: str | None = None
