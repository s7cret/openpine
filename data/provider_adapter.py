"""Adapter boundary for local marketdata-provider installations."""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import structlog

from openpine.contracts import Bar, BarQuery, InstrumentKey, Timeframe

log = structlog.get_logger(__name__)

DEFAULT_PROVIDER_ROOTS = (
    Path("[local-home]/marketdata_provider"),
    Path("[local-home]/marketdata_provider/src"),
)


@dataclass(frozen=True)
class LocalProviderInstallation:
    """Detected local marketdata-provider import boundary."""

    root: Path
    import_path: Path
    package_dir: Path


def _candidate_import_paths(root: Path) -> Iterable[Path]:
    yield root
    yield root / "src"


def detect_local_marketdata_provider(
    roots: Iterable[str | Path] = DEFAULT_PROVIDER_ROOTS,
) -> LocalProviderInstallation | None:
    """Find a local marketdata_provider package without importing it."""

    for raw_root in roots:
        root = Path(raw_root).expanduser()
        for import_path in _candidate_import_paths(root):
            package_dir = import_path / "marketdata_provider"
            if (package_dir / "__init__.py").is_file():
                return LocalProviderInstallation(
                    root=root,
                    import_path=import_path,
                    package_dir=package_dir,
                )
    return None


def ensure_provider_import_path(
    installation: LocalProviderInstallation,
) -> None:
    """Make a detected local provider importable."""

    import_path = str(installation.import_path)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)


def _attr_or_item(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"missing any of: {', '.join(names)}")


def normalize_provider_bar(
    provider_bar: Any,
    query: BarQuery,
) -> Bar:
    """Convert a marketdata-provider bar shape to OpenPine's Bar contract."""

    symbol = (
        str(_attr_or_item(provider_bar, "symbol", "exchange_symbol")).upper()
        if _has_any_field(provider_bar, ("symbol", "exchange_symbol"))
        else query.instrument_key.symbol
    )
    exchange = (
        str(_attr_or_item(provider_bar, "exchange")).upper()
        if _has_field(provider_bar, "exchange")
        else query.instrument_key.exchange
    )
    timeframe_value = (
        str(_attr_or_item(provider_bar, "timeframe"))
        if _has_field(provider_bar, "timeframe")
        else query.timeframe.value
    )

    instrument = InstrumentKey(
        symbol=symbol,
        exchange=exchange,
        market_type=query.instrument_key.market_type,
        price_type=query.instrument_key.price_type,
        base=query.instrument_key.base,
        quote=query.instrument_key.quote,
    )
    timeframe = Timeframe(value=timeframe_value)

    return Bar(
        instrument_key=instrument,
        timeframe=timeframe,
        timestamp=int(_attr_or_item(provider_bar, "time", "timestamp", "open_time", "open_time_ms")),
        open=float(_attr_or_item(provider_bar, "open")),
        high=float(_attr_or_item(provider_bar, "high")),
        low=float(_attr_or_item(provider_bar, "low")),
        close=float(_attr_or_item(provider_bar, "close")),
        volume=float(_attr_or_item(provider_bar, "volume")) if _has_field(provider_bar, "volume") else 0.0,
        closed=bool(_attr_or_item(provider_bar, "is_closed", "closed")) if _has_any_field(provider_bar, ("is_closed", "closed")) else True,
    )


def _is_synthetic_empty_provider_bar(provider_bar: Any) -> bool:
    if not _has_field(provider_bar, "volume"):
        return False
    try:
        volume = float(_attr_or_item(provider_bar, "volume"))
    except (TypeError, ValueError):
        return False
    return volume == 0.0


def _has_field(obj: Any, name: str) -> bool:
    return (isinstance(obj, dict) and name in obj) or hasattr(obj, name)


def _has_any_field(obj: Any, names: tuple[str, ...]) -> bool:
    return any(_has_field(obj, name) for name in names)


class LocalMarketDataProviderAdapter:
    """OpenPine historical-data boundary around marketdata-provider."""

    provider_name = "marketdata-provider"

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    @classmethod
    def from_local_installation(
        cls,
        installation: LocalProviderInstallation | None = None,
    ) -> "LocalMarketDataProviderAdapter | None":
        installation = installation or detect_local_marketdata_provider()
        if installation is None:
            return None
        ensure_provider_import_path(installation)
        module = importlib.import_module("marketdata_provider")
        provider_cls = getattr(module, "MarketDataProvider", None)
        if provider_cls is None:
            # Try submodule paths
            try:
                provider_module = importlib.import_module("marketdata_provider.provider")
                provider_cls = getattr(provider_module, "MarketDataProvider", None)
            except ModuleNotFoundError:
                pass
        if provider_cls is None:
            return None
        return cls(provider_cls())

    def get_bars(self, query: BarQuery) -> list[Bar]:
        """Delegate to the provider and normalize returned bars."""

        kwargs: dict[str, Any] = {}
        if query.limit is not None:
            kwargs["max_bars"] = query.limit

        try:
            raw_bars = self._call_provider(query, kwargs)
        except Exception as exc:
            log.warning("local_marketdata_provider.get_bars_failed", query=str(query), error=str(exc))
            raise

        return [
            normalize_provider_bar(raw_bar, query)
            for raw_bar in raw_bars or []
            if not _is_synthetic_empty_provider_bar(raw_bar)
        ]

    def _call_provider(self, query: BarQuery, kwargs: dict[str, Any]) -> Any:
        get_bars = self._provider.get_bars
        try:
            signature = inspect.signature(get_bars)
        except (TypeError, ValueError):
            signature = None

        if signature is not None:
            params = signature.parameters
            if "query" in params:
                return get_bars(query)
            if {"exchange", "market", "symbol", "timeframe"} <= set(params):
                return get_bars(
                    exchange=query.instrument_key.exchange.lower(),
                    market=query.instrument_key.market_type.lower(),
                    symbol=query.instrument_key.symbol,
                    timeframe=query.timeframe.value,
                    start=query.start_ms,
                    end=query.end_ms,
                    **kwargs,
                )

        return get_bars(
            query.instrument_key.symbol,
            query.timeframe.value,
            query.start_ms,
            query.end_ms,
            **kwargs,
        )


def create_local_marketdata_provider_adapter(
    roots: Iterable[str | Path] = DEFAULT_PROVIDER_ROOTS,
) -> LocalMarketDataProviderAdapter | None:
    """Create a local provider adapter, or None when no local package exists."""

    installation = detect_local_marketdata_provider(roots)
    if installation is None:
        return None
    return LocalMarketDataProviderAdapter.from_local_installation(installation)


__all__ = [
    "DEFAULT_PROVIDER_ROOTS",
    "LocalMarketDataProviderAdapter",
    "LocalProviderInstallation",
    "create_local_marketdata_provider_adapter",
    "detect_local_marketdata_provider",
    "ensure_provider_import_path",
    "normalize_provider_bar",
]
