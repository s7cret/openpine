"""DataOrchestrator: single boundary for bar reading/writing.

Section OP-DL-004 of OpenPine.
Section 7.5 + 33.1 of OpenPine TZ v3.

Rules:
- get_bars(query: BarQuery) -> list[Bar] is the ONLY read contract for bars.
- on_candle_closed(bar, instrument_key, timeframe) is the ONLY write boundary
  for confirmed closed candles from live feeds.
"""

from __future__ import annotations

import time
from typing import Optional, Protocol

from openpine.data.bar_query import BarQuery
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

    def get_bars(self, query: BarQuery) -> list["Bar"]:
        """Get bars for the given query."""
        ...


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

            # Build a query to check for existing data
            instrument_key = f"{req.exchange}:{req.market_type}:{req.symbol}:{req.price_type}"
            query = BarQuery(
                instrument_key=instrument_key,
                timeframe=req.timeframe,
                from_time=req.from_time,
                to_time=req.to_time,
            )

            gaps = self.detect_gaps(query)
            if gaps:
                gaps_remaining.extend(gaps)

        return EnsureDataResult(
            success=True,
            gaps_filled=0,
            gaps_remaining=gaps_remaining,
        )

    def get_bars(self, query: BarQuery) -> list["Bar"]:
        """Single read contract for bars.

        Tries storage first, then provider if storage has no data.

        Args:
            query: BarQuery specifying instrument, timeframe, time range

        Returns:
            List of Bar objects matching the query
        """
        # Try storage first
        if query.source in ("storage", "auto"):
            try:
                bars = self._candle_storage.read_candles(query)
                if bars:
                    return bars
            except Exception:
                pass

        # Try provider
        if query.source in ("provider", "auto") and self._provider is not None:
            try:
                return self._provider.get_bars(query)
            except Exception:
                pass

        return []

    def detect_gaps(self, query: BarQuery) -> list[DataGap]:
        """Detect gaps in the candle data.

        Args:
            query: BarQuery specifying instrument, timeframe, and time range

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
        bar: "Bar",
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
        from marketdata_provider.core.bar import Bar

        try:
            # Ensure instrument_key is set on bar if it has that attribute
            if hasattr(bar, "instrument_key"):
                bar.instrument_key = instrument_key  # type: ignore[attr-defined]
            if hasattr(bar, "timeframe"):
                bar.timeframe = timeframe  # type: ignore[attr-defined]

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

    def list_manifests(self, query: BarQuery) -> list[CandleManifest]:
        """List candle manifests for a query.

        Args:
            query: BarQuery specifying instrument and time range

        Returns:
            List of CandleManifest objects
        """
        return self._candle_storage.list_manifests(query)


__all__ = ["DataOrchestrator", "MarketDataProvider", "StrategyInstance"]
