"""Execute fan-out strategy bar jobs through the OpenPine runtime boundary."""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.data.orchestrator import DataOrchestrator

from openpine.admission import require_strategy_semantic_profile
from openpine.exchange_metadata import default_qty_rounding_mode, default_qty_step
from openpine.jobs import Job, JobScheduler, JobType
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance
from openpine.runtime.declaration_args import normalize_strategy_declaration_args
from openpine.runtime.engine import (
    BacktestEngineAdapter,
    BacktestRunConfig,
)
from openpine.runtime.isolated_run import capture_generated_source
from openpine.state.store import StateStore
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
    def run_isolated(
        self,
        source: bytes,
        bars: list[Bar],
        config: BacktestRunConfig,
        resume_state: Any | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any: ...


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
        artifact_store: Any | None = None,
        runtime_adapter: RuntimeAdapter | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
        htf_timeframe: str | None = None,
        execution_lease: Callable[[str], Any] | None = None,
        publication_lease: Callable[[str], Any] | None = None,
    ) -> None:
        self.registry = registry
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.state_store = state_store
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.runtime_adapter = runtime_adapter or BacktestEngineAdapter()
        self.htf_bars = htf_bars
        self.htf_timeframe = htf_timeframe
        self.execution_lease = execution_lease
        self.publication_lease = publication_lease
        self._job_htf_timeframe: str | None = None
        self._job_mtf_series: tuple[Any, ...] | None = None
        self._job_id: str | None = None
        self._job_type: JobType | None = None
        self._job_run_identity: dict[str, object] | None = None
        self._job_broker_account_ref: str | None = None
        self._job_paper_epoch_start: int | None = None
        self._process_lock = threading.RLock()
        self._stamped_sources: dict[tuple[str, str], bytes] = {}

    def _requested_htf_timeframe(self) -> str | None:
        if self._job_htf_timeframe is not None:
            return self._job_htf_timeframe
        return self.htf_timeframe

    def _confirmed_htf_bars(self, strategy: StrategyInstance, bars: list[Bar]):
        if not bars:
            raise RuntimeError("strategy execution requires chart bars")
        if self.htf_bars is not None:
            return self.htf_bars
        from openpine.runtime.isolated_run import _confirmed_htf_bars_for_timeframe
        from openpine.runtime.mtf import (
            admitted_mtf_requests,
            confirmed_mtf_bars_for_requests,
        )

        requested = self._requested_htf_timeframe()
        requests = self._job_mtf_series
        if requests is None:
            requests = admitted_mtf_requests(
                chart_symbol=strategy.symbol,
                htf_timeframe=requested,
            )
        if requests:
            return confirmed_mtf_bars_for_requests(
                chart_bars=bars,
                chart_symbol=strategy.symbol,
                chart_timeframe=strategy.timeframe,
                requests=requests,
                load_bars=lambda symbol, timeframe: self._load_mtf_provider_bars(
                    strategy, bars, symbol, timeframe
                ),
            )
        fetched = None
        if requested and str(requested) != str(strategy.timeframe):
            fetched = self._load_htf_provider_bars(strategy, bars, str(requested))
        return _confirmed_htf_bars_for_timeframe(
            chart_bars=bars,
            symbol=str(strategy.symbol).upper(),
            chart_timeframe=str(strategy.timeframe),
            requested_timeframe=requested,
            fetched_htf_bars=fetched,
        )

    def _load_htf_provider_bars(self, strategy: StrategyInstance, bars: list[Bar], timeframe: str):
        return self._load_mtf_provider_bars(strategy, bars, strategy.symbol, timeframe)

    def _load_mtf_provider_bars(
        self,
        strategy: StrategyInstance,
        bars: list[Bar],
        symbol: str,
        timeframe: str,
    ):
        if not bars:
            raise RuntimeError("MTF execution requires chart bars")
        first_bar = bars[0]
        last_bar = bars[-1]
        start_ms = int(first_bar.time)
        end_ms = getattr(last_bar, "time_close", None)
        tf = parse_timeframe(timeframe)
        if end_ms is None:
            end_ms = int(last_bar.time) + (tf.duration_ms or 60_000)
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=strategy.exchange.lower(),
                market=strategy.market_type.lower(),
                symbol=str(symbol).upper(),
            ),
            timeframe=tf,
            start_ms=start_ms,
            end_ms=int(end_ms),
            gap_policy="allow_with_metadata",
        )
        load_bars = getattr(self.orchestrator, "load_bars", None)
        if callable(load_bars):
            series = load_bars(query)
            return list(getattr(series, "bars", series))
        return list(self.orchestrator.get_bars(query))

    def process(self, job: Job) -> StrategyJobExecutionResult:
        """Process one queued strategy bar job and update scheduler status."""

        with self._process_lock:
            return self._process_serialized(job)

    def _process_serialized(self, job: Job) -> StrategyJobExecutionResult:
        """Execute one job while mutable job-local runtime bindings are exclusive."""

        try:
            self._validate_job(job)
            self._job_id = job.id
            self._job_type = job.job_type
            payload = _job_payload(job)
            requested = payload.get("htf_timeframe")
            self._job_htf_timeframe = str(requested) if requested else None
            strategy = self.registry.get_strategy(payload["strategy_id"])
            expected_job_type = _strategy_job_type(strategy.mode)
            if job.job_type != expected_job_type:
                raise RuntimeError(
                    "strategy job type does not match the stored execution mode"
                )
            expected_input = {
                "artifact_id": strategy.artifact_id,
                "params_hash": strategy.params_hash,
                "timeframe": parse_timeframe(strategy.timeframe).canonical,
                "instrument_key": (
                    f"{strategy.exchange.lower()}:{strategy.market_type.lower()}:"
                    f"{strategy.symbol.upper()}:{strategy.price_type.lower()}"
                ),
                "semantic_profile": strategy.semantic_profile,
            }
            strict_identity = {
                "artifact_id",
                "params_hash",
                "semantic_profile",
            } <= payload.keys()
            if strict_identity and (
                not strategy.enabled
                or strategy.archived
                or strategy.status != "running"
            ):
                raise RuntimeError("strategy is not active for delegated execution")
            if strict_identity:
                for field, expected in expected_input.items():
                    if field not in payload:
                        continue
                    actual = payload[field]
                    if field == "instrument_key" and str(actual).count(":") == 2:
                        actual = f"{actual}:{strategy.price_type.lower()}"
                    if actual != expected:
                        raise RuntimeError(
                            f"strategy job {field} does not match stored strategy identity"
                        )
            if "mtf_series" in payload:
                from openpine.runtime.mtf import admitted_mtf_requests

                self._job_mtf_series = admitted_mtf_requests(
                    chart_symbol=strategy.symbol,
                    htf_timeframe=self._job_htf_timeframe,
                    mtf_series=payload.get("mtf_series"),
                )
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
            execution_bars = (
                self._load_paper_replay_bars(strategy, bar)
                if job.job_type == JobType.PAPER_BAR_PROCESS
                else [bar]
            )
            execution = (
                self.execution_lease(job.id)
                if self.execution_lease is not None
                else nullcontext()
            )
            with execution:
                runtime_result = self._run_strategy(strategy, execution_bars, None)
            status = str(getattr(runtime_result, "status", "completed")).lower()
            if status not in {"ok", "completed"}:
                raise RuntimeError(f"strategy runtime failed with status={status}")

            if job.job_type == JobType.PAPER_BAR_PROCESS:
                snapshot_state: object = self._paper_evaluation_snapshot(job, execution_bars, bar)
            else:
                snapshot_state = getattr(runtime_result, "resume_state", None)
                if snapshot_state is None:
                    snapshot_state = getattr(
                        getattr(runtime_result, "raw_result", None),
                        "resume_state",
                        None,
                    )
            publication = (
                self.publication_lease(job.id)
                if self.publication_lease is not None
                else nullcontext()
            )
            with publication:
                trades_recorded = self._record_ledger(
                    strategy, job, bar, runtime_result
                )
                snapshot = self.state_store.save_runtime_snapshot(
                    strategy_id=strategy.strategy_id,
                    artifact_id=strategy.artifact_id,
                    params_hash=strategy.params_hash,
                    instrument_key=state_key["instrument_key"],
                    timeframe=state_key["timeframe"],
                    runtime_state=snapshot_state,
                    bar_time=bar.time,
                    reason=f"{job.job_type.value}",
                    failed_bar=False,
                )
                if job.job_type == JobType.PAPER_BAR_PROCESS and snapshot is None:
                    raise RuntimeError(
                        "paper evaluation snapshot publication is required"
                    )
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
        finally:
            self._job_htf_timeframe = None
            self._job_mtf_series = None
            self._job_id = None
            self._job_type = None
            self._job_run_identity = None
            self._job_broker_account_ref = None
            self._job_paper_epoch_start = None

    def _validate_job(self, job: Job) -> None:
        if job.job_type not in {
            JobType.PAPER_BAR_PROCESS,
            JobType.LIVE_BAR_PROCESS,
            JobType.OBSERVE_BAR_PROCESS,
        }:
            raise ValueError(f"unsupported strategy job type: {job.job_type}")
        _job_payload(job)

    def _load_target_bar(self, strategy: StrategyInstance, payload: dict[str, Any]) -> Bar:
        instrument = _instrument_from_payload(strategy, payload)
        timeframe = parse_timeframe(str(payload["timeframe"]))
        bar_time = int(payload["bar_time"])
        duration_ms = timeframe.duration_ms or 0
        recorded_close_time = int(payload.get("bar_close_time") or bar_time + duration_ms)
        bar_close_time = max(recorded_close_time, bar_time + duration_ms)
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

    def _load_paper_replay_bars(self, strategy: StrategyInstance, target_bar: Bar) -> list[Bar]:
        duration_ms = target_bar.timeframe.duration_ms
        if duration_ms is None or duration_ms <= 0:
            raise RuntimeError("paper replay requires a fixed-duration timeframe")
        activation_ms = self._paper_epoch_start(strategy)
        if not callable(getattr(self.registry, "execution_epoch_started_at", None)):
            activation_ms = min(activation_ms, int(target_bar.time))
            self._job_paper_epoch_start = activation_ms
        replay_start = ((activation_ms + duration_ms - 1) // duration_ms) * duration_ms
        if replay_start > target_bar.time:
            raise RuntimeError("target bar predates the strategy activation boundary")
        replay_end = target_bar.time + duration_ms
        bars: list[Bar] = []
        for open_time in range(replay_start, replay_end, duration_ms):
            query = BarQuery(
                instrument=target_bar.instrument,
                timeframe=target_bar.timeframe,
                start_ms=open_time,
                end_ms=open_time + duration_ms,
                source="storage",
                gap_policy="fail",
            )
            loaded = list(self.orchestrator.get_bars(query))
            if len(loaded) != 1 or int(loaded[0].time) != open_time or not bool(loaded[0].closed):
                raise RuntimeError(
                    "paper replay requires every closed canonical bar from activation"
                )
            bars.append(loaded[0])
        return bars

    def _paper_epoch_start(self, strategy: StrategyInstance) -> int:
        if self._job_paper_epoch_start is not None:
            return self._job_paper_epoch_start
        loader = getattr(self.registry, "execution_epoch_started_at", None)
        persisted = (
            loader(strategy.strategy_id, mode="paper") if callable(loader) else None
        )
        raw_epoch = persisted if isinstance(persisted, int) and not isinstance(persisted, bool) else strategy.updated_at
        epoch = max(0, int(raw_epoch))
        self._job_paper_epoch_start = epoch
        return epoch

    def _paper_evaluation_snapshot(
        self, job: Job, replay_bars: list[Bar], target_bar: Bar
    ) -> dict[str, object]:
        identity = self._job_run_identity or {}
        return {
            "schema_version": "openpine.paper.evaluation.v1",
            "resume_policy": "deterministic_replay",
            "paper_epoch_policy": "reset_on_activation",
            "run_id": str(identity.get("run_id") or f"strategy-job-{job.id}"),
            "run_hash": identity.get("content_hash"),
            "data_snapshot_hash": identity.get("data_snapshot_hash"),
            "broker_adapter_ref": identity.get("broker_adapter_ref"),
            "broker_account_ref": identity.get("broker_account_ref"),
            "replay_start_bar_time": int(replay_bars[0].time),
            "processed_bar_time": int(target_bar.time),
            "bars_replayed": len(replay_bars),
        }

    def _stamped_artifact_source(self, strategy: StrategyInstance) -> bytes:
        key = (str(strategy.pine_id), str(strategy.artifact_id))
        stamped = self._stamped_sources.get(key)
        if stamped:
            return stamped
        if self.artifact_store is None:
            stamped = capture_generated_source(strategy.pine_id, strategy.artifact_id)
        else:
            from openpine.run_identity import verified_generated_source

            artifact = self.artifact_store.get_artifact(strategy.artifact_id, strategy.pine_id)
            stamped = verified_generated_source(artifact)
        if not stamped:
            raise RuntimeError("captured artifact source is missing")
        self._stamped_sources[key] = stamped
        return stamped

    def _canonical_envelopes_for_bars(self, bars: list[Bar]) -> tuple[dict[str, object], ...]:
        envelopes: list[dict[str, object]] = []
        for bar in bars:
            duration_ms = bar.timeframe.duration_ms or 0
            close_time = getattr(bar, "time_close", None)
            query = BarQuery(
                instrument=bar.instrument,
                timeframe=bar.timeframe,
                start_ms=bar.time,
                end_ms=max(int(close_time or 0), bar.time + duration_ms),
                source="storage",
                gap_policy="fail",
            )
            series = self.orchestrator.load_bars(query)
            canonical = getattr(series, "canonical_bars", None)
            loaded = list(getattr(series, "bars", ()))
            if (
                not isinstance(canonical, tuple)
                or len(canonical) != 1
                or len(loaded) != 1
                or int(loaded[0].time) != int(bar.time)
            ):
                raise RuntimeError("canonical marketdata bar envelope is required")
            envelopes.append(dict(canonical[0]))
        return tuple(envelopes)

    def _bind_isolated_config(
        self,
        strategy: StrategyInstance,
        bars: list[Bar],
        config: BacktestRunConfig,
        htf_bars,
    ) -> None:
        from openpine.admission import load_active_deployment_identity
        from openpine.artifacts import ArtifactStore
        from openpine.config import OpenPineConfig
        from openpine.run_identity import bind_isolated_execution
        from openpine.runtime.admitted_manifest import load_admitted_manifest

        if not bars:
            raise RuntimeError("strategy job execution requires chart bars")
        last_bar = bars[-1]
        bar_envelopes = self._canonical_envelopes_for_bars(bars)

        runtime_config = OpenPineConfig.load()
        manifest_path = runtime_config.deployment_manifest
        wheelhouse = runtime_config.deployment_wheelhouse
        if manifest_path is None or wheelhouse is None:
            raise RuntimeError("strategy job execution requires an admitted deployment")
        store = self.artifact_store or ArtifactStore()
        artifact = store.get_artifact(strategy.artifact_id, strategy.pine_id)
        deployment = load_active_deployment_identity(manifest_path, wheelhouse)
        broker_adapter_ref = None
        broker_account_ref = None
        mode = "backtest"
        if self._job_type == JobType.PAPER_BAR_PROCESS:
            mode = "paper"
            broker_adapter_ref, broker_account_ref = _paper_broker_identity(
                strategy,
                deployment,
                paper_epoch_start=self._paper_epoch_start(strategy),
            )
            self._job_broker_account_ref = broker_account_ref
        elif self._job_type == JobType.LIVE_BAR_PROCESS:
            raise RuntimeError("LIVE_RC_BLOCKED")
        self._job_run_identity = bind_isolated_execution(
            config,
            data_dir=runtime_config.data_dir,
            deployment=deployment,
            admitted_manifest=load_admitted_manifest(manifest_path),
            mode=mode,
            run_id=f"strategy-job-{self._job_id or last_bar.time}",
            strategy_id=strategy.strategy_id,
            artifact=artifact,
            bars=bars,
            bar_envelopes=bar_envelopes,
            supplemental_bars=htf_bars,
            params=_strategy_params(strategy),
            created_at_utc_ms=max(0, int(last_bar.time_close)),
            broker_adapter_ref=broker_adapter_ref,
            broker_account_ref=broker_account_ref,
        )

    def _run_strategy(
        self, strategy: StrategyInstance, bars: Bar | list[Bar], resume_state: Any | None
    ) -> Any:
        if isinstance(bars, Bar):
            bars = [bars]
        if not bars:
            raise RuntimeError("strategy execution requires chart bars")
        config = _build_bar_run_config(strategy, bars, artifact_store=self.artifact_store)
        params = _strategy_params(strategy)
        htf_bars = self._confirmed_htf_bars(strategy, bars)
        source = self._stamped_artifact_source(strategy)
        self._bind_isolated_config(strategy, bars, config, htf_bars)
        return self.runtime_adapter.run_isolated(
            source,
            bars,
            config,
            resume_state=resume_state,
            htf_bars=htf_bars,
            params=params,
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
        if job.job_type == JobType.OBSERVE_BAR_PROCESS:
            return 0
        source = (
            LedgerSource.LIVE if job.job_type == JobType.LIVE_BAR_PROCESS else LedgerSource.PAPER
        )
        raw_result = getattr(runtime_result, "raw_result", runtime_result)
        resume_state = getattr(runtime_result, "resume_state", None) or getattr(
            raw_result, "resume_state", None
        )
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
                account_id=self._job_broker_account_ref or _paper_account_ref(strategy, self._paper_epoch_start(strategy)),
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
            if exit_time is None or int(exit_time) > bar.time_close:
                continue
            if source == LedgerSource.PAPER:
                if int(exit_time) < self._paper_epoch_start(strategy):
                    continue
            elif int(exit_time) < bar.time:
                continue
            self.ledger.record_trade(
                StrategyTrade(
                    trade_id=_ledger_trade_id(
                        strategy.strategy_id,
                        self._job_broker_account_ref or _paper_account_ref(strategy, self._paper_epoch_start(strategy)),
                        source,
                        trade,
                    ),
                    strategy_id=strategy.strategy_id,
                    account_id=self._job_broker_account_ref or _paper_account_ref(strategy, self._paper_epoch_start(strategy)),
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


def _paper_broker_identity(
    strategy: StrategyInstance,
    deployment: Any,
    *,
    paper_epoch_start: int,
) -> tuple[str, str]:
    wheel_identities = getattr(deployment, "wheel_identities", ())
    engine_identity = next(
        (
            (str(name).replace("_", "-"), str(version), str(digest))
            for name, version, digest in wheel_identities
            if str(name).replace("_", "-") == "backtest-engine"
        ),
        None,
    )
    if engine_identity is None:
        raise RuntimeError("admitted backtest-engine wheel identity is required")
    name, version, digest = engine_identity
    if not version or not digest.startswith("sha256:") or len(digest) != 71:
        raise RuntimeError("admitted backtest-engine wheel identity is invalid")
    values = (
        strategy.strategy_id,
        strategy.artifact_id,
        strategy.params_hash,
        str(paper_epoch_start),
    )
    if any(not isinstance(value, str) or not value for value in values):
        raise RuntimeError("paper account identity is incomplete")
    return (
        f"urn:openpine:paper-broker:{name}:{version}:{digest}",
        _paper_account_ref(strategy, paper_epoch_start),
    )


def _paper_account_ref(
    strategy: StrategyInstance, paper_epoch_start: int
) -> str:
    return (
        "urn:openpine:paper-account:"
        f"{strategy.strategy_id}:{strategy.artifact_id}:"
        f"{strategy.params_hash}:{int(paper_epoch_start)}"
    )


def _strategy_job_type(mode: str) -> JobType:
    normalized = str(mode).lower()
    if normalized == "live":
        return JobType.LIVE_BAR_PROCESS
    if normalized == "observe":
        return JobType.OBSERVE_BAR_PROCESS
    if normalized == "paper":
        return JobType.PAPER_BAR_PROCESS
    raise RuntimeError(f"unsupported stored strategy execution mode: {mode}")


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


def _build_bar_run_config(
    strategy: StrategyInstance,
    bars: Bar | list[Bar],
    *,
    artifact_store: Any | None = None,
) -> BacktestRunConfig:
    if isinstance(bars, Bar):
        bars = [bars]
    if not bars:
        raise RuntimeError("strategy execution requires chart bars")
    first_bar = bars[0]
    last_bar = bars[-1]
    artifact = _load_sealed_artifact(strategy, artifact_store=artifact_store)
    decl_args = normalize_strategy_declaration_args(_declaration_args_from_artifact(artifact))
    commission_type = {
        "cash_per_order": "fixed_per_order",
        "cash_per_contract": "fixed_per_contract",
    }.get(
        str(decl_args.get("commission_type", "none")),
        decl_args.get("commission_type", "none"),
    )
    kwargs = {
        "symbol": strategy.symbol,
        "timeframe": first_bar.timeframe.canonical,
        "exchange": strategy.exchange.lower(),
        "market_type": strategy.market_type.lower(),
        "start_time": first_bar.time,
        "end_time": last_bar.time_close,
        "score_start_time": first_bar.time,
        "score_end_time": last_bar.time_close,
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
        "qty_rounding_mode": default_qty_rounding_mode(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        "export_resume_state": True,
        "content_hash_enabled": False,
        "collect_events": False,
        "collect_order_lifecycle": False,
        "semantic_profile": require_strategy_semantic_profile(strategy).value,
        "generated_artifact": artifact.get("generated_artifact") if artifact else None,
        "execution_context": _execution_context_from_artifact(artifact),
        "instrument_id": (
            f"{strategy.exchange.lower()}:{strategy.market_type.lower()}:{strategy.symbol}"
        ),
    }
    supported = set(inspect.signature(BacktestRunConfig).parameters)
    return BacktestRunConfig(**{key: value for key, value in kwargs.items() if key in supported})


def _load_sealed_artifact(
    strategy: StrategyInstance, *, artifact_store: Any | None = None
) -> dict[str, Any] | None:
    if not strategy.pine_id or not strategy.artifact_id:
        return None
    try:
        if artifact_store is None:
            from openpine.artifacts import ArtifactStore

            artifact_store = ArtifactStore()
        artifact = artifact_store.get_artifact(strategy.artifact_id, strategy.pine_id)
    except Exception:
        return None
    return artifact if isinstance(artifact, dict) else None


def _declaration_args_from_artifact(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if not artifact:
        return {}
    declaration = (
        artifact.get("compile_meta", {}).get("translation_metadata", {}).get("declaration", {})
    )
    args = declaration.get("arguments", {})
    return args if isinstance(args, dict) else {}


def _execution_context_from_artifact(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not artifact:
        return None
    envelope = artifact.get("generated_artifact")
    if not isinstance(envelope, dict):
        return None
    if envelope.get("schema_id") != "openpine.generated_artifact.v3":
        raise RuntimeError("sealed V3 generated artifact is required")
    context = artifact.get("execution_context")
    if not isinstance(context, dict):
        raise RuntimeError("sealed execution_context.v1 is required")
    from openpine_contracts import validate_payload, verify_content_hash

    try:
        validate_payload("openpine.execution_context.v1", context)
    except Exception as exc:
        raise RuntimeError("sealed execution_context.v1 is invalid") from exc
    if not verify_content_hash(context, schema_id="openpine.execution_context.v1"):
        raise RuntimeError("sealed execution_context.v1 content hash is invalid")
    if context.get("generated_artifact_hash") != envelope.get("content_hash"):
        raise RuntimeError("execution_context does not bind the generated artifact")
    if context.get("source_hash") != envelope.get("source_hash"):
        raise RuntimeError("execution_context source_hash does not match generated artifact")
    if context.get("emitted_module_hash") != envelope.get("emitted_module_hash"):
        raise RuntimeError("execution_context emitted_module_hash does not match generated artifact")
    return context


def _artifact_declaration_args(strategy: StrategyInstance) -> dict[str, Any]:
    return _declaration_args_from_artifact(_load_sealed_artifact(strategy))


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
    avg_price = (
        sum(
            float(getattr(trade, "entry_price", 0.0) or 0.0)
            * float(getattr(trade, "qty", 0.0) or 0.0)
            for trade in open_trades
        )
        / qty
        if qty
        else None
    )

    class _Position:
        pass

    position = _Position()
    position.size = qty if direction == "long" else -qty if direction == "short" else 0.0
    position.direction = direction
    position.avg_price = avg_price
    position.realized_profit = getattr(raw_result, "net_profit", 0.0)
    position.open_profit = None
    return position


def _ledger_trade_id(
    strategy_id: str,
    account_id_or_source: str | LedgerSource,
    source_or_trade: LedgerSource | Any,
    trade: Any | None = None,
) -> str:
    if trade is None:
        account_id = ""
        source = account_id_or_source
        trade = source_or_trade
    else:
        account_id = str(account_id_or_source)
        source = source_or_trade
    if not isinstance(source, LedgerSource):
        raise TypeError("ledger trade source is invalid")
    payload = {
        "strategy_id": strategy_id,
        "account_id": account_id,
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
