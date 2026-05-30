"""DataOrchestrator: single boundary for bar reading/writing.

Section OP-DL-004 of OpenPine.
Section 7.5 + 33.1 of OpenPine TZ v3.

Rules:
- get_bars(query: MDPBarQuery) -> list[Bar] is the ONLY read contract for bars.
- on_candle_closed(bar, instrument_key, timeframe) is the ONLY write boundary
  for confirmed closed candles from live feeds.

Architecture:
- Public API (get_bars, load_bars) accepts marketdata_provider.contracts.BarQuery.
- Storage is called with the same canonical query; OpenPine no longer owns a
  second BarQuery contract.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from marketdata_provider import create_candle_store
from marketdata_provider.config import MarketDataConfig, StorageConfig
from marketdata_provider.contracts import (
    Bar,
    BarSeries,
    CandleStore,
    CoverageReport,
    InstrumentKey,
    StoreResult,
    parse_timeframe,
)
from marketdata_provider.contracts import BarQuery as MDPBarQuery
from openpine.data.models import (
    AggregationRequirement,
    CandleCommitResult,
    DataGap,
    DataPlan,
    DataRequirement,
    EnsureDataResult,
)


class StrategyInstance:
    """Placeholder — replaced by real strategy instance model."""

    def __init__(self, strategy_id: str) -> None:
        self.id = strategy_id


class MarketDataProvider(Protocol):
    """Protocol for market data providers that can supply bars."""

    def fetch_bars(self, query: MDPBarQuery) -> BarSeries: ...


class DataCoverageError(RuntimeError):
    """Base class for fail-closed data coverage errors."""


class IncompleteCoverageError(DataCoverageError):
    """Raised when a query cannot be satisfied under gap_policy='fail'."""


class ProviderUnavailableError(DataCoverageError):
    """Raised when provider data is required but no provider is configured."""


class StorageUnavailableError(DataCoverageError):
    """Raised when storage access fails."""


def _default_candle_store() -> CandleStore:
    from openpine.config import OpenPineConfig

    config = OpenPineConfig.load()
    return create_candle_store(
        MarketDataConfig(
            storage=StorageConfig(cache_dir=config.data_cache_root),
        )
    )


class DataOrchestrator:
    """Single OpenPine boundary for bar reading/writing.

    This class manages:
    - Historical bar reads (get_bars)
    - Live candle persistence (on_candle_closed)
    - Gap detection (detect_gaps)
    - Data plan building (build_data_plan)
    - Data assurance (ensure_data)
    - Backfill scheduling (schedule_backfill)
    """

    def __init__(
        self,
        candle_store: CandleStore | None = None,
        provider: MarketDataProvider | None = None,
    ) -> None:
        """Initialize DataOrchestrator.

        Args:
            candle_store: Canonical marketdata-provider CandleStore.
            provider: Optional market data provider for live data
        """
        self._candle_store = candle_store or _default_candle_store()
        self._provider = provider
        self._pending_bars: list[tuple] = []  # (bar, instrument_key, timeframe, source)

    def set_provider(self, provider: MarketDataProvider) -> None:
        """Set the market data provider for bar fetching."""
        self._provider = provider

    def build_data_plan(self, strategy_instance: StrategyInstance) -> DataPlan:
        """Build data plan for a strategy instance.

        Args:
            strategy_instance: Strategy instance to build plan for

        Returns:
            DataPlan with requirements
        """
        # Placeholder: in production, this would query the strategy for its
        # data requirements (instrument, timeframe, ranges)
        return DataPlan(
            requirements=[],
            aggregation_requirements=[],
        )

    def ensure_data(self, plan: DataPlan) -> EnsureDataResult:
        """Ensure all data in the plan is available.

        Args:
            plan: DataPlan with requirements to satisfy

        Returns:
            EnsureDataResult with status and remaining gaps
        """
        gaps_remaining: list[DataGap] = []

        for req in plan.requirements:
            if not isinstance(req, DataRequirement):
                continue

            query = MDPBarQuery(
                instrument=InstrumentKey(
                    exchange=req.exchange,
                    market=req.market_type,
                    symbol=req.symbol,
                ),
                timeframe=parse_timeframe(req.timeframe),
                start_ms=req.from_time or 0,
                end_ms=req.to_time or int(2**63 - 1),
                source="storage",
            )

            gaps = self.detect_gaps(query)
            if gaps:
                gaps_remaining.extend(gaps)

        return EnsureDataResult(
            success=True,
            gaps_filled=0,
            gaps_remaining=gaps_remaining,
        )

    def load_bars(self, query: MDPBarQuery) -> BarSeries:
        """Single canonical read contract for bars.

        Reads according to query.source. Storage/provider errors are raised, never converted
        into an empty result.
        """
        if query.source == "storage":
            return self._load_from_storage(query, require_complete=query.gap_policy == "fail")

        if query.source == "provider":
            provider_series = self._load_from_provider(query)
            return self._require_complete(provider_series, "provider") if query.gap_policy == "fail" else provider_series

        if query.source != "auto":
            raise ValueError(f"unsupported data source: {query.source}")

        storage_series = self._load_from_storage(query, require_complete=False)
        if _is_complete(storage_series):
            return storage_series

        provider_series = self._load_missing_from_provider(query, storage_series.coverage.missing_intervals)
        self._write_provider_series(provider_series)
        merged = _merge_series(query, storage_series, provider_series)
        return self._require_complete(merged, "auto") if query.gap_policy == "fail" else merged

    def get_bars(self, query: MDPBarQuery) -> list[Bar]:
        """Return canonical bars for callers that need a plain sequence."""

        return list(self.load_bars(query).bars)

    def _load_from_storage(self, query: MDPBarQuery, *, require_complete: bool) -> BarSeries:
        try:
            series = self._candle_store.read(query)
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return self._require_complete(series, "storage") if require_complete else series

    def _load_from_provider(self, query: MDPBarQuery) -> BarSeries:
        if self._provider is None:
            raise ProviderUnavailableError("market data provider is not configured")
        try:
            return self._provider.fetch_bars(query)
        except Exception:
            raise

    def _load_missing_from_provider(
        self,
        query: MDPBarQuery,
        missing_intervals: tuple[tuple[int, int], ...],
    ) -> BarSeries:
        if not missing_intervals:
            return BarSeries(
                query=query,
                bars=(),
                coverage=_coverage_for(query, (), "provider"),
            )

        fetched: list[Bar] = []
        for start_ms, end_ms in missing_intervals:
            missing_query = replace(query, start_ms=start_ms, end_ms=end_ms, source="provider")
            missing_series = self._load_from_provider(missing_query)
            if query.gap_policy == "fail":
                self._require_complete(missing_series, "provider")
            fetched.extend(missing_series.bars)

        bars = tuple(sorted(fetched, key=lambda bar: bar.time))
        return BarSeries(query=query, bars=bars, coverage=_coverage_for(query, bars, "provider"))

    def _write_provider_series(self, series: BarSeries) -> None:
        if not series.bars:
            return
        result = self._candle_store.write(series)
        if not result.success:
            raise StorageUnavailableError(result.error or "failed to persist provider bars")

    @staticmethod
    def _require_complete(series: BarSeries, source: str) -> BarSeries:
        if _is_complete(series):
            return series
        raise IncompleteCoverageError(
            f"{source} coverage incomplete for "
            f"{series.query.instrument.exchange}/{series.query.instrument.market}/"
            f"{series.query.instrument.symbol} {series.query.timeframe.canonical}: "
            f"{series.coverage.missing_intervals or series.coverage.status}"
        )

    def detect_gaps(self, query: MDPBarQuery) -> list[DataGap]:
        """Detect gaps in the candle data.

        Args:
            query: Canonical marketdata_provider BarQuery

        Returns:
            List of DataGap objects
        """
        if hasattr(self._candle_store, "detect_gaps"):
            return list(self._candle_store.detect_gaps(query))  # type: ignore[attr-defined]
        coverage = self._candle_store.coverage(query)
        return [_data_gap_from_interval(query, start, end) for start, end in coverage.missing_intervals]

    def schedule_backfill(self, requirement: DataRequirement) -> str:
        """Schedule a backfill job for a data requirement.

        Args:
            requirement: DataRequirement to backfill

        Returns:
            Job ID for the backfill task
        """
        import uuid

        job_id = f"backfill_{uuid.uuid4().hex[:12]}"
        # In production, this would create a job in the jobs table
        return job_id

    def on_candle_closed(
        self,
        bar: Bar,
        instrument_key: str,
        timeframe: str,
        source: str = "live",
    ) -> CandleCommitResult:
        """Write boundary for confirmed closed candles from live feeds.

        Stores bar durably to parquet and updates manifest.

        Args:
            bar: The confirmed closed Bar
            instrument_key: Full instrument key string
            timeframe: Timeframe string (e.g. "1m", "5m")
            source: Source of the candle ("live", "backfill", "import")

        Returns:
            CandleCommitResult with success status and manifest_id
        """
        try:
            query = MDPBarQuery(
                instrument=bar.instrument,
                timeframe=bar.timeframe,
                start_ms=bar.time,
                end_ms=bar.time_close,
                source="storage",
            )
            result = self._candle_store.write(
                BarSeries(
                    query=query,
                    bars=(bar,),
                    coverage=_coverage_for(query, (bar,), source),
                )
            )

            return CandleCommitResult(
                success=result.success,
                error=result.error,
            )

        except Exception as e:
            return CandleCommitResult(
                success=False,
                error=str(e),
            )

    def list_manifests(self, query: MDPBarQuery) -> list[CandleManifest]:
        """List candle manifests for a query.

        Args:
            query: Canonical marketdata_provider BarQuery

        Returns:
            List of CandleManifest objects
        """
        if hasattr(self._candle_store, "list_manifests"):
            return list(self._candle_store.list_manifests(query))  # type: ignore[attr-defined]
        return []


def _coverage_for(query: MDPBarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    if not bars:
        return CoverageReport(
            requested_start_ms=query.start_ms,
            requested_end_ms=query.end_ms,
            delivered_start_ms=None,
            delivered_end_ms=None,
            missing_intervals=((query.start_ms, query.end_ms),),
            source_mix=(source,),
            status="empty",
        )
    duplicate_timestamps = tuple(sorted({bar.time for bar in bars if sum(1 for other in bars if other.time == bar.time) > 1}))
    ordered = all(bars[i].time < bars[i + 1].time for i in range(len(bars) - 1))
    missing_intervals = _missing_intervals(query, bars) if ordered and not duplicate_timestamps else ()
    status = "valid"
    if duplicate_timestamps:
        status = "duplicate"
    elif not ordered:
        status = "unordered"
    elif missing_intervals:
        status = "gap"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        missing_intervals=missing_intervals,
        duplicate_timestamps=duplicate_timestamps,
        source_mix=(source,),
        status=status,
    )


def _missing_intervals(query: MDPBarQuery, bars: tuple[Bar, ...]) -> tuple[tuple[int, int], ...]:
    intervals: list[tuple[int, int]] = []
    if bars[0].time > query.start_ms:
        intervals.append((query.start_ms, bars[0].time))

    duration_ms = query.timeframe.duration_ms
    if duration_ms:
        expected_next = bars[0].time + duration_ms
        for bar in bars[1:]:
            if bar.time > expected_next:
                intervals.append((expected_next, bar.time))
            expected_next = bar.time + duration_ms

    delivered_end = bars[-1].time_close
    if delivered_end < query.end_ms:
        intervals.append((delivered_end, query.end_ms))
    return tuple(intervals)


def _data_gap_from_interval(query: MDPBarQuery, start_ms: int, end_ms: int) -> DataGap:
    now_ms = int(__import__("time").time() * 1000)
    return DataGap(
        gap_id=(
            f"gap_{query.instrument.exchange}:{query.instrument.market}:"
            f"{query.instrument.symbol}:trade_{query.timeframe.canonical}_{start_ms}_{end_ms}"
        ),
        exchange=query.instrument.exchange,
        market_type=query.instrument.market,
        symbol=query.instrument.symbol,
        price_type="trade",
        timeframe=query.timeframe.canonical,
        provider="marketdata-provider",
        gap_start=start_ms,
        gap_end=end_ms,
        created_at=now_ms,
        updated_at=now_ms,
    )


def _is_complete(series: BarSeries) -> bool:
    return bool(series.bars) and series.coverage.status == "valid" and not series.coverage.missing_intervals


def _merge_series(query: MDPBarQuery, left: BarSeries, right: BarSeries) -> BarSeries:
    by_time = {bar.time: bar for bar in left.bars}
    by_time.update({bar.time: bar for bar in right.bars})
    bars = tuple(sorted(by_time.values(), key=lambda bar: bar.time))
    return BarSeries(
        query=query,
        bars=bars,
        coverage=_coverage_for(query, bars, "storage+provider"),
    )


__all__ = [
    "DataCoverageError",
    "DataOrchestrator",
    "IncompleteCoverageError",
    "MarketDataProvider",
    "ProviderUnavailableError",
    "StorageUnavailableError",
    "StrategyInstance",
]
