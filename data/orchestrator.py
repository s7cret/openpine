"""Single production data orchestrator for canonical marketdata contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from marketdata_provider import create_candle_store
from marketdata_provider.config import MarketDataConfig, StorageConfig
from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CandleStore, CoverageReport, StoreResult
from openpine.data.models import CandleCommitResult, DataGap


class MarketDataProvider(Protocol):
    """Provider boundary used by OpenPine data orchestration."""

    def fetch_bars(self, query: BarQuery) -> BarSeries: ...


class DataCoverageError(RuntimeError):
    """Base class for fail-closed data coverage errors."""


class IncompleteCoverageError(DataCoverageError):
    """Raised when a query cannot be satisfied under gap_policy='fail'."""


class ProviderUnavailableError(DataCoverageError):
    """Raised when provider data is required but no provider is configured."""


class StorageUnavailableError(DataCoverageError):
    """Raised when storage access or persistence fails."""


def _default_candle_store() -> CandleStore:
    return create_candle_store(MarketDataConfig(storage=StorageConfig()))


class DataOrchestrator:
    """Read, validate, and persist canonical marketdata bar series."""

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        store: CandleStore | None = None,
        validator: BarSeriesValidator | None = None,
        *,
        candle_store: CandleStore | None = None,
    ) -> None:
        self._provider = provider
        self._store = store or candle_store or _default_candle_store()
        self._validator = validator or BarSeriesValidator()

    def set_provider(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def load_bars(self, query: BarQuery) -> BarSeries:
        """Load bars according to query.source: storage, provider, or auto."""

        if query.source == "storage":
            return self._load_storage(query, require_complete=query.gap_policy == "fail")
        if query.source == "provider":
            series = self._load_provider(query)
            return self._require_complete(series, "provider") if query.gap_policy == "fail" else series
        if query.source != "auto":
            raise ValueError(f"unsupported data source: {query.source}")

        storage_series = self._load_storage(query, require_complete=False)
        if storage_series.coverage.is_complete:
            return storage_series

        provider_series = self._load_missing_from_provider(query, storage_series.coverage.missing_intervals)
        if provider_series.bars:
            self._write_provider_series(provider_series)
        merged = _merge_series(query, storage_series, provider_series)
        return self._require_complete(merged, "auto") if query.gap_policy == "fail" else merged

    def get_bars(self, query: BarQuery) -> list[Bar]:
        """Return loaded bars as a list for callers that need a sequence."""

        return list(self.load_bars(query).bars)

    @staticmethod
    def coverage_for_series(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
        """Build canonical coverage for an already-normalized bar tuple."""

        return _coverage_for(query, bars, source)

    def store_bars(self, series: BarSeries) -> StoreResult:
        self._validator.validate(series)
        return self._write_series(series)

    def on_candle_closed(
        self,
        bar: Bar,
        instrument_key: str,
        timeframe: str,
        source: str = "live",
    ) -> CandleCommitResult:
        """Durable write boundary for a confirmed closed live candle."""

        query = BarQuery(
            instrument=bar.instrument,
            timeframe=bar.timeframe,
            start_ms=bar.time,
            end_ms=bar.time_close,
            source="storage",
            gap_policy="fail",
            error_policy="raise",
        )
        series = BarSeries(query=query, bars=(bar,), coverage=_coverage_for(query, (bar,), source))
        result = self._write_series(series)
        return CandleCommitResult(success=True, manifest_id=getattr(result, "manifest_id", None))

    def detect_gaps(self, query: BarQuery) -> list[DataGap]:
        """Return missing intervals from the configured candle store coverage."""

        if hasattr(self._store, "detect_gaps"):
            return list(self._store.detect_gaps(query))  # type: ignore[attr-defined]
        coverage = self._store.coverage(query)
        return [_data_gap_from_interval(query, start, end) for start, end in coverage.missing_intervals]

    def _write_provider_series(self, series: BarSeries) -> StoreResult:
        self._validator.validate(series, allow_gaps=True)
        return self._write_series(series)

    def _write_series(self, series: BarSeries) -> StoreResult:
        try:
            result = self._store.write(series)
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc
        if not result.success:
            raise StorageUnavailableError(result.error or "failed to persist bars")
        return result

    def validate_coverage(self, series: BarSeries) -> CoverageReport:
        return self._validator.validate(series)

    def _load_storage(self, query: BarQuery, *, require_complete: bool) -> BarSeries:
        try:
            series = self._store.read(query)
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc
        self._validator.validate(series, allow_gaps=True)
        return self._require_complete(series, "storage") if require_complete else series

    def _load_provider(self, query: BarQuery) -> BarSeries:
        if self._provider is None:
            raise ProviderUnavailableError("market data provider is not configured")
        return self._provider.fetch_bars(query)

    def _load_missing_from_provider(self, query: BarQuery, intervals: tuple[tuple[int, int], ...]) -> BarSeries:
        fetched: list[Bar] = []
        for start_ms, end_ms in _coalesce_intervals(intervals):
            missing_query = replace(query, start_ms=start_ms, end_ms=end_ms, source="provider")
            missing_series = self._load_provider(missing_query)
            self._validator.validate(missing_series, allow_gaps=query.gap_policy != "fail")
            if query.gap_policy == "fail":
                self._require_complete(missing_series, "provider")
            fetched.extend(missing_series.bars)
        bars = tuple(sorted(fetched, key=lambda bar: bar.time))
        return BarSeries(query=query, bars=bars, coverage=_coverage_for(query, bars, "provider"))

    @staticmethod
    def _require_complete(series: BarSeries, source: str) -> BarSeries:
        if series.coverage.is_complete:
            return series
        raise IncompleteCoverageError(
            f"{source} coverage incomplete for "
            f"{series.query.instrument.exchange}/{series.query.instrument.market}/"
            f"{series.query.instrument.symbol} {series.query.timeframe.canonical}: "
            f"{series.coverage.missing_intervals or series.coverage.status}"
        )


class BarSeriesValidator:
    """Validate canonical bar ordering and query coverage metadata."""

    def validate(self, series: BarSeries, *, allow_gaps: bool | None = None) -> CoverageReport:
        coverage = _coverage_for(series.query, series.bars, _source_name(series.coverage))
        if coverage.duplicate_timestamps:
            raise IncompleteCoverageError(f"duplicate bar timestamps: {coverage.duplicate_timestamps}")
        if coverage.status == "unordered":
            raise IncompleteCoverageError("bar series is not ordered by timestamp")
        gaps_allowed = series.query.gap_policy == "allow_with_metadata" if allow_gaps is None else allow_gaps
        if coverage.missing_intervals and not gaps_allowed:
            raise IncompleteCoverageError(f"missing bar intervals: {coverage.missing_intervals}")
        return coverage


def _source_name(coverage: CoverageReport) -> str:
    return coverage.source_mix[0] if coverage.source_mix else "unknown"


def _coverage_for(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    if not bars:
        return CoverageReport(query.start_ms, query.end_ms, None, None, ((query.start_ms, query.end_ms),), (), (source,), "empty")

    duplicate_timestamps = _duplicate_timestamps(bars)
    ordered = all(bars[index].time < bars[index + 1].time for index in range(len(bars) - 1))
    missing_intervals = _missing_intervals(query, bars) if ordered and not duplicate_timestamps else ()
    status = "duplicate" if duplicate_timestamps else "unordered" if not ordered else "gap" if missing_intervals else "valid"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=max(bar.time_close for bar in bars),
        missing_intervals=missing_intervals,
        duplicate_timestamps=duplicate_timestamps,
        source_mix=(source,),
        status=status,
    )


def _duplicate_timestamps(bars: tuple[Bar, ...]) -> tuple[int, ...]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for bar in bars:
        if bar.time in seen:
            duplicates.add(bar.time)
        seen.add(bar.time)
    return tuple(sorted(duplicates))


def _coalesce_intervals(intervals: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    if not intervals:
        return ()
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = []
    current_start, current_end = ordered[0]
    for start_ms, end_ms in ordered[1:]:
        if start_ms <= current_end:
            current_end = max(current_end, end_ms)
            continue
        merged.append((current_start, current_end))
        current_start, current_end = start_ms, end_ms
    merged.append((current_start, current_end))
    return tuple(merged)


def _missing_intervals(query: BarQuery, bars: tuple[Bar, ...]) -> tuple[tuple[int, int], ...]:
    duration_ms = query.timeframe.duration_ms
    if duration_ms is None:
        return ()
    delivered = {bar.time for bar in bars}
    return tuple(
        (start_ms, min(start_ms + duration_ms, query.end_ms))
        for start_ms in range(query.start_ms, query.end_ms, duration_ms)
        if start_ms not in delivered
    )


def _merge_series(query: BarQuery, storage_series: BarSeries, provider_series: BarSeries) -> BarSeries:
    by_time: dict[int, Bar] = {bar.time: bar for bar in storage_series.bars}
    for bar in provider_series.bars:
        by_time[bar.time] = bar
    bars = tuple(sorted(by_time.values(), key=lambda bar: bar.time))
    return BarSeries(query=query, bars=bars, coverage=_coverage_for(query, bars, "auto"))


def _data_gap_from_interval(query: BarQuery, start_ms: int, end_ms: int) -> DataGap:
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


__all__ = [
    "BarSeriesValidator",
    "DataCoverageError",
    "DataOrchestrator",
    "IncompleteCoverageError",
    "ProviderUnavailableError",
    "StorageUnavailableError",
]
