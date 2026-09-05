"""Single production data orchestrator for canonical marketdata contracts."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Mapping
from copy import deepcopy
from concurrent.futures import Future
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Protocol, cast

from openpine_contracts import validate_payload, verify_content_hash

from marketdata_provider import create_candle_store
from marketdata_provider.config import ArtifactIdentityConfig, MarketDataConfig, StorageConfig
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CandleStore,
    CoverageReport,
    StoreResult,
)
from marketdata_provider.errors import MDValidationError

from openpine.config import DEFAULT_CONFIG
from openpine.data.models import CandleCommitResult, DataGap
from openpine.data.persistent_cache import (
    cache_enabled_by_env,
    default_cache_dir,
    load_bar_series,
    save_bar_series,
)
from openpine.data.row_helpers import duplicate_timestamps


class MarketDataProvider(Protocol):
    """Provider boundary used by OpenPine data orchestration."""

    persists_fetches: bool

    def fetch_bars(self, query: BarQuery) -> object: ...


@dataclass(frozen=True)
class CanonicalBarSeries:
    """Compatibility bars paired with their exact admitted RC.4 envelopes."""

    query: BarQuery
    bars: tuple[Bar, ...]
    coverage: CoverageReport
    canonical_bars: tuple[dict[str, object], ...]
    snapshot: dict[str, object]


LoadedBarSeries = BarSeries | CanonicalBarSeries


@dataclass
class _ProviderFlight:
    """One in-flight provider request for a physical market-data series."""

    query: BarQuery
    future: Future[LoadedBarSeries]


class DataCoverageError(RuntimeError):
    """Base class for fail-closed data coverage errors."""


class IncompleteCoverageError(DataCoverageError):
    """Raised when a query cannot be satisfied under gap_policy='fail'."""


class ProviderUnavailableError(DataCoverageError):
    """Raised when provider data is required but no provider is configured."""


class StorageUnavailableError(DataCoverageError):
    """Raised when storage access or persistence fails."""


_COMMIT = re.compile(r"^(?!0{40}$)[0-9a-f]{40}$")
_STACK = re.compile(r"^sha256:(?!0{64}$)[0-9a-f]{64}$")


def admitted_artifact_identity() -> ArtifactIdentityConfig:
    from openpine.runtime.admitted_manifest import load_admitted_manifest

    try:
        payload = load_admitted_manifest()
    except Exception as exc:
        raise StorageUnavailableError("admitted candidate manifest is required") from exc
    components = payload.get("components")
    if not isinstance(components, Mapping):
        raise StorageUnavailableError("admitted candidate components are required")
    row = components.get("marketdata-provider")
    if not isinstance(row, Mapping):
        raise StorageUnavailableError("admitted marketdata-provider identity is required")
    sha = row.get("sha")
    stack_id = payload.get("manifest_hash")
    if not isinstance(sha, str) or _COMMIT.fullmatch(sha) is None:
        raise StorageUnavailableError(
            "producer_commit must be an exact nonzero 40-hex commit"
        )
    if not isinstance(stack_id, str) or _STACK.fullmatch(stack_id) is None:
        raise StorageUnavailableError(
            "stack_id must be an exact nonzero sha256 manifest hash"
        )
    return ArtifactIdentityConfig(producer_commit=sha, stack_id=stack_id)


def _default_candle_store() -> CandleStore:
    cache_root = DEFAULT_CONFIG.data_cache_root or (DEFAULT_CONFIG.data_dir / "cache")
    return create_candle_store(
        MarketDataConfig(
            storage=StorageConfig(cache_dir=cache_root / "marketdata"),
            artifact_identity=admitted_artifact_identity(),
        )
    )


def _canonical_series(
    payload: object, query: BarQuery, *, source: str
) -> BarSeries | CanonicalBarSeries:
    if isinstance(payload, BarSeries):
        return payload
    if not isinstance(payload, Mapping):
        raise StorageUnavailableError("marketdata boundary returned an invalid snapshot")
    schema_validate = source != "storage"
    snapshot = deepcopy(dict(payload)) if schema_validate else dict(payload)
    envelope = snapshot.get("snapshot_envelope")
    raw_bars = snapshot.get("bars")
    if not isinstance(envelope, Mapping) or not isinstance(raw_bars, list):
        raise StorageUnavailableError("marketdata snapshot envelope is required")
    if schema_validate:
        validate_payload("openpine.marketdata.v2", envelope)
        if not verify_content_hash(envelope, schema_id="openpine.marketdata.v2"):
            raise StorageUnavailableError("marketdata snapshot content hash is invalid")
    body = envelope.get("body")
    contract_query = body.get("query") if isinstance(body, Mapping) else None
    expected = {
        "instrument_id": query.instrument.serialize(),
        "timeframe": query.timeframe.canonical,
        "start_utc_ms": query.start_ms,
        "end_utc_ms": query.end_ms,
    }
    if not isinstance(contract_query, Mapping) or any(
        contract_query.get(name) != value for name, value in expected.items()
    ):
        raise StorageUnavailableError("marketdata snapshot query identity mismatch")
    canonical: list[dict[str, object]] = []
    bars: list[Bar] = []
    for index, raw_bar in enumerate(raw_bars):
        if not isinstance(raw_bar, Mapping):
            raise StorageUnavailableError(f"marketdata bar {index} is invalid")
        bar_envelope = deepcopy(dict(raw_bar)) if schema_validate else dict(raw_bar)
        if schema_validate:
            validate_payload("openpine.marketdata.bar.v2", bar_envelope)
            if not verify_content_hash(
                bar_envelope, schema_id="openpine.marketdata.bar.v2"
            ):
                raise StorageUnavailableError(
                    f"marketdata bar {index} content hash is invalid"
                )
        if (
            bar_envelope.get("instrument_id") != expected["instrument_id"]
            or bar_envelope.get("timeframe") != expected["timeframe"]
        ):
            raise StorageUnavailableError(
                f"marketdata bar {index} identity does not match query"
            )
        finality = getattr(bar_envelope.get("finality"), "value", None) or str(
            bar_envelope.get("finality")
        )
        bars.append(
            Bar(
                instrument=query.instrument,
                timeframe=query.timeframe,
                time=int(bar_envelope["open_time_utc_ms"]),
                time_close=int(bar_envelope["close_time_utc_ms"]),
                open=float(str(bar_envelope["open"])),
                high=float(str(bar_envelope["high"])),
                low=float(str(bar_envelope["low"])),
                close=float(str(bar_envelope["close"])),
                volume=float(str(bar_envelope["volume"])),
                closed=finality == "FINAL",
            )
        )
        canonical.append(bar_envelope)
    bar_tuple = tuple(bars)
    return CanonicalBarSeries(
        query=query,
        bars=bar_tuple,
        coverage=_coverage_for(query, bar_tuple, source),
        canonical_bars=tuple(canonical),
        snapshot=snapshot,
    )


class DataOrchestrator:
    """Read, validate, and persist canonical marketdata bar series."""

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        store: CandleStore | None = None,
        validator: BarSeriesValidator | None = None,
        *,
        candle_store: CandleStore | None = None,
        cache_dir: Path | None = None,
        cache_enabled: bool | None = None,
    ) -> None:
        custom_store = store is not None or candle_store is not None
        self._provider = provider
        self._store = store or candle_store or _default_candle_store()
        self._validator = validator or BarSeriesValidator()
        self._cache_dir = cache_dir or default_cache_dir()
        self._cache_enabled = (
            (not custom_store and cache_enabled_by_env())
            if cache_enabled is None
            else cache_enabled
        )
        self._provider_flights_guard = threading.Lock()
        self._provider_flights: dict[tuple[str, str, str, str], _ProviderFlight] = {}

    def set_provider(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    @property
    def provider_persists_fetches(self) -> bool:
        """Whether the configured provider is the persistence owner for fetches."""

        return bool(getattr(self._provider, "persists_fetches", False))

    def load_bars(
        self, query: BarQuery, progress_callback: Callable[..., None] | None = None
    ) -> LoadedBarSeries:
        """Load bars according to query.source: storage, provider, or auto."""

        if query.source == "storage":
            return self._load_storage(
                query, require_complete=query.gap_policy == "fail"
            )
        cached = self._load_cache(query, progress_callback=progress_callback)
        if cached is not None:
            return cached
        if query.source == "provider":
            series = self._load_provider(query, progress_callback=progress_callback)
            series = (
                self._require_complete(series, "provider")
                if query.gap_policy == "fail"
                else series
            )
            self._save_cache(series)
            return series
        if query.source != "auto":
            raise ValueError(f"unsupported data source: {query.source}")

        storage_series = self._load_storage(query, require_complete=False)
        if storage_series.coverage.is_complete or (
            storage_series.bars and query.gap_policy == "allow_with_metadata"
        ):
            self._save_cache(storage_series)
            return storage_series

        provider_series = self._load_missing_from_provider(
            query,
            storage_series.coverage.missing_intervals,
            progress_callback=progress_callback,
        )
        if provider_series.bars and not self.provider_persists_fetches:
            self._write_provider_series(provider_series)
        if provider_series.bars:
            stored = self._load_storage(query, require_complete=False)
            if isinstance(stored, CanonicalBarSeries) and stored.bars:
                merged = stored
            else:
                merged = _merge_series(query, storage_series, provider_series)
        else:
            merged = storage_series
        merged = (
            self._require_complete(merged, "auto")
            if query.gap_policy == "fail"
            else merged
        )
        self._save_cache(merged)
        return merged

    def get_bars(self, query: BarQuery) -> list[Bar]:
        """Return loaded bars as a list for callers that need a sequence."""

        return list(self.load_bars(query).bars)

    def latest_bar_time(self, query: BarQuery) -> int | None:
        """Return the latest stored bar timestamp without materializing bars when supported."""

        latest = getattr(self._store, "latest_bar_time", None)
        if callable(latest):
            return latest(query)
        coverage = self._store.coverage(query)
        return coverage.delivered_end_ms

    @staticmethod
    def coverage_for_series(
        query: BarQuery, bars: tuple[Bar, ...], source: str
    ) -> CoverageReport:
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
        series = BarSeries(
            query=query, bars=(bar,), coverage=_coverage_for(query, (bar,), source)
        )
        result = self._write_series(series)
        return CandleCommitResult(
            success=True, manifest_id=getattr(result, "manifest_id", None)
        )

    def detect_gaps(self, query: BarQuery) -> list[DataGap]:
        """Return missing intervals from the configured candle store coverage."""

        if hasattr(self._store, "detect_gaps"):
            return list(self._store.detect_gaps(query))  # type: ignore[attr-defined]
        coverage = self._store.coverage(query)
        return [
            _data_gap_from_interval(query, start, end)
            for start, end in coverage.missing_intervals
        ]

    def _write_provider_series(self, series: LoadedBarSeries) -> StoreResult:
        self._validator.validate(series, allow_gaps=True)
        return self._write_series(series)

    def _write_series(self, series: LoadedBarSeries) -> StoreResult:
        try:
            payload = series.snapshot if isinstance(series, CanonicalBarSeries) else series
            result = self._store.write(payload)
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc
        if not result.success:
            raise StorageUnavailableError(result.error or "failed to persist bars")
        return result

    def validate_coverage(self, series: BarSeries) -> CoverageReport:
        return self._validator.validate(series)

    def _load_storage(self, query: BarQuery, *, require_complete: bool) -> LoadedBarSeries:
        try:
            try:
                series_reader = getattr(self._store, "read_series", None)
                payload = (
                    cast(Callable[[BarQuery], object], series_reader)(query)
                    if query.gap_policy == "allow_with_metadata" and callable(series_reader)
                    else self._store.read(query)
                )
            except MDValidationError as exc:
                if str(exc) != "provider_revision is unavailable for an empty snapshot":
                    raise
                series: LoadedBarSeries = BarSeries(
                    query=query,
                    bars=(),
                    coverage=_coverage_for(query, (), "storage"),
                )
            else:
                series = _canonical_series(payload, query, source="storage")
        except Exception as exc:
            raise StorageUnavailableError(str(exc)) from exc
        self._validator.validate(series, allow_gaps=True)
        return self._require_complete(series, "storage") if require_complete else series

    def _load_cache(
        self, query: BarQuery, progress_callback: Callable[..., None] | None = None
    ) -> BarSeries | None:
        if not self._cache_enabled:
            return None
        series = load_bar_series(
            self._cache_dir, query, progress_callback=progress_callback
        )
        if series is None:
            return None
        if not isinstance(series, CanonicalBarSeries) and not getattr(
            series, "canonical_bars", None
        ):
            return None
        self._validator.validate(series, allow_gaps=query.gap_policy != "fail")
        return (
            self._require_complete(series, "persistent_cache")
            if query.gap_policy == "fail"
            else series
        )

    def _save_cache(self, series: LoadedBarSeries) -> None:
        if not self._cache_enabled or not isinstance(series, CanonicalBarSeries):
            return
        try:
            save_bar_series(self._cache_dir, series)
        except Exception:
            return

    def _load_provider(
        self, query: BarQuery, progress_callback: Callable[..., None] | None = None
    ) -> LoadedBarSeries:
        if self._provider is None:
            raise ProviderUnavailableError("market data provider is not configured")
        series_key = _provider_series_key(query)
        while True:
            with self._provider_flights_guard:
                flight = self._provider_flights.get(series_key)
                if flight is None:
                    flight = _ProviderFlight(query=query, future=Future())
                    self._provider_flights[series_key] = flight
                    owns_flight = True
                else:
                    owns_flight = False
            if owns_flight:
                break
            if flight.query == query:
                return flight.future.result()
            try:
                flight.future.result()
            except Exception:  # noqa: S110
                # A different range still gets its own attempt after the
                # active series flight finishes, even if that request failed.
                pass

        try:
            fetch_bars = self._provider.fetch_bars
            if progress_callback is not None and _accepts_progress_callback(fetch_bars):
                raw_series = fetch_bars(query, progress_callback=progress_callback)  # type: ignore[call-arg]
            else:
                raw_series = fetch_bars(query)
            series = _canonical_series(raw_series, query, source="provider")
            self._validator.validate(series, allow_gaps=query.gap_policy != "fail")
        except BaseException as exc:
            with self._provider_flights_guard:
                if self._provider_flights.get(series_key) is flight:
                    self._provider_flights.pop(series_key, None)
            flight.future.set_exception(exc)
            raise
        with self._provider_flights_guard:
            if self._provider_flights.get(series_key) is flight:
                self._provider_flights.pop(series_key, None)
        flight.future.set_result(series)
        return series

    def _load_missing_from_provider(
        self,
        query: BarQuery,
        intervals: tuple[tuple[int, int], ...],
        progress_callback: Callable[..., None] | None = None,
    ) -> BarSeries:
        fetched: list[Bar] = []
        for start_ms, end_ms in _coalesce_intervals(intervals):
            missing_query = replace(
                query, start_ms=start_ms, end_ms=end_ms, source="provider"
            )
            missing_series = self._load_provider(
                missing_query, progress_callback=progress_callback
            )
            self._validator.validate(
                missing_series, allow_gaps=query.gap_policy != "fail"
            )
            if query.gap_policy == "fail":
                self._require_complete(missing_series, "provider")
            fetched.extend(missing_series.bars)
        bars = tuple(sorted(fetched, key=lambda bar: bar.time))
        return BarSeries(
            query=query, bars=bars, coverage=_coverage_for(query, bars, "provider")
        )

    @staticmethod
    def _require_complete(series: LoadedBarSeries, source: str) -> LoadedBarSeries:
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

    def validate(
        self, series: LoadedBarSeries, *, allow_gaps: bool | None = None
    ) -> CoverageReport:
        coverage = _coverage_for(
            series.query, series.bars, _source_name(series.coverage)
        )
        if coverage.duplicate_timestamps:
            raise IncompleteCoverageError(
                f"duplicate bar timestamps: {coverage.duplicate_timestamps}"
            )
        if coverage.status == "unordered":
            raise IncompleteCoverageError("bar series is not ordered by timestamp")
        if any(not bar.closed for bar in series.bars):
            raise IncompleteCoverageError(
                "open candle is not allowed in historical bar series"
            )
        gaps_allowed = (
            series.query.gap_policy == "allow_with_metadata"
            if allow_gaps is None
            else allow_gaps
        )
        if coverage.missing_intervals and not gaps_allowed:
            raise IncompleteCoverageError(
                f"missing bar intervals: {coverage.missing_intervals}"
            )
        return coverage


def _source_name(coverage: CoverageReport) -> str:
    return coverage.source_mix[0] if coverage.source_mix else "unknown"


def _provider_series_key(query: BarQuery) -> tuple[str, str, str, str]:
    return (
        query.instrument.exchange,
        query.instrument.market,
        query.instrument.symbol,
        query.timeframe.canonical,
    )


def _accepts_progress_callback(fetch_bars: Callable[..., BarSeries]) -> bool:
    try:
        signature = inspect.signature(fetch_bars)
    except (TypeError, ValueError):
        return False
    return "progress_callback" in signature.parameters


def _coverage_for(
    query: BarQuery, bars: tuple[Bar, ...], source: str
) -> CoverageReport:
    if not bars:
        return CoverageReport(
            query.start_ms,
            query.end_ms,
            None,
            None,
            ((query.start_ms, query.end_ms),),
            (),
            (source,),
            "empty",
        )

    duplicate_ts = duplicate_timestamps(bars)
    ordered = all(
        bars[index].time < bars[index + 1].time for index in range(len(bars) - 1)
    )
    missing_intervals = (
        _missing_intervals(query, bars) if ordered and not duplicate_ts else ()
    )
    status = (
        "duplicate"
        if duplicate_ts
        else "unordered" if not ordered else "gap" if missing_intervals else "valid"
    )
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=max(bar.time_close for bar in bars),
        missing_intervals=missing_intervals,
        duplicate_timestamps=duplicate_ts,
        source_mix=(source,),
        status=status,
    )


def _coalesce_intervals(
    intervals: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
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


def _missing_intervals(
    query: BarQuery, bars: tuple[Bar, ...]
) -> tuple[tuple[int, int], ...]:
    duration_ms = query.timeframe.duration_ms
    if duration_ms is None:
        return ()
    delivered = {bar.time for bar in bars}
    return tuple(
        (start_ms, min(start_ms + duration_ms, query.end_ms))
        for start_ms in range(query.start_ms, query.end_ms, duration_ms)
        if start_ms not in delivered
    )


def _merge_series(
    query: BarQuery, storage_series: BarSeries, provider_series: BarSeries
) -> BarSeries:
    by_time: dict[int, Bar] = {bar.time: bar for bar in storage_series.bars}
    for bar in provider_series.bars:
        by_time[bar.time] = bar
    bars = tuple(sorted(by_time.values(), key=lambda bar: bar.time))
    return BarSeries(
        query=query, bars=bars, coverage=_coverage_for(query, bars, "auto")
    )


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
