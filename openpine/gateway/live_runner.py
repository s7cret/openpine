"""Fail-closed live runner shell pending canonical order authority."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

from openpine._compat import structlog

log = structlog.get_logger(__name__)


class CanonicalOrderRouterRequired(RuntimeError):
    """No live decision is admitted without Intent→Risk→OrderRouter authority."""


@dataclass
class RunnerConfig:
    """Configuration for the live strategy runner."""

    check_interval_seconds: float = 5.0  # how often to check for new bars
    lookback_bars: int = 500  # bars to load for mini-backtest
    recheck_bars: int = 0  # do not replay already processed bars by default
    max_catchup_bars: int = 12  # cap burst catch-up after stalls/restarts
    shutdown_timeout_seconds: float = 30.0
    order_store: Any = None  # set at init


@dataclass
class StrategyBarState:
    """Track last processed bar time per strategy."""

    strategy_id: str
    last_bar_time_ms: int = 0
    last_check_ms: int = 0


class LiveStrategyRunner:
    """Processes new bars for running strategies.

    It may monitor bar clocks, but cannot synthesize orders from backtest output.
    Live execution remains unavailable until the canonical order router is wired.
    """

    def __init__(
        self,
        config: RunnerConfig | None = None,
        registry=None,
        orchestrator=None,
        storage=None,
        order_store=None,
        artifact_store=None,
        state_store=None,
        htf_bars: list[dict[str, Any]] | None = None,
        htf_timeframe: str | None = None,
    ) -> None:
        self.config = config or RunnerConfig()
        self.registry = registry
        self.orchestrator = orchestrator
        self.storage = storage
        self.order_store = order_store
        self.artifact_store = artifact_store
        self.state_store = state_store or self._default_state_store()
        self.htf_bars = htf_bars
        self.htf_timeframe = htf_timeframe
        self._htf_timeframe_by_strategy: dict[str, str] = {}
        self._mtf_series_by_strategy: dict[str, tuple[Any, ...]] = {}

        self._running = False
        self._task: asyncio.Task | None = None
        self._executor_futures: set[asyncio.Future[Any]] = set()
        self._strategy_states: dict[str, StrategyBarState] = {}
        self._stamped_sources: dict[tuple[str, str], bytes] = {}

    def set_strategy_htf_timeframe(self, strategy_id: str, timeframe: str | None) -> None:
        key = str(strategy_id)
        if timeframe is None or str(timeframe) == "":
            self._htf_timeframe_by_strategy.pop(key, None)
            return
        self._htf_timeframe_by_strategy[key] = str(timeframe)

    def set_strategy_mtf_series(self, strategy_id: str, series) -> None:
        from openpine.runtime.mtf import normalize_mtf_requests

        key = str(strategy_id)
        normalized = normalize_mtf_requests(series)
        if not normalized:
            self._mtf_series_by_strategy.pop(key, None)
            return
        self._mtf_series_by_strategy[key] = normalized

    def _requested_htf_timeframe(self, strategy) -> str | None:
        key = str(getattr(strategy, "strategy_id", ""))
        if key in self._htf_timeframe_by_strategy:
            return self._htf_timeframe_by_strategy[key]
        return self.htf_timeframe

    def _confirmed_htf_bars(self, strategy, bars):
        if self.htf_bars is not None:
            return self.htf_bars
        from openpine.runtime.isolated_run import _confirmed_htf_bars_for_timeframe
        from openpine.runtime.mtf import (
            admitted_mtf_requests,
            confirmed_mtf_bars_for_requests,
        )

        requested = self._requested_htf_timeframe(strategy)
        strategy_key = str(getattr(strategy, "strategy_id", ""))
        requests = self._mtf_series_by_strategy.get(strategy_key)
        if requests is None:
            requests = admitted_mtf_requests(
                chart_symbol=strategy.symbol,
                htf_timeframe=requested,
            )
        if requests:
            return confirmed_mtf_bars_for_requests(
                chart_bars=bars,
                chart_symbol=strategy.symbol,
                chart_timeframe=strategy.timeframe,
                requests=requests,
                load_bars=lambda symbol, timeframe: self._load_mtf_provider_bars(
                    strategy, bars, symbol, timeframe
                ),
            )
        fetched = None
        if requested and str(requested) != str(strategy.timeframe) and bars:
            fetched = self._load_htf_provider_bars(strategy, bars, str(requested))
        return _confirmed_htf_bars_for_timeframe(
            chart_bars=bars,
            symbol=str(strategy.symbol).upper(),
            chart_timeframe=str(strategy.timeframe),
            requested_timeframe=requested,
            fetched_htf_bars=fetched,
        )

    def _load_htf_provider_bars(self, strategy, bars, timeframe: str):
        return self._load_mtf_provider_bars(
            strategy, bars, strategy.symbol, timeframe
        )

    def _load_mtf_provider_bars(self, strategy, bars, symbol: str, timeframe: str):
        from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

        first = bars[0]
        last = bars[-1]
        start_ms = first.get("time", 0) if isinstance(first, dict) else getattr(first, "time", 0)
        end_ms = (
            last.get("time_close")
            if isinstance(last, dict)
            else getattr(last, "time_close", None)
        )
        tf = parse_timeframe(timeframe)
        if end_ms is None:
            last_time = last.get("time", 0) if isinstance(last, dict) else getattr(last, "time", 0)
            end_ms = int(last_time) + (tf.duration_ms or 60_000)
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=strategy.exchange.lower(),
                market=strategy.market_type.lower(),
                symbol=str(symbol).upper(),
            ),
            timeframe=tf,
            start_ms=int(start_ms),
            end_ms=int(end_ms),
            gap_policy="allow_with_metadata",
        )
        series = (
            self.orchestrator.load_bars(query)
            if self.orchestrator is not None
            else self._fetch_direct(query)
        )
        return list(series.bars)

    def start(self) -> None:
        """Start the live runner as an async task."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_event_loop()
            self._task = loop.create_task(self._run_loop())
        except RuntimeError:
            log.warning("live_runner.no_event_loop")
        log.info("live_runner.started", interval=self.config.check_interval_seconds)

    def stop(self) -> None:
        """Request live-runner shutdown without claiming quiescence."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("live_runner.stop_requested")

    async def wait_stopped(self) -> bool:
        """Wait boundedly for the task and executor work to become quiescent."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, self.config.shutdown_timeout_seconds)
        task = self._task
        if task is not None and not task.done():
            _done, pending_tasks = await asyncio.wait(
                {task},
                timeout=max(0.0, deadline - loop.time()),
            )
            if pending_tasks:
                log.error("live_runner.shutdown_timeout", pending_tasks=1)
                return False
        if task is not None and task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass

        pending = [future for future in self._executor_futures if not future.done()]
        if pending:
            _done, still_pending = await asyncio.wait(
                pending,
                timeout=max(0.0, deadline - loop.time()),
            )
            if still_pending:
                log.error(
                    "live_runner.shutdown_timeout",
                    pending_executor_jobs=len(still_pending),
                )
                return False
        log.info("live_runner.stopped")
        return True

    def _executor_future_done(self, future: asyncio.Future[Any]) -> None:
        self._executor_futures.discard(future)
        if future.cancelled():
            return
        try:
            future.exception()
        except Exception:
            return

    async def _run_loop(self) -> None:
        """Main loop: check for bar closes and process strategies."""
        while self._running:
            try:
                await self._check_all_strategies()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("live_runner.loop_error", error=str(exc))
            await asyncio.sleep(self.config.check_interval_seconds)

    async def _check_all_strategies(self) -> None:
        """Check all running strategies for new bar closes."""
        if not self.registry:
            return

        strategies = self.registry.list_strategies()
        running = [s for s in strategies if s.enabled and s.status == "running"]

        if not running:
            return

        now_ms = int(time.time() * 1000)

        for strategy in running:
            try:
                await self._process_strategy(strategy, now_ms)
            except Exception as exc:
                log.error(
                    "live_runner.strategy_error",
                    strategy_id=strategy.strategy_id,
                    error=str(exc),
                )

    async def _process_strategy(self, strategy, now_ms: int) -> None:
        """Process a single strategy if its timeframe bar has closed."""
        from marketdata_provider.contracts import parse_timeframe

        sid = strategy.strategy_id
        tf = parse_timeframe(strategy.timeframe)
        if tf.duration_ms is None:
            return

        # Calculate the latest closed bar time. A bar at time T closes at
        # T + duration_ms.
        current_bar_start = now_ms - (now_ms % tf.duration_ms)
        latest_closed_bar_time = current_bar_start - tf.duration_ms

        # Get or create state
        if sid not in self._strategy_states:
            self._strategy_states[sid] = StrategyBarState(
                strategy_id=sid,
                last_bar_time_ms=self._latest_processed_bar_time(
                    strategy, latest_closed_bar_time
                ),
            )
        state = self._strategy_states[sid]

        # Skip until a new closed bar exists. Recent bars are rechecked only
        # when time advances, which catches late signals without running the
        # mini-backtest every five seconds on the same candle.
        if latest_closed_bar_time <= state.last_bar_time_ms:
            return

        state.last_check_ms = now_ms
        bars_to_process = self._bars_to_process(
            state, latest_closed_bar_time, tf.duration_ms
        )

        log.info(
            "live_runner.new_bars",
            strategy_id=sid,
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            bar_time=latest_closed_bar_time,
            from_bar=bars_to_process[0] if bars_to_process else None,
            bars=len(bars_to_process),
        )

        raise CanonicalOrderRouterRequired(
            "live authority unavailable: canonical Intent→Risk→OrderRouter→BrokerEvent "
            "pipeline is required"
        )

    def _bars_to_process(
        self, state: StrategyBarState, latest_closed_bar_time: int, duration_ms: int
    ) -> list[int]:
        """Return closed bar starts to process, including bounded catch-up."""
        if state.last_bar_time_ms <= 0:
            return [latest_closed_bar_time]

        first_unprocessed = state.last_bar_time_ms + duration_ms
        recheck_from = (
            max(0, state.last_bar_time_ms - self.config.recheck_bars * duration_ms)
            if self.config.recheck_bars > 0
            else first_unprocessed
        )
        max_new_bars = max(1, self.config.max_catchup_bars)
        end = min(
            latest_closed_bar_time, first_unprocessed + (max_new_bars - 1) * duration_ms
        )
        start = min(first_unprocessed, recheck_from)

        return list(range(start, end + duration_ms, duration_ms))

    def _latest_processed_bar_time(self, strategy, latest_closed_bar_time: int) -> int:
        if self.state_store is None:
            return 0
        try:
            meta = self.state_store.latest_snapshot_metadata(
                strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=self._instrument_key(strategy),
                timeframe=self._timeframe_key(strategy),
                at_or_before_bar_time=latest_closed_bar_time,
            )
            return int(meta.bar_time) if meta is not None else 0
        except Exception as exc:
            log.warning(
                "live_runner.latest_processed_load_failed",
                strategy_id=strategy.strategy_id,
                error=str(exc),
            )
            return 0

    @staticmethod
    def _default_state_store():
        try:
            from openpine.config import OpenPineConfig
            from openpine.state.store import StateStore

            return StateStore(OpenPineConfig.load().data_dir / "state")
        except Exception as exc:
            log.warning("live_runner.state_store_init_failed", error=str(exc))
            return None

    @staticmethod
    def _instrument_key(strategy) -> dict:
        return {
            "exchange": strategy.exchange.lower(),
            "market": strategy.market_type.lower(),
            "symbol": strategy.symbol.upper(),
            "price_type": "trade",
        }

    @staticmethod
    def _timeframe_key(strategy) -> dict:
        return {"canonical": str(strategy.timeframe)}

    def _load_resume_snapshot(
        self,
        strategy,
        *,
        instrument_key: dict,
        timeframe: dict,
        at_or_before_bar_time: int,
    ):
        if self.state_store is None:
            return None
        try:
            return self.state_store.load_latest_compatible(
                strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=instrument_key,
                timeframe=timeframe,
                at_or_before_bar_time=at_or_before_bar_time,
            )
        except Exception as exc:
            log.warning(
                "live_runner.resume_snapshot_load_failed",
                strategy_id=strategy.strategy_id,
                error=str(exc),
            )
            return None

    def _save_resume_snapshot(
        self,
        strategy,
        *,
        result,
        instrument_key: dict,
        timeframe: dict,
        bar_time: int,
        data_fingerprint: str | None,
    ) -> None:
        if self.state_store is None:
            return
        resume_state = getattr(result, "resume_state", None)
        if resume_state is None:
            return
        try:
            self.state_store.save_runtime_snapshot(
                strategy_id=strategy.strategy_id,
                artifact_id=strategy.artifact_id,
                params_hash=strategy.params_hash,
                instrument_key=instrument_key,
                timeframe=timeframe,
                runtime_state=resume_state,
                bar_time=bar_time,
                reason="live_bar",
                data_fingerprint=data_fingerprint,
            )
        except Exception as exc:
            log.warning(
                "live_runner.resume_snapshot_save_failed",
                strategy_id=strategy.strategy_id,
                error=str(exc),
            )

    def _mark_resume_snapshot_invalid(self, strategy, bar_time: int | None) -> None:
        if self.state_store is None:
            return
        try:
            self.state_store.mark_invalid(strategy.strategy_id, since_bar_time=bar_time)
        except Exception as exc:
            log.warning(
                "live_runner.resume_snapshot_invalidate_failed",
                strategy_id=strategy.strategy_id,
                error=str(exc),
            )

    @staticmethod
    def _fetch_direct(query):
        from openpine.data.orchestrator import DataOrchestrator
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

        provider = create_local_marketdata_provider_adapter()
        return DataOrchestrator(provider=provider).load_bars(query)

    @staticmethod
    def _series_fingerprint(series) -> str:
        digest = hashlib.sha256()
        digest.update(b"openpine.live.bar_series.v1\0")
        digest.update(str(series.query.instrument.exchange).encode())
        digest.update(b"\0")
        digest.update(str(series.query.instrument.market).encode())
        digest.update(b"\0")
        digest.update(str(series.query.instrument.symbol).encode())
        digest.update(b"\0")
        digest.update(str(series.query.timeframe.canonical).encode())
        digest.update(b"\0")
        for bar in series.bars:
            digest.update(
                (
                    f"{bar.time}|{bar.time_close}|{bar.open:.12g}|{bar.high:.12g}|"
                    f"{bar.low:.12g}|{bar.close:.12g}|{bar.volume!r}\n"
                ).encode()
            )
        return digest.hexdigest()
