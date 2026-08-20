"""Explicit multi-series MTF request normalization and confirmed-bar stamping."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from marketdata_provider.contracts import parse_timeframe
from openpine.runtime.isolated_run import (
    IsolatedRunError,
    _confirmed_htf_bars_from_provider_bars,
)


@dataclass(frozen=True, slots=True)
class MtfSeriesKey:
    symbol: str
    timeframe: str

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "timeframe": self.timeframe}


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def normalize_mtf_requests(requests: Iterable[Any] | None) -> tuple[MtfSeriesKey, ...]:
    """Return canonical, unique explicit MTF keys in operator order."""

    if requests is None:
        return ()
    normalized: list[MtfSeriesKey] = []
    seen: set[tuple[str, str]] = set()
    for item in requests:
        symbol_raw = _field(item, "symbol")
        timeframe_raw = _field(item, "timeframe")
        symbol = str(symbol_raw or "").strip().upper()
        timeframe_text = str(timeframe_raw or "").strip()
        if not symbol:
            raise ValueError("MTF series symbol is required")
        if not timeframe_text:
            raise ValueError("MTF series timeframe is required")
        try:
            timeframe = parse_timeframe(timeframe_text).canonical
        except (TypeError, ValueError) as exc:
            raise ValueError(f"MTF timeframe {timeframe_text!r} is invalid") from exc
        key = (symbol, timeframe)
        if key in seen:
            raise ValueError(f"duplicate MTF series: {symbol} {timeframe}")
        seen.add(key)
        normalized.append(MtfSeriesKey(symbol, timeframe))
    if len(normalized) > 16:
        raise ValueError("at most 16 MTF series may be requested")
    return tuple(normalized)


def parse_mtf_series_args(values: Iterable[str] | None) -> tuple[MtfSeriesKey, ...]:
    """Parse repeatable CLI values in explicit SYMBOL:TIMEFRAME form."""

    requests: list[dict[str, str]] = []
    for raw in values or ():
        text = str(raw).strip()
        if ":" not in text:
            raise ValueError("MTF series must use SYMBOL:TIMEFRAME")
        symbol, timeframe = text.rsplit(":", 1)
        requests.append({"symbol": symbol, "timeframe": timeframe})
    return normalize_mtf_requests(requests)


def admitted_mtf_requests(
    *,
    chart_symbol: str,
    htf_timeframe: str | None = None,
    mtf_series: Iterable[Any] | None = None,
) -> tuple[MtfSeriesKey, ...]:
    """Normalize plural requests or the legacy chart-symbol shorthand."""

    explicit = normalize_mtf_requests(mtf_series)
    legacy = str(htf_timeframe or "").strip()
    if legacy and explicit:
        raise ValueError("htf_timeframe and mtf_series cannot be combined")
    if legacy:
        return normalize_mtf_requests(
            ({"symbol": str(chart_symbol), "timeframe": legacy},)
        )
    return explicit


def confirmed_mtf_bars_for_requests(
    *,
    chart_bars: Sequence[Any],
    chart_symbol: str,
    chart_timeframe: str,
    requests: Iterable[Any] | None,
    load_bars: Callable[[str, str], Sequence[Any]],
) -> list[dict[str, object]]:
    """Fetch and stamp every explicit series, failing closed on any gap.

    The chart series is reused when explicitly requested with the same key.
    Other keys are loaded exactly once via the caller-owned provider boundary.
    """

    try:
        keys = normalize_mtf_requests(requests)
        canonical_chart = MtfSeriesKey(
            str(chart_symbol).strip().upper(),
            parse_timeframe(str(chart_timeframe)).canonical,
        )
    except ValueError as exc:
        raise IsolatedRunError(str(exc)) from exc

    stamped: list[dict[str, object]] = []
    for key in keys:
        try:
            raw_bars = (
                chart_bars
                if key == canonical_chart
                else load_bars(key.symbol, key.timeframe)
            )
        except Exception as exc:
            raise IsolatedRunError(
                f"MTF series {key.symbol} {key.timeframe} could not be loaded"
            ) from exc
        confirmed = _confirmed_htf_bars_from_provider_bars(
            raw_bars,
            symbol=key.symbol,
            timeframe=key.timeframe,
        )
        if not confirmed:
            raise IsolatedRunError(
                f"MTF series {key.symbol} {key.timeframe} has no confirmed bars"
            )
        stamped.extend(confirmed)
    return stamped


def mtf_requests_json(requests: Iterable[Any] | None) -> str:
    """Canonical compact JSON for durable strategy admission."""

    import json

    return json.dumps(
        [item.to_dict() for item in normalize_mtf_requests(requests)],
        separators=(",", ":"),
    )


def mtf_requests_from_json(raw: str | None) -> tuple[MtfSeriesKey, ...]:
    """Decode durable MTF admission fail-closed."""

    import json

    text = str(raw or "[]")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("stored MTF series JSON is invalid") from exc
    if not isinstance(payload, list):
        raise ValueError("stored MTF series must be an array")
    return normalize_mtf_requests(payload)
