"""Pydantic schemas for gateway API request/response models.

These are the public contract — API consumers depend on these shapes.
"""

from __future__ import annotations

import json
import math
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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


def _non_blank(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-blank string")
    return value


NonBlankStr = Annotated[str, BeforeValidator(_non_blank)]


def _object_json(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("must encode a JSON object")
    return value


# ── Pine Sources ───────────────────────────────────────────────────────────────


class PineSourceCreate(BaseModel):
    """Create a new Pine source file."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)
    source_text: str = Field(..., min_length=1)
    source_type: str = Field(
        default="strategy", pattern="^(strategy|indicator|library|unknown)$"
    )

    _validate_non_blank = field_validator("name", "source_text")(_non_blank)


class PineSourceUpdate(BaseModel):
    """Update an existing Pine source."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    source_text: str | None = None
    source_type: str | None = Field(
        default=None, pattern="^(strategy|indicator|library|unknown)$"
    )
    archived: bool | None = None

    _validate_non_blank = field_validator("name", "source_text")(_non_blank)

    @model_validator(mode="after")
    def validate_update(self) -> PineSourceUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one update field is required")
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("update fields must not be null")
        return self


class PineSourceResponse(BaseModel):
    id: str
    name: str
    source_type: str
    version: str
    active_artifact_id: str | None = None
    archived: bool = False
    created_at: int
    updated_at: int


class PineSourceDetailResponse(PineSourceResponse):
    source_text: str


# ── Strategies ─────────────────────────────────────────────────────────────────


class StrategyCreate(BaseModel):
    """Create a new strategy instance from a compiled Pine source."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256)
    pine_id: str = Field(
        ..., min_length=1, description="Pine source id (from /pine-sources)"
    )
    artifact_id: str = Field(..., min_length=1, description="Compiled artifact id")
    symbol: str = Field(..., min_length=1, max_length=32)
    timeframe: str = Field(..., min_length=1, max_length=16)
    exchange: str = Field(default="binance", min_length=1, max_length=64)
    market_type: str = Field(default="spot", min_length=1, max_length=32)
    params_json: str = Field(default="{}")
    mode: StrategyMode = StrategyMode.PAPER
    semantic_profile: str = Field(..., min_length=1)

    _validate_non_blank = field_validator(
        "name", "pine_id", "artifact_id", "symbol", "timeframe", "exchange", "market_type"
    )(_non_blank)
    _validate_params_json = field_validator("params_json")(_object_json)


class StrategyUpdate(BaseModel):
    """Partial update for a strategy."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    symbol: str | None = Field(default=None, max_length=32)
    timeframe: str | None = Field(default=None, max_length=16)
    exchange: str | None = Field(default=None, max_length=64)
    market_type: str | None = Field(default=None, max_length=32)
    params_json: str | None = None
    mode: StrategyMode | None = None
    enabled: bool | None = None
    archived: bool | None = None
    semantic_profile: str | None = None

    _validate_non_blank = field_validator(
        "name", "symbol", "timeframe", "exchange", "market_type"
    )(_non_blank)
    _validate_params_json = field_validator("params_json")(_object_json)

    @model_validator(mode="after")
    def validate_update(self) -> StrategyUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one update field is required")
        if any(getattr(self, field_name) is None for field_name in self.model_fields_set):
            raise ValueError("update fields must not be null")
        return self


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
    archived: bool = False
    status: str
    created_at: int
    updated_at: int
    health: dict[str, Any] | None = None
    semantic_profile: str | None = None


class StrategyAction(BaseModel):
    """Start/stop/pause/error-clear actions."""

    action: str = Field(..., pattern="^(start|stop|pause|enable|clear_error)$")


# ── Backtest ───────────────────────────────────────────────────────────────────


class BacktestRunRequest(BaseModel):
    """Request to run a backtest."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(..., min_length=1)
    from_time: str = Field(..., min_length=1, description="ISO date or ms timestamp")
    to_time: str = Field(..., min_length=1, description="ISO date or ms timestamp")
    params_override: dict[str, Any] | None = None
    warmup_bars: int = Field(default=0, ge=0)
    capture_plots: bool = False
    initial_capital: float | None = Field(
        None,
        gt=0,
        allow_inf_nan=False,
        description="Override starting capital (defaults to strategy declaration)",
    )
    semantic_profile: str | None = None
    allow_legacy: bool = False
    htf_timeframe: str | None = Field(default=None, max_length=16)


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
    entry_id: str | None = None
    exit_id: str | None = None
    entry_time: int
    exit_time: int | None = None
    direction: str
    entry_price: float
    exit_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    qty: float
    net_profit: float | None = None
    gross_profit: float | None = None
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
    exchange: str = "binance"
    market_type: str = "spot"
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
    model_config = ConfigDict(extra="forbid")
    strategy_id: NonBlankStr = Field(max_length=128)
    semantic_profile: str | None = None
    allow_legacy: bool = False


class LiveStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: NonBlankStr = Field(max_length=128)
    preview_hash: str = ""
    confirmation: str = ""
    idempotency_key: str = ""
    expires_at_utc_ms: int | None = None
    semantic_profile: str | None = None
    allow_legacy: bool = False
    htf_timeframe: str | None = Field(default=None, max_length=16)


class TradingStatusResponse(BaseModel):
    strategy_id: str
    mode: str
    status: str
    last_bar_time: int | None = None
    position_qty: float | None = None
    position_side: str | None = None


# ── Data ──────────────────────────────────────────────────────────────────────


class DataBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: NonBlankStr = Field(max_length=32)
    timeframe: NonBlankStr = Field(max_length=16)
    from_time: NonBlankStr = Field(max_length=64)
    to_time: NonBlankStr = Field(max_length=64)
    exchange: NonBlankStr = Field(default="binance", max_length=64)
    market_type: NonBlankStr = Field(default="spot", max_length=32)


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
    runtime_health: dict[str, Any] = Field(default_factory=dict)


# ── Risk ──────────────────────────────────────────────────────────────────────


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    model_config = ConfigDict(extra="forbid")
    strategy_id: NonBlankStr = Field(max_length=128)
    trials: int = Field(ge=1, le=10000)
    semantic_profile: str | None = None
    allow_legacy: bool = False


class OptimizerDryRunResponse(BaseModel):
    strategy_id: str
    trials_requested: int
    status: str
    reason: str | None = None


class OptimizerParameterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: Literal["int", "float", "bool", "string", "enum"]
    default: Any
    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    options: list[Any] | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_parameter(self) -> OptimizerParameterSpec:
        if self.type in {"int", "float"}:
            values = (self.default, self.min, self.max, self.step)
            if any(value is None or isinstance(value, bool) for value in values):
                raise ValueError("numeric parameters require numeric default/min/max/step")
            if self.type == "int" and not all(isinstance(value, int) for value in values):
                raise ValueError("int parameter values must be integers")
            minimum = self.min
            maximum = self.max
            step = self.step
            assert minimum is not None and maximum is not None and step is not None
            numeric_values = (self.default, minimum, maximum, step)
            if not all(math.isfinite(float(value)) for value in numeric_values):
                raise ValueError("numeric parameter values must be finite")
            if float(step) <= 0:
                raise ValueError("parameter step must be positive")
            if float(maximum) < float(minimum):
                raise ValueError("parameter max must be >= min")
        elif self.type == "bool":
            if not isinstance(self.default, bool):
                raise ValueError("bool parameter default must be boolean")
        else:
            if not self.options or self.default not in self.options:
                raise ValueError("string/enum default must be present in options")
        return self


class OptimizerSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: NonBlankStr = Field(max_length=128)
    from_time: NonBlankStr = Field(max_length=64)
    to_time: NonBlankStr = Field(max_length=64)
    trials: int = Field(ge=1, le=100)
    objective: Literal[
        "net_profit", "profit_factor", "sharpe_ratio", "max_drawdown_percent"
    ] = "net_profit"
    parameters: list[OptimizerParameterSpec] = Field(min_length=1, max_length=64)
    semantic_profile: str | None = None
    allow_legacy: bool = False
    htf_timeframe: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def validate_parameter_names(self) -> OptimizerSearchRequest:
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("duplicate optimizer parameter names")
        return self


class OptimizerChampion(BaseModel):
    params: dict[str, Any]
    metrics: dict[str, float]


class OptimizerTrialSummary(BaseModel):
    id: int | None = None
    status: str | None = None
    objective_value: float | None = None
    params_hash: str | None = None
    result_content_hash: str | None = None


class OptimizerSearchResponse(BaseModel):
    optimization_id: str
    strategy_id: str
    objective: str
    status: str
    trials_requested: int
    trials_completed: int
    champion: OptimizerChampion | None = None
    trial_status_counts: dict[str, int] = Field(default_factory=dict)
    trials: list[OptimizerTrialSummary] = Field(default_factory=list)
    uses_backtest_engine_path: bool


# ── Replay ────────────────────────────────────────────────────────────────────


class ReplayRequest(BaseModel):
    """Replay a strategy over a date range (re-run backtest on historical data)."""

    model_config = ConfigDict(extra="forbid")
    from_date: NonBlankStr | None = Field(default=None, max_length=64)
    to_date: NonBlankStr | None = Field(default=None, max_length=64)
    htf_timeframe: str | None = Field(default=None, max_length=16)


class ReplayResponse(BaseModel):
    run_id: str
    strategy_id: str
    status: str
    bars_processed: int
    message: str = ""


# ── Compare TV ────────────────────────────────────────────────────────────────


class CompareTvRequest(BaseModel):
    """Compare OpenPine plots against TradingView chart export."""

    model_config = ConfigDict(extra="forbid")
    openpine_plots_path: NonBlankStr = Field(max_length=4096)
    tv_chart_path: NonBlankStr = Field(max_length=4096)
    abs_tol: float = Field(default=1e-6, ge=0, allow_inf_nan=False)
    rel_tol: float = Field(default=1e-9, ge=0, allow_inf_nan=False)
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


# ── Achievements ─────────────────────────────────────────────────────────────


class AchievementItem(BaseModel):
    """One achievement row for the UI."""
    id: str
    tier: str
    icon: str
    title: str
    description: str
    metric: str
    target: float
    current: float
    reward: str
    hidden: bool
    unlocked: bool
    unlocked_at: int | None = None
    progress_pct: float = 0.0


class AchievementSummary(BaseModel):
    total: int
    unlocked: int
    by_tier: dict[str, dict[str, int]] = Field(default_factory=dict)


class AchievementsResponse(BaseModel):
    summary: AchievementSummary
    items: list[AchievementItem]
