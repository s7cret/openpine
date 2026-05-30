"""DataOrchestrator: single boundary for bar reading/writing.

Section OP-DL-004 of OpenPine.
Section 7.5 + 33.1 of OpenPine TZ v3.

Rules:
- get_bars(query: MDPBarQuery) -> list[Bar] is the ONLY read contract for bars.
- on_candle_closed(bar, instrument_key, timeframe) is the ONLY write boundary
  for confirmed closed candles from live feeds.

Architecture:
- Public API (get_bars, load_bars) accepts marketdata_provider.contracts.BarQuery (MDPBarQuery).
- Internal storage operations use openpine.data.bar_query.BarQuery (StorageBarQuery).
- Conversion between formats is done by _to_storage_query().
"""

from __future__ import annotations

from typing import Optional, Protocol

from marketdata_provider.contracts import (
    Bar,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    Timeframe,
)
from marketdata_provider.contracts import BarQuery as MDPBarQuery
from openpine.data.bar_query import BarQuery as StorageBarQuery
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

            # Build a storage-format query to check for existing data
            instrument_key = f"{req.exchange}:{req.market_type}:{req.symbol}:{req.price_type}"
            storage_query = StorageBarQuery(
                instrument_key=instrument_key,
                timeframe=req.timeframe,
                from_time=req.from_time,
                to_time=req.to_time,
            )

            gaps = self._candle_storage.detect_gaps(storage_query)
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
        storage_bars: list[Bar] = []
        storage_error: Exception | None = None

        if query.source in ("storage", "auto"):
            try:
                storage_bars = [
                    _canonical_bar_from_storage_bar(bar, query)
                    for bar in self._candle_storage.read_candles(_to_storage_query(query))
                ]
            except Exception as exc:
                storage_error = exc
                if query.source == "storage":
                    raise
            if storage_bars:
                bars_tuple = tuple(storage_bars)
                return BarSeries(
                    query=query,
                    bars=bars_tuple,
                    coverage=_coverage_for(query, bars_tuple, "storage"),
                )

        if query.source in ("provider", "auto") and self._provider is not None:
            try:
                return self._provider.fetch_bars(query)
            except Exception:
                raise

        if storage_error is not None:
            raise storage_error
        if query.source == "provider" and self._provider is None:
            raise RuntimeError("market data provider is not configured")

        return BarSeries(
            query=query,
            bars=(),
            coverage=CoverageReport(
                requested_start_ms=query.start_ms,
                requested_end_ms=query.end_ms,
                delivered_start_ms=None,
                delivered_end_ms=None,
                missing_intervals=((query.start_ms, query.end_ms),),
                status="empty",
            ),
        )

    def get_bars(self, query: MDPBarQuery) -> list[Bar]:
        """Return canonical bars for callers that need a plain sequence."""

        return list(self.load_bars(query).bars)

    def detect_gaps(self, query: MDPBarQuery | StorageBarQuery) -> list[DataGap]:
        """Detect gaps in the candle data.

        Args:
            query: BarQuery in canonical (MDPBarQuery) or storage format

        Returns:
            List of DataGap objects
        """
        storage_query = _to_storage_query(query) if isinstance(query, MDPBarQuery) else query
        return self._candle_storage.detect_gaps(storage_query)

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

    def list_manifests(self, query: MDPBarQuery | StorageBarQuery) -> list[CandleManifest]:
        """List candle manifests for a query.

        Args:
            query: BarQuery in canonical (MDPBarQuery) or storage format

        Returns:
            List of CandleManifest objects
        """
        storage_query = _to_storage_query(query) if isinstance(query, MDPBarQuery) else query
        return self._candle_storage.list_manifests(storage_query)


def _to_storage_query(query: MDPBarQuery | StorageBarQuery) -> StorageBarQuery:
    """Convert canonical marketdata_provider BarQuery to storage BarQuery.

    marketdata_provider BarQuery:
      - instrument: InstrumentKey(exchange, market, symbol)
      - timeframe: Timeframe(canonical)
      - start_ms, end_ms (end-exclusive per canonical TZ semantics)

    Storage BarQuery:
      - instrument_key: "exchange:market:symbol:price_type"
      - timeframe: canonical string
      - from_time, to_time (inclusive)
    """
    if isinstance(query, StorageBarQuery):
        return query
    return StorageBarQuery(
        instrument_key=(
            f"{query.instrument.exchange}:{query.instrument.market}:{query.instrument.symbol}:trade"
        ),
        timeframe=query.timeframe.canonical,
        from_time=query.start_ms,
        to_time=query.end_ms - 1,
        include_open_candle=False,
        source="storage",
    )


def _canonical_bar_from_storage_bar(bar: object, query: MDPBarQuery) -> Bar:
    time = int(getattr(bar, "time"))
    return Bar(
        instrument=InstrumentKey(
            exchange=query.instrument.exchange,
            market=query.instrument.market,
            symbol=query.instrument.symbol,
        ),
        timeframe=Timeframe(canonical=query.timeframe.canonical),
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
    duplicate_timestamps = tuple(
        sorted({bar.time for bar in bars if sum(1 for other in bars if other.time == bar.time) > 1})
    )
    ordered = all(bars[i].time < bars[i + 1].time for i in range(len(bars) - 1))
    status = "valid"
    if duplicate_timestamps:
        status = "duplicate"
    elif not ordered:
        status = "unordered"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        duplicate_timestamps=duplicate_timestamps,
        source_mix=(source,),
        status=status,
    )


__all__ = ["DataOrchestrator", "MarketDataProvider", "StrategyInstance"]
