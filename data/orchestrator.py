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

from typing import Optional, Protocol

from marketdata_provider.contracts import (
    Bar,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)
from marketdata_provider.contracts import BarQuery as MDPBarQuery
from openpine.data.candle_storage import CandleStorage
from openpine.data.contracts import WriteMode
from openpine.data.models import (
    AggregationRequirement,
    CandleCommitResult,
    CandleManifest,
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
        candle_storage: Optional[CandleStorage] = None,
        provider: Optional[MarketDataProvider] = None,
    ) -> None:
        """Initialize DataOrchestrator.

        Args:
            candle_storage: CandleStorage instance for parquet read/write
            provider: Optional market data provider for live data
        """
        self._candle_storage = candle_storage or CandleStorage()
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

            gaps = self._candle_storage.detect_gaps(query)
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

        provider_series = self._load_from_provider(query)
        if query.gap_policy == "fail":
            self._require_complete(provider_series, "provider")
        self._write_provider_series(provider_series)
        return _merge_series(query, storage_series, provider_series)

    def get_bars(self, query: MDPBarQuery) -> list[Bar]:
        """Return canonical bars for callers that need a plain sequence."""

        return list(self.load_bars(query).bars)

    def _load_from_storage(self, query: MDPBarQuery, *, require_complete: bool) -> BarSeries:
        try:
            bars = tuple(
                _canonical_bar_from_storage_bar(bar, query)
                for bar in self._candle_storage.read_candles(query)
            )
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc

        series = BarSeries(
            query=query,
            bars=bars,
            coverage=_coverage_for(query, bars, "storage"),
        )
        return self._require_complete(series, "storage") if require_complete else series

    def _load_from_provider(self, query: MDPBarQuery) -> BarSeries:
        if self._provider is None:
            raise ProviderUnavailableError("market data provider is not configured")
        try:
            return self._provider.fetch_bars(query)
        except Exception:
            raise

    def _write_provider_series(self, series: BarSeries) -> None:
        if not series.bars:
            return
        result = self._candle_storage.write_candles(
            candles=list(series.bars),
            instrument_key=(
                f"{series.query.instrument.exchange}:"
                f"{series.query.instrument.market}:"
                f"{series.query.instrument.symbol}:trade"
            ),
            timeframe=series.query.timeframe.canonical,
        )
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
        return self._candle_storage.detect_gaps(query)

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
            result = self._candle_storage.write_candles(
                candles=[bar],
                mode=WriteMode.UPSERT_PARTITION,
                instrument_key=instrument_key,
                timeframe=timeframe,
            )

            if result.success and result.manifests_created:
                manifest_id = result.manifests_created[0].manifest_id
            else:
                manifest_id = None

            return CandleCommitResult(
                success=result.success,
                manifest_id=manifest_id,
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
        return self._candle_storage.list_manifests(query)


def _canonical_bar_from_storage_bar(bar: object, query: MDPBarQuery) -> Bar:
    time = int(getattr(bar, "time"))
    return Bar(
        instrument=InstrumentKey(
            exchange=query.instrument.exchange,
            market=query.instrument.market,
            symbol=query.instrument.symbol,
        ),
        timeframe=query.timeframe,
        time=time,
        time_close=int(getattr(bar, "time_close", query.end_ms)),
        open=float(getattr(bar, "open")),
        high=float(getattr(bar, "high")),
        low=float(getattr(bar, "low")),
        close=float(getattr(bar, "close")),
        volume=float(getattr(bar, "volume")) if getattr(bar, "volume", None) is not None else None,
        closed=bool(getattr(bar, "closed", True)),
    )


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
