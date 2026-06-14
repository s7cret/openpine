"""PeriodicBarFetcher — auto-refresh bars for active strategies.

Runs every minute (configurable) and fetches latest bars for all
enabled strategies, storing them via DataOrchestrator.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
import time
from typing import Any

from openpine._compat import structlog

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    InstrumentKey,
    parse_timeframe,
)
from openpine.data.orchestrator import DataOrchestrator, StorageUnavailableError
from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance

log = structlog.get_logger(__name__)


@dataclass
class RefreshConfig:
    """Configuration for periodic bar refresh."""

    interval_seconds: float = 60.0
    lookback_bars: int = 2  # fetch last N bars to catch any gaps
    source_timeframe: str = "1m"
    enabled: bool = True


@dataclass(frozen=True)
class RawMarketKey:
    """Unique raw market data key shared by enabled strategies."""

    exchange: str
    market_type: str
    symbol: str
    price_type: str

    @classmethod
    def from_strategy(cls, strategy: StrategyInstance) -> "RawMarketKey":
        return cls(
            exchange=strategy.exchange.lower(),
            market_type=strategy.market_type.lower(),
            symbol=strategy.symbol.upper(),
            price_type=strategy.price_type.lower(),
        )

    @property
    def instrument_key(self) -> str:
        return f"{self.exchange}:{self.market_type}:{self.symbol}:{self.price_type}"


class PeriodicBarFetcher:
    """Background thread that refreshes bars for active strategies.

    Usage:
        fetcher = PeriodicBarFetcher()
        fetcher.start()  # starts background thread
        ...
        fetcher.stop()   # stops gracefully
    """

    def __init__(
        self,
        config: RefreshConfig | None = None,
        registry: SQLiteStrategyRegistry | None = None,
        orchestrator: DataOrchestrator | None = None,
    ) -> None:
        self.config = config or RefreshConfig()
        self.registry = registry or SQLiteStrategyRegistry()
        self.orchestrator = orchestrator or DataOrchestrator(
            provider=create_local_marketdata_provider_adapter()
        )

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self.last_fetch_at: int | None = None  # ms timestamp of last successful fetch
        self.last_fetch_instruments: int = 0

    def start(self) -> None:
        """Start the background refresh thread."""
        if self._running:
            log.warning("periodic_fetcher.already_running")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info(
            "periodic_fetcher.started",
            interval_seconds=self.config.interval_seconds,
            lookback_bars=self.config.lookback_bars,
        )

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the background thread gracefully."""
        if not self._running:
            return

        log.info("periodic_fetcher.stopping")
        self._stop_event.set()
        self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                log.warning("periodic_fetcher.stop_timeout")

        log.info("periodic_fetcher.stopped")

    def _run_loop(self) -> None:
        """Main loop: sleep then refresh."""
        while self._running and not self._stop_event.is_set():
            try:
                self._refresh_all_active()
            except Exception as exc:
                log.error("periodic_fetcher.refresh_failed", error=str(exc))

            # Sleep in small increments to respond to stop quickly
            slept = 0.0
            while (
                slept < self.config.interval_seconds
                and self._running
                and not self._stop_event.is_set()
            ):
                time.sleep(1.0)
                slept += 1.0

    def _refresh_all_active(self) -> None:
        """Fetch latest bars for all enabled strategies."""
        strategies = self.registry.list_strategies()
        active = [s for s in strategies if s.enabled]

        if not active:
            log.debug("periodic_fetcher.no_active_strategies")
            return

        groups = _group_strategies_by_market(active)
        now_ms = int(time.time() * 1000)

        log.info(
            "periodic_fetcher.refreshing",
            strategies=len(active),
            market_keys=len(groups),
            source_timeframe=self.config.source_timeframe,
        )

        for key, group in groups.items():
            try:
                self._refresh_market_key(key, group, now_ms=now_ms)
            except Exception as exc:
                log.error(
                    "periodic_fetcher.stream_refresh_failed",
                    market_key=str(key),
                    strategies=len(group),
                    error=str(exc),
                )

        self.last_fetch_at = now_ms
        self.last_fetch_instruments = len(groups)

    def _refresh_strategy(self, strategy: StrategyInstance) -> None:
        """Fetch latest bars for a single strategy."""
        self._refresh_market_key(
            RawMarketKey.from_strategy(strategy),
            [strategy],
            now_ms=int(time.time() * 1000),
        )

    def _refresh_market_key(
        self,
        key: RawMarketKey,
        strategies: list[StrategyInstance],
        *,
        now_ms: int,
    ) -> None:
        """Fetch source-timeframe bars once for a shared raw market key."""
        timeframe = parse_timeframe(self.config.source_timeframe)
        if timeframe.duration_ms is None:
            raise ValueError(
                f"cannot periodically refresh variable-duration timeframe: {self.config.source_timeframe}"
            )
        tf_ms = timeframe.duration_ms
        target_timeframes = sorted(
            {parse_timeframe(strategy.timeframe).canonical for strategy in strategies}
        )
        max_ratio = max(
            1,
            *(
                (target.duration_ms or tf_ms) // tf_ms
                for target in (parse_timeframe(value) for value in target_timeframes)
                if target.duration_ms is not None
            ),
        )
        lookback_ms = tf_ms * max(
            self.config.lookback_bars, max_ratio + self.config.lookback_bars
        )
        end_ms = now_ms - (now_ms % tf_ms)
        recent_start_ms = end_ms - lookback_ms
        last_stored_ms = self._latest_stored_bar_time(key, timeframe, end_ms)
        if last_stored_ms is not None:
            start_ms = max(0, last_stored_ms + tf_ms)
            if start_ms >= end_ms:
                log.debug(
                    "periodic_fetcher.no_new_bars",
                    market_key=str(key),
                    source_timeframe=timeframe.canonical,
                    strategies=len(strategies),
                )
                return
        else:
            start_ms = recent_start_ms

        # Fetch through the canonical marketdata provider so exchange/market
        # routing (Binance/Bybit/etc.) stays centralized in marketdata-provider.
        bars = self._fetch_bars_direct(key, timeframe, start_ms, end_ms)
        if bars:
            log.info(
                "periodic_fetcher.market_refreshed",
                market_key=str(key),
                source_timeframe=timeframe.canonical,
                target_timeframes=target_timeframes,
                strategies=len(strategies),
                bars_fetched=len(bars),
            )
            # Persist the refresh batch once. WebSocket paths still use
            # on_candle_closed for single confirmed candle events.
            query = BarQuery(
                instrument=InstrumentKey(
                    symbol=key.symbol,
                    exchange=key.exchange,
                    market=key.market_type,
                ),
                timeframe=timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                source="storage",
            )
            series = BarSeries(
                query=query,
                bars=tuple(bars),
                coverage=DataOrchestrator.coverage_for_series(
                    query, tuple(bars), "live"
                ),
            )
            try:
                self.orchestrator.store_bars(series)
            except StorageUnavailableError as exc:
                if "conflicting closed candle" not in str(exc):
                    raise
                log.info(
                    "periodic_fetcher.market_refresh_already_stored",
                    market_key=str(key),
                    source_timeframe=timeframe.canonical,
                    error=str(exc),
                )
            self._store_target_aggregates(
                key,
                bars,
                source_timeframe=timeframe,
                target_timeframes=target_timeframes,
            )
        else:
            log.debug(
                "periodic_fetcher.no_new_bars",
                market_key=str(key),
                source_timeframe=timeframe.canonical,
                strategies=len(strategies),
            )

    def _store_target_aggregates(
        self,
        key: RawMarketKey,
        source_bars: list[Bar],
        *,
        source_timeframe: Any,
        target_timeframes: list[str],
    ) -> None:
        """Persist target timeframe bars derived from the shared source stream."""
        source_ms = source_timeframe.duration_ms
        if source_ms is None:
            return
        incoming_by_time = {bar.time: bar for bar in source_bars}
        for target_value in target_timeframes:
            target = parse_timeframe(target_value)
            target_ms = target.duration_ms
            if target_ms is None or target_ms == source_ms:
                continue
            if target_ms < source_ms or target_ms % source_ms:
                continue
            target_close_times = [
                bar.time + source_ms
                for bar in source_bars
                if (bar.time + source_ms) % target_ms == 0
            ]
            if not target_close_times:
                continue
            by_time = self._source_context_for_aggregates(
                key,
                source_timeframe=source_timeframe,
                start_ms=min(target_close_times) - target_ms,
                end_ms=max(target_close_times),
            )
            by_time.update(incoming_by_time)
            expected = target_ms // source_ms
            aggregate_bars: list[Bar] = []
            for bar in sorted(source_bars, key=lambda item: item.time):
                close_ms = bar.time + source_ms
                if close_ms % target_ms:
                    continue
                start_ms = close_ms - target_ms
                window = [
                    by_time[start_ms + (idx * source_ms)]
                    for idx in range(expected)
                    if start_ms + (idx * source_ms) in by_time
                ]
                if len(window) != expected:
                    continue
                from openpine.workers.strategy_fanout import _aggregate_bars

                aggregate_bars.append(
                    _aggregate_bars(window, target_timeframe=target.canonical)
                )
            if not aggregate_bars:
                continue
            query = BarQuery(
                instrument=InstrumentKey(
                    symbol=key.symbol,
                    exchange=key.exchange,
                    market=key.market_type,
                ),
                timeframe=target,
                start_ms=min(bar.time for bar in aggregate_bars),
                end_ms=max(bar.time_close for bar in aggregate_bars),
                source="storage",
            )
            series = BarSeries(
                query=query,
                bars=tuple(aggregate_bars),
                coverage=DataOrchestrator.coverage_for_series(
                    query, tuple(aggregate_bars), "aggregate"
                ),
            )
            try:
                self.orchestrator.store_bars(series)
                log.info(
                    "periodic_fetcher.target_aggregates_stored",
                    market_key=str(key),
                    source_timeframe=source_timeframe.canonical,
                    target_timeframe=target.canonical,
                    bars_stored=len(aggregate_bars),
                )
            except StorageUnavailableError as exc:
                if "conflicting closed candle" not in str(exc):
                    raise
                log.info(
                    "periodic_fetcher.target_aggregate_already_stored",
                    market_key=str(key),
                    target_timeframe=target.canonical,
                    error=str(exc),
                )

    def _source_context_for_aggregates(
        self,
        key: RawMarketKey,
        *,
        source_timeframe: Any,
        start_ms: int,
        end_ms: int,
    ) -> dict[int, Bar]:
        """Load stored source bars needed to derive target timeframe closes."""
        try:
            query = BarQuery(
                instrument=InstrumentKey(
                    symbol=key.symbol,
                    exchange=key.exchange,
                    market=key.market_type,
                ),
                timeframe=source_timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                source="storage",
                gap_policy="allow_with_metadata",
            )
            return {bar.time: bar for bar in self.orchestrator.load_bars(query).bars}
        except Exception:
            return {}

    @staticmethod
    def _fetch_bars_direct(
        key: RawMarketKey,
        timeframe: Any,
        start_ms: int,
        end_ms: int,
    ) -> list[Bar]:
        """Fetch klines through the canonical marketdata provider.

        The periodic refresh path receives an exchange-aware RawMarketKey from
        strategy metadata. Keep the exchange routing in marketdata-provider
        instead of embedding REST endpoints here; otherwise non-Binance
        strategies silently request the wrong exchange.
        """
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=key.exchange,
                market=key.market_type,
                symbol=key.symbol.upper(),
            ),
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            source="provider",
            gap_policy="allow_with_metadata",
        )
        try:
            provider = create_local_marketdata_provider_adapter()
            series = provider.fetch_bars(query)
        except Exception as exc:
            log.warning(
                "periodic_fetcher.provider_error",
                market_key=str(key),
                source_timeframe=timeframe.canonical,
                error=str(exc),
            )
            return []
        return list(series.bars)

    def _latest_stored_bar_time(
        self, key: RawMarketKey, timeframe: Any, end_ms: int
    ) -> int | None:
        """Return last stored source bar open time so restart catch-up does not skip gaps."""
        try:
            query = BarQuery(
                instrument=InstrumentKey(
                    symbol=key.symbol,
                    exchange=key.exchange,
                    market=key.market_type,
                ),
                timeframe=timeframe,
                start_ms=0,
                end_ms=end_ms,
                source="storage",
                gap_policy="allow_with_metadata",
            )
            latest = self.orchestrator.latest_bar_time(query)
            return int(latest) if latest is not None else None
        except Exception:
            return None


def _group_strategies_by_market(
    strategies: list[StrategyInstance],
) -> dict[RawMarketKey, list[StrategyInstance]]:
    """Group enabled strategies by the raw market data they share."""
    groups: dict[RawMarketKey, list[StrategyInstance]] = defaultdict(list)
    for strategy in strategies:
        groups[RawMarketKey.from_strategy(strategy)].append(strategy)
    return dict(groups)


_group_strategies_by_stream = _group_strategies_by_market


__all__ = [
    "PeriodicBarFetcher",
    "RawMarketKey",
    "RefreshConfig",
    "_group_strategies_by_market",
    "_group_strategies_by_stream",
]
