"""OpenPine boundary for non-OHLCV footprint data."""

from __future__ import annotations

from typing import Protocol

from marketdata_provider.contracts import FootprintQuery, FootprintSeries, StoreResult
from marketdata_provider.store.footprint_store import FootprintStore
from openpine.data.orchestrator import IncompleteCoverageError, StorageUnavailableError


class FootprintProvider(Protocol):
    def fetch_footprint(self, query: FootprintQuery) -> FootprintSeries: ...


class FootprintOrchestrator:
    """Load and persist footprints without routing through candle storage."""

    def __init__(
        self,
        provider: FootprintProvider | None = None,
        store: FootprintStore | None = None,
    ) -> None:
        self._provider = provider
        self._store = store

    def load_footprints(self, query: FootprintQuery) -> FootprintSeries:
        if self._provider is not None and query.source in {"auto", "provider"}:
            series = self._provider.fetch_footprint(query)
            if query.gap_policy == "fail" and not series.coverage.is_complete:
                raise IncompleteCoverageError(
                    f"footprint coverage incomplete: {series.coverage.missing_intervals}"
                )
            if self._store is not None and series.bars:
                self.store_footprints(series)
            return series
        if self._store is None:
            raise StorageUnavailableError("footprint store is not configured")
        series = self._store.read(query)
        if query.gap_policy == "fail" and not series.coverage.is_complete:
            raise IncompleteCoverageError(
                f"footprint storage coverage incomplete: {series.coverage.missing_intervals}"
            )
        return series

    def store_footprints(self, series: FootprintSeries) -> StoreResult:
        if self._store is None:
            raise StorageUnavailableError("footprint store is not configured")
        result = self._store.write(series)
        if not result.success:
            raise StorageUnavailableError(
                result.error or "failed to persist footprints"
            )
        return result
