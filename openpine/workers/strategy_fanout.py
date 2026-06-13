"""Fan out closed market bars into strategy processing jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from marketdata_provider.contracts import Bar, BarQuery, parse_timeframe
from openpine.data.orchestrator import IncompleteCoverageError
from openpine.data.orchestrator import DataOrchestrator
from openpine.data.periodic_fetcher import RawMarketKey
from openpine.jobs import Job, JobScheduler, JobType
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance


class FanoutStatus(StrEnum):
    ENQUEUED = "enqueued"
    NO_STRATEGIES = "no_strategies"
    TARGET_NOT_CLOSED = "target_not_closed"
    SOURCE_INCOMPLETE = "source_incomplete"


@dataclass(frozen=True)
class TargetBarResult:
    timeframe: str
    status: FanoutStatus
    bar: Bar | None = None
    jobs: tuple[Job, ...] = ()


@dataclass(frozen=True)
class SourceBarFanoutResult:
    market_key: RawMarketKey
    source_timeframe: str
    strategies: int
    targets: tuple[TargetBarResult, ...] = ()

    @property
    def jobs(self) -> tuple[Job, ...]:
        return tuple(job for target in self.targets for job in target.jobs)


@dataclass(frozen=True)
class StrategyBarFanoutConfig:
    source_timeframe: str = "1m"
    persist_source: bool = True
    persist_aggregates: bool = True
    source: str = "live"
    priority: int = 50


@dataclass(frozen=True)
class _StrategyGroup:
    timeframe: str
    strategies: tuple[StrategyInstance, ...] = field(default_factory=tuple)


class StrategyBarFanout:
    """Bridge source candles to idempotent strategy processing jobs.

    This layer does not execute orders. It persists a closed source candle once,
    derives closed target-timeframe candles from source candles, and enqueues one
    paper/live processing job per enabled strategy that requires that target bar.
    """

    def __init__(
        self,
        *,
        registry: SQLiteStrategyRegistry,
        orchestrator: DataOrchestrator,
        scheduler: JobScheduler,
        config: StrategyBarFanoutConfig | None = None,
    ) -> None:
        self.registry = registry
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.config = config or StrategyBarFanoutConfig()

    def process_source_bar(self, bar: Bar) -> SourceBarFanoutResult:
        source_timeframe = parse_timeframe(self.config.source_timeframe)
        if bar.timeframe.canonical != source_timeframe.canonical:
            raise ValueError(
                f"StrategyBarFanout expects {source_timeframe.canonical} source bars, "
                f"got {bar.timeframe.canonical}"
            )
        market_key = RawMarketKey(
            exchange=bar.instrument.exchange.lower(),
            market_type=bar.instrument.market.lower(),
            symbol=bar.instrument.symbol.upper(),
            price_type="trade",
        )
        strategies = _enabled_strategies_for_market(
            self.registry.list_strategies(), market_key
        )
        if self.config.persist_source:
            self.orchestrator.on_candle_closed(
                bar,
                instrument_key=market_key.instrument_key,
                timeframe=source_timeframe.canonical,
                source=self.config.source,
            )
        if not strategies:
            return SourceBarFanoutResult(
                market_key=market_key,
                source_timeframe=source_timeframe.canonical,
                strategies=0,
                targets=(
                    TargetBarResult(
                        source_timeframe.canonical, FanoutStatus.NO_STRATEGIES
                    ),
                ),
            )

        targets: list[TargetBarResult] = []
        for group in _group_by_target_timeframe(strategies):
            target_bar = self._target_bar_from_source(bar, group.timeframe)
            if target_bar is None:
                targets.append(
                    TargetBarResult(group.timeframe, FanoutStatus.TARGET_NOT_CLOSED)
                )
                continue
            if target_bar is not bar and self.config.persist_aggregates:
                self.orchestrator.on_candle_closed(
                    target_bar,
                    instrument_key=market_key.instrument_key,
                    timeframe=group.timeframe,
                    source="aggregate",
                )
            jobs = tuple(
                self._enqueue_strategy_job(strategy, target_bar, market_key)
                for strategy in group.strategies
            )
            targets.append(
                TargetBarResult(
                    group.timeframe, FanoutStatus.ENQUEUED, bar=target_bar, jobs=jobs
                )
            )

        return SourceBarFanoutResult(
            market_key=market_key,
            source_timeframe=source_timeframe.canonical,
            strategies=len(strategies),
            targets=tuple(targets),
        )

    def _target_bar_from_source(
        self, source_bar: Bar, target_timeframe: str
    ) -> Bar | None:
        source_timeframe = parse_timeframe(self.config.source_timeframe)
        target = parse_timeframe(target_timeframe)
        if target.duration_ms is None or source_timeframe.duration_ms is None:
            raise ValueError(
                f"variable duration timeframe is not supported for fanout: {target_timeframe}"
            )
        if target.duration_ms < source_timeframe.duration_ms:
            raise ValueError(
                f"target timeframe {target.canonical} is below source {source_timeframe.canonical}"
            )
        if target.duration_ms % source_timeframe.duration_ms != 0:
            raise ValueError(
                f"target timeframe {target.canonical} is not a multiple of source {source_timeframe.canonical}"
            )
        if target.canonical == source_timeframe.canonical:
            return source_bar

        source_close_exclusive = source_bar.time + source_timeframe.duration_ms
        if source_close_exclusive % target.duration_ms != 0:
            return None
        start_ms = source_close_exclusive - target.duration_ms
        query = BarQuery(
            instrument=source_bar.instrument,
            timeframe=source_timeframe,
            start_ms=start_ms,
            end_ms=source_close_exclusive,
            source="storage",
            gap_policy="fail",
        )
        try:
            source_bars = self.orchestrator.get_bars(query)
        except IncompleteCoverageError:
            return None
        expected = target.duration_ms // source_timeframe.duration_ms
        if len(source_bars) != expected:
            return None
        return _aggregate_bars(source_bars, target_timeframe=target.canonical)

    def _enqueue_strategy_job(
        self,
        strategy: StrategyInstance,
        bar: Bar,
        market_key: RawMarketKey,
    ) -> Job:
        job_type = _job_type_for_strategy_mode(strategy.mode)
        idempotency_key = (
            f"{job_type.value}:{strategy.strategy_id}:"
            f"{market_key.instrument_key}:{bar.timeframe.canonical}:{bar.time}"
        )
        job = Job(
            job_type=job_type,
            strategy_id=strategy.strategy_id,
            idempotency_key=idempotency_key,
            serialization_key=strategy.strategy_id,
            priority=self.config.priority,
            input={
                "strategy_id": strategy.strategy_id,
                "artifact_id": strategy.artifact_id,
                "params_hash": strategy.params_hash,
                "instrument_key": market_key.instrument_key,
                "timeframe": bar.timeframe.canonical,
                "bar_time": bar.time,
                "bar_close_time": bar.time_close,
                "source": self.config.source,
            },
        )
        return self.scheduler.enqueue(job)


def _enabled_strategies_for_market(
    strategies: Iterable[StrategyInstance],
    market_key: RawMarketKey,
) -> tuple[StrategyInstance, ...]:
    return tuple(
        strategy
        for strategy in strategies
        if strategy.enabled
        and strategy.exchange.lower() == market_key.exchange
        and strategy.market_type.lower() == market_key.market_type
        and strategy.symbol.upper() == market_key.symbol
        and strategy.price_type.lower() == market_key.price_type
    )


def _group_by_target_timeframe(
    strategies: Iterable[StrategyInstance],
) -> tuple[_StrategyGroup, ...]:
    groups: dict[str, list[StrategyInstance]] = {}
    for strategy in strategies:
        timeframe = parse_timeframe(strategy.timeframe).canonical
        groups.setdefault(timeframe, []).append(strategy)
    return tuple(
        _StrategyGroup(timeframe, tuple(group))
        for timeframe, group in sorted(groups.items())
    )


def _job_type_for_strategy_mode(mode: str) -> JobType:
    normalized = mode.lower()
    if normalized == "live":
        return JobType.LIVE_BAR_PROCESS
    if normalized == "observe":
        return JobType.OBSERVE_BAR_PROCESS
    return JobType.PAPER_BAR_PROCESS


def _aggregate_bars(bars: list[Bar], *, target_timeframe: str) -> Bar:
    if not bars:
        raise ValueError("cannot aggregate empty bar series")
    target = parse_timeframe(target_timeframe)
    ordered = sorted(bars, key=lambda bar: bar.time)
    return Bar(
        instrument=ordered[0].instrument,
        timeframe=target,
        time=ordered[0].time,
        time_close=ordered[0].time + (target.duration_ms or 0),
        open=ordered[0].open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=ordered[-1].close,
        volume=sum(float(bar.volume or 0.0) for bar in ordered),
        closed=True,
    )


__all__ = [
    "FanoutStatus",
    "SourceBarFanoutResult",
    "StrategyBarFanout",
    "StrategyBarFanoutConfig",
    "TargetBarResult",
    "_job_type_for_strategy_mode",
]
