"""PeriodicBarFetcher — auto-refresh bars for active strategies.

Runs every minute (configurable) and fetches latest bars for all
enabled strategies, storing them via DataOrchestrator.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import threading
import time

import structlog

from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
from openpine.data.orchestrator import DataOrchestrator
from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance

log = structlog.get_logger(__name__)


@dataclass
class RefreshConfig:
    """Configuration for periodic bar refresh."""

    interval_seconds: float = 60.0
    lookback_bars: int = 2  # fetch last N bars to catch any gaps
    enabled: bool = True


@dataclass(frozen=True)
class StreamKey:
    """Unique market-data stream/fetch key shared by enabled strategies."""

    exchange: str
    market_type: str
    symbol: str
    price_type: str
    timeframe: str

    @classmethod
    def from_strategy(cls, strategy: StrategyInstance) -> "StreamKey":
        return cls(
            exchange=strategy.exchange.lower(),
            market_type=strategy.market_type.lower(),
            symbol=strategy.symbol.upper(),
            price_type=strategy.price_type.lower(),
            timeframe=parse_timeframe(strategy.timeframe).canonical,
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

        groups = _group_strategies_by_stream(active)
        now_ms = int(time.time() * 1000)

        log.info(
            "periodic_fetcher.refreshing",
            strategies=len(active),
            stream_keys=len(groups),
        )

        for key, group in groups.items():
            try:
                self._refresh_stream_key(key, group, now_ms=now_ms)
            except Exception as exc:
                log.error(
                    "periodic_fetcher.stream_refresh_failed",
                    stream_key=str(key),
                    strategies=len(group),
                    error=str(exc),
                )

    def _refresh_strategy(self, strategy: StrategyInstance) -> None:
        """Fetch latest bars for a single strategy."""
        self._refresh_stream_key(StreamKey.from_strategy(strategy), [strategy], now_ms=int(time.time() * 1000))

    def _refresh_stream_key(
        self,
        key: StreamKey,
        strategies: list[StrategyInstance],
        *,
        now_ms: int,
    ) -> None:
        """Fetch latest bars once for a shared stream key."""
        timeframe = parse_timeframe(key.timeframe)
        if timeframe.duration_ms is None:
            raise ValueError(f"cannot periodically refresh variable-duration timeframe: {key.timeframe}")
        tf_ms = timeframe.duration_ms
        lookback_ms = tf_ms * self.config.lookback_bars
        start_ms = now_ms - lookback_ms

        query = BarQuery(
            instrument=InstrumentKey(
                symbol=key.symbol,
                exchange=key.exchange,
                market=key.market_type,
            ),
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=now_ms,
            source="provider",
        )

        bars = self.orchestrator.get_bars(query)
        if bars:
            log.info(
                "periodic_fetcher.stream_refreshed",
                stream_key=str(key),
                strategies=len(strategies),
                bars_fetched=len(bars),
            )
            # Persist via orchestrator's on_candle_closed for each bar
            for bar in bars:
                self.orchestrator.on_candle_closed(
                    bar,
                    instrument_key=key.instrument_key,
                    timeframe=key.timeframe,
                    source="live",
                )
        else:
            log.debug(
                "periodic_fetcher.no_new_bars",
                stream_key=str(key),
                strategies=len(strategies),
            )


def _group_strategies_by_stream(strategies: list[StrategyInstance]) -> dict[StreamKey, list[StrategyInstance]]:
    """Group enabled strategies by the market data they share."""
    groups: dict[StreamKey, list[StrategyInstance]] = defaultdict(list)
    for strategy in strategies:
        groups[StreamKey.from_strategy(strategy)].append(strategy)
    return dict(groups)


__all__ = [
    "PeriodicBarFetcher",
    "RefreshConfig",
    "StreamKey",
    "_group_strategies_by_stream",
]
