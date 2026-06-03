"""Execute fan-out strategy bar jobs through the OpenPine runtime boundary."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Protocol

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.data.orchestrator import DataOrchestrator
from openpine.data.provider_adapter import create_local_runtime_data_provider_adapter
from openpine.exchange_metadata import default_qty_rounding_mode, default_qty_step
from openpine.jobs import Job, JobScheduler, JobStatus, JobType
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance
from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig, load_strategy_class_from_artifact
from openpine.state.store import SnapshotMetadata, StateStore
from openpine.storage.strategy_ledger import (
    LedgerSource,
    PositionSide,
    StrategyLedger,
    StrategyPosition,
    StrategyTrade,
    TradeStatus,
)


class StrategyJobStatus(StrEnum):
    """Terminal status returned by the strategy job executor."""

    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StrategyJobExecutionResult:
    """Result of processing a single strategy bar job."""

    job_id: str
    strategy_id: str
    status: StrategyJobStatus
    bar_time: int | None = None
    snapshot_id: str | None = None
    trades_recorded: int = 0
    skipped_reason: str | None = None
    error: str | None = None


class RuntimeAdapter(Protocol):
    def run(
        self,
        strategy_class: type,
        bars: list[Bar],
        config: BacktestRunConfig,
        params: dict | None = None,
        execution_backend: Any | None = None,
        progress_callback: Any | None = None,
        runtime_data_provider: Any | None = None,
        resume_state: Any | None = None,
        effective_pre_bars: int | None = None,
    ) -> Any: ...


StrategyClassLoader = Callable[[StrategyInstance], type]


class StrategyJobExecutor:
    """Run queued paper/live strategy-bar jobs idempotently.

    Input jobs are produced by :class:`StrategyBarFanout`: the source 1m bar has
    already been persisted and any target timeframe bar has already been
    aggregated and stored. This worker only loads that target bar, resumes the
    strategy runtime, saves the next snapshot, and mirrors accounting facts into
    the strategy ledger.
    """

    def __init__(
        self,
        *,
        registry: SQLiteStrategyRegistry,
        orchestrator: DataOrchestrator,
        scheduler: JobScheduler,
        state_store: StateStore,
        ledger: StrategyLedger | None = None,
        runtime_adapter: RuntimeAdapter | None = None,
        strategy_loader: StrategyClassLoader | None = None,
        runtime_data_provider: Any | None = None,
    ) -> None:
        self.registry = registry
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.state_store = state_store
        self.ledger = ledger
        self.runtime_adapter = runtime_adapter or BacktestEngineAdapter()
        self.strategy_loader = strategy_loader or _load_strategy_class
        self.runtime_data_provider = runtime_data_provider

    def process(self, job: Job) -> StrategyJobExecutionResult:
        """Process one queued strategy bar job and update scheduler status."""

        try:
            self._validate_job(job)
            payload = _job_payload(job)
            strategy = self.registry.get_strategy(payload["strategy_id"])
            bar = self._load_target_bar(strategy, payload)
            state_key = _state_key(strategy, bar)

            latest_meta = self.state_store.latest_snapshot_metadata(
                strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=state_key["instrument_key"],
                timeframe=state_key["timeframe"],
            )
            if latest_meta is not None and latest_meta.bar_time >= bar.time:
                result = StrategyJobExecutionResult(
                    job_id=job.id,
                    strategy_id=strategy.strategy_id,
                    status=StrategyJobStatus.SKIPPED,
                    bar_time=bar.time,
                    snapshot_id=latest_meta.snapshot_id,
                    skipped_reason="already_processed",
                )
                self.scheduler.mark_done(job.id, _result_dict(result))
                return result

            self.scheduler.mark_running(job.id)
            resume_state = self.state_store.load_runtime_snapshot(
                strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=state_key["instrument_key"],
                timeframe=state_key["timeframe"],
                at_or_before_bar_time=bar.time - 1,
            )
            runtime_result = self._run_strategy(strategy, bar, resume_state)
            status = str(getattr(runtime_result, "status", "completed")).lower()
            if status not in {"ok", "completed"}:
                raise RuntimeError(f"strategy runtime failed with status={status}")

            resume_out = getattr(runtime_result, "resume_state", None)
            if resume_out is None:
                resume_out = getattr(getattr(runtime_result, "raw_result", None), "resume_state", None)
            snapshot = self.state_store.save_runtime_snapshot(
                strategy_id=strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=state_key["instrument_key"],
                timeframe=state_key["timeframe"],
                runtime_state=resume_out,
                bar_time=bar.time,
                reason=f"{job.job_type.value}",
                failed_bar=False,
            )
            trades_recorded = self._record_ledger(strategy, job, bar, runtime_result)
            result = StrategyJobExecutionResult(
                job_id=job.id,
                strategy_id=strategy.strategy_id,
                status=StrategyJobStatus.DONE,
                bar_time=bar.time,
                snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
                trades_recorded=trades_recorded,
            )
            self.scheduler.mark_done(job.id, _result_dict(result))
            return result
        except Exception as exc:
            error = str(exc)
            self.scheduler.mark_failed(job.id, error)
            return StrategyJobExecutionResult(
                job_id=job.id,
                strategy_id=job.strategy_id or "",
                status=StrategyJobStatus.FAILED,
                error=error,
            )

    def _validate_job(self, job: Job) -> None:
        if job.job_type not in {JobType.PAPER_BAR_PROCESS, JobType.LIVE_BAR_PROCESS}:
            raise ValueError(f"unsupported strategy job type: {job.job_type}")
        _job_payload(job)

    def _load_target_bar(self, strategy: StrategyInstance, payload: dict[str, Any]) -> Bar:
        instrument = _instrument_from_payload(strategy, payload)
        timeframe = parse_timeframe(str(payload["timeframe"]))
        bar_time = int(payload["bar_time"])
        bar_close_time = int(payload.get("bar_close_time") or bar_time + (timeframe.duration_ms or 0))
        query = BarQuery(
            instrument=instrument,
            timeframe=timeframe,
            start_ms=bar_time,
            end_ms=bar_close_time,
            source="storage",
            gap_policy="fail",
        )
        bars = self.orchestrator.get_bars(query)
        if len(bars) != 1:
            raise RuntimeError(
                f"expected one stored {timeframe.canonical} bar for "
                f"{instrument.exchange}/{instrument.market}/{instrument.symbol} at {bar_time}, got {len(bars)}"
            )
        return bars[0]

    def _run_strategy(self, strategy: StrategyInstance, bar: Bar, resume_state: Any | None) -> Any:
        strategy_class = self.strategy_loader(strategy)
        config = _build_bar_run_config(strategy, bar)
        params = _strategy_params(strategy)
        runtime_data_provider = self.runtime_data_provider
        if runtime_data_provider is None:
            runtime_data_provider = create_local_runtime_data_provider_adapter(
                exchange=strategy.exchange.lower(),
                market=strategy.market_type.lower(),
                prefetch_end_ms=bar.time_close,
            )
        setattr(strategy_class, "runtime_data_provider", runtime_data_provider)
        setattr(strategy_class, "runtime_intrabar_provider", runtime_data_provider)
        return self.runtime_adapter.run(
            strategy_class,
            [bar],
            config,
            params=params,
            execution_backend=None,
            runtime_data_provider=runtime_data_provider,
            resume_state=resume_state,
        )

    def _record_ledger(
        self,
        strategy: StrategyInstance,
        job: Job,
        bar: Bar,
        runtime_result: Any,
    ) -> int:
        if self.ledger is None:
            return 0
        source = LedgerSource.LIVE if job.job_type == JobType.LIVE_BAR_PROCESS else LedgerSource.PAPER
        raw_result = getattr(runtime_result, "raw_result", runtime_result)
        resume_state = getattr(runtime_result, "resume_state", None) or getattr(raw_result, "resume_state", None)
        self._record_position(strategy, source, bar, resume_state, raw_result)
        return self._record_closed_trades(strategy, source, bar, raw_result)

    def _record_position(
        self,
        strategy: StrategyInstance,
        source: LedgerSource,
        bar: Bar,
        resume_state: Any | None,
        raw_result: Any,
    ) -> None:
        position = _broker_position(resume_state) or _result_position(raw_result)
        if position is None:
            return
        signed_size = float(getattr(position, "size", 0.0) or 0.0)
        direction = str(getattr(position, "direction", "") or "").lower()
        side = PositionSide.FLAT
        if signed_size > 0 or direction == "long":
            side = PositionSide.LONG
        elif signed_size < 0 or direction == "short":
            side = PositionSide.SHORT
        self.ledger.upsert_position(
            StrategyPosition(
                strategy_id=strategy.strategy_id,
                exchange=strategy.exchange,
                market_type=strategy.market_type,
                symbol=strategy.symbol,
                price_type=strategy.price_type,
                timeframe=parse_timeframe(strategy.timeframe).canonical,
                source=source,
                side=side,
                qty=abs(signed_size),
                avg_price=_float_or_none(getattr(position, "avg_price", None)),
                realized_pnl=_float_or_none(getattr(position, "realized_profit", None)) or 0.0,
                unrealized_pnl=_float_or_none(getattr(position, "open_profit", None)),
                last_bar_time=bar.time,
            )
        )

    def _record_closed_trades(
        self,
        strategy: StrategyInstance,
        source: LedgerSource,
        bar: Bar,
        raw_result: Any,
    ) -> int:
        closed = list(getattr(raw_result, "closed_trades", None) or [])
        recorded = 0
        for trade in closed:
            exit_time = getattr(trade, "exit_time", None)
            if exit_time is None or int(exit_time) < bar.time or int(exit_time) > bar.time_close:
                continue
            self.ledger.record_trade(
                StrategyTrade(
                    trade_id=_ledger_trade_id(strategy.strategy_id, source, trade),
                    strategy_id=strategy.strategy_id,
                    exchange=strategy.exchange,
                    market_type=strategy.market_type,
                    symbol=strategy.symbol,
                    price_type=strategy.price_type,
                    timeframe=parse_timeframe(strategy.timeframe).canonical,
                    source=source,
                    status=TradeStatus.CLOSED,
                    direction=str(getattr(trade, "direction", "")),
                    entry_time=int(getattr(trade, "entry_time", 0) or 0),
                    exit_time=int(exit_time),
                    entry_price=float(getattr(trade, "entry_price", 0.0) or 0.0),
                    exit_price=_float_or_none(getattr(trade, "exit_price", None)),
                    qty=abs(float(getattr(trade, "qty", 0.0) or 0.0)),
                    entry_id=getattr(trade, "entry_id", None),
                    exit_id=getattr(trade, "exit_id", None),
                    gross_pnl=_float_or_none(getattr(trade, "profit", None)),
                    net_pnl=_float_or_none(getattr(trade, "profit", None)),
                    fee=(
                        (_float_or_none(getattr(trade, "commission_entry", None)) or 0.0)
                        + (_float_or_none(getattr(trade, "commission_exit", None)) or 0.0)
                    ),
                    bars_held=getattr(trade, "bars_held", None),
                    metadata={"job_id": getattr(trade, "id", None)},
                )
            )
            recorded += 1
        return recorded


def _load_strategy_class(strategy: StrategyInstance) -> type:
    return load_strategy_class_from_artifact(
        strategy.pine_id,
        strategy.artifact_id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
    )


def _job_payload(job: Job) -> dict[str, Any]:
    payload = dict(job.input or {})
    required = {"strategy_id", "instrument_key", "timeframe", "bar_time"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"strategy job is missing input fields: {missing}")
    return payload


def _instrument_from_payload(strategy: StrategyInstance, payload: dict[str, Any]) -> InstrumentKey:
    raw = str(payload.get("instrument_key") or "")
    parts = raw.split(":")
    if len(parts) >= 3:
        return InstrumentKey(exchange=parts[0], market=parts[1], symbol=parts[2])
    return InstrumentKey(
        exchange=strategy.exchange.lower(),
        market=strategy.market_type.lower(),
        symbol=strategy.symbol.upper(),
    )


def _state_key(strategy: StrategyInstance, bar: Bar) -> dict[str, dict[str, str]]:
    return {
        "instrument_key": {
            "exchange": strategy.exchange.lower(),
            "market": strategy.market_type.lower(),
            "symbol": strategy.symbol.upper(),
            "price_type": strategy.price_type.lower(),
        },
        "timeframe": {"canonical": bar.timeframe.canonical},
    }


def _strategy_params(strategy: StrategyInstance) -> dict[str, Any]:
    try:
        loaded = json.loads(strategy.params_json or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _build_bar_run_config(strategy: StrategyInstance, bar: Bar) -> BacktestRunConfig:
    decl_args = _artifact_declaration_args(strategy)
    commission_type = {
        "cash_per_order": "fixed_per_order",
        "cash_per_contract": "fixed_per_contract",
    }.get(str(decl_args.get("commission_type", "none")), decl_args.get("commission_type", "none"))
    kwargs = {
        "symbol": strategy.symbol,
        "timeframe": bar.timeframe.canonical,
        "exchange": strategy.exchange.lower(),
        "market_type": strategy.market_type.lower(),
        "start_time": bar.time,
        "end_time": bar.time_close,
        "initial_capital": decl_args.get("initial_capital", 10_000.0),
        "default_qty_type": decl_args.get("default_qty_type", "fixed"),
        "default_qty_value": decl_args.get("default_qty_value", 1.0),
        "commission_type": commission_type,
        "commission_value": decl_args.get("commission_value", 0.0),
        "slippage": decl_args.get("slippage", 0.0),
        "slippage_type": decl_args.get("slippage_type", "tick"),
        "exit_matching": str(decl_args.get("close_entries_rule", "fifo")).upper(),
        "pyramiding": decl_args.get("pyramiding", 0),
        "margin_long": decl_args.get("margin_long", 100.0),
        "margin_short": decl_args.get("margin_short", 100.0),
        "process_orders_on_close": bool(decl_args.get("process_orders_on_close", False)),
        "calc_on_order_fills": bool(decl_args.get("calc_on_order_fills", False)),
        "calc_on_every_tick": bool(decl_args.get("calc_on_every_tick", False)),
        "use_bar_magnifier": bool(decl_args.get("use_bar_magnifier", False)),
        "qty_step": default_qty_step(strategy.exchange, strategy.market_type, strategy.symbol),
        "qty_rounding_mode": default_qty_rounding_mode(strategy.exchange, strategy.market_type, strategy.symbol),
        "export_resume_state": True,
        "content_hash_enabled": False,
        "collect_events": False,
        "collect_order_lifecycle": False,
    }
    supported = set(inspect.signature(BacktestRunConfig).parameters)
    return BacktestRunConfig(**{key: value for key, value in kwargs.items() if key in supported})


def _artifact_declaration_args(strategy: StrategyInstance) -> dict[str, Any]:
    if not strategy.pine_id or not strategy.artifact_id:
        return {}
    try:
        from openpine.artifacts import ArtifactStore

        artifact = ArtifactStore().get_artifact(strategy.artifact_id, strategy.pine_id)
    except Exception:
        return {}
    declaration = artifact.get("compile_meta", {}).get("translation_metadata", {}).get("declaration", {})
    args = declaration.get("arguments", {})
    return args if isinstance(args, dict) else {}


def _broker_position(resume_state: Any | None) -> Any | None:
    broker_state = getattr(resume_state, "broker_state", None)
    if broker_state is None and isinstance(resume_state, dict):
        broker_state = resume_state.get("broker_state")
    if broker_state is None:
        return None
    if isinstance(broker_state, dict):
        return broker_state.get("position")
    return getattr(broker_state, "position", None)


def _result_position(raw_result: Any) -> Any | None:
    open_trades = list(getattr(raw_result, "open_trades", None) or [])
    if not open_trades:
        return None
    qty = sum(float(getattr(trade, "qty", 0.0) or 0.0) for trade in open_trades)
    direction = str(getattr(open_trades[0], "direction", "flat") or "flat")
    avg_price = sum(
        float(getattr(trade, "entry_price", 0.0) or 0.0) * float(getattr(trade, "qty", 0.0) or 0.0)
        for trade in open_trades
    ) / qty if qty else None

    class _Position:
        pass

    position = _Position()
    position.size = qty if direction == "long" else -qty if direction == "short" else 0.0
    position.direction = direction
    position.avg_price = avg_price
    position.realized_profit = getattr(raw_result, "net_profit", 0.0)
    position.open_profit = None
    return position


def _ledger_trade_id(strategy_id: str, source: LedgerSource, trade: Any) -> str:
    payload = {
        "strategy_id": strategy_id,
        "source": source.value,
        "id": getattr(trade, "id", None),
        "entry_id": getattr(trade, "entry_id", None),
        "exit_id": getattr(trade, "exit_id", None),
        "entry_time": getattr(trade, "entry_time", None),
        "exit_time": getattr(trade, "exit_time", None),
        "qty": getattr(trade, "qty", None),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"strade_{hashlib.sha256(raw).hexdigest()[:24]}"


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_dict(result: StrategyJobExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "strategy_id": result.strategy_id,
        "bar_time": result.bar_time,
        "snapshot_id": result.snapshot_id,
        "trades_recorded": result.trades_recorded,
        "skipped_reason": result.skipped_reason,
        "error": result.error,
    }


__all__ = [
    "StrategyJobExecutionResult",
    "StrategyJobExecutor",
    "StrategyJobStatus",
]
