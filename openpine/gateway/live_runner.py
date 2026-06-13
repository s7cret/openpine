"""Live Strategy Runner — processes new bars for running strategies.

When a bar closes on a strategy's timeframe, runs a mini-backtest on recent
bars to detect new order signals, executes them via PaperExecutionAdapter,
persists orders, and sends notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import traceback
from dataclasses import dataclass

from openpine._compat import structlog

from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)


@dataclass
class RunnerConfig:
    """Configuration for the live strategy runner."""

    check_interval_seconds: float = 5.0  # how often to check for new bars
    lookback_bars: int = 500  # bars to load for mini-backtest
    recheck_bars: int = 0  # do not replay already processed bars by default
    max_catchup_bars: int = 12  # cap burst catch-up after stalls/restarts
    order_store: any = None  # set at init


@dataclass
class StrategyBarState:
    """Track last processed bar time per strategy."""

    strategy_id: str
    last_bar_time_ms: int = 0
    last_check_ms: int = 0


class LiveStrategyRunner:
    """Processes new bars for running strategies.

    Monitors bar close times. When a strategy's timeframe bar closes,
    runs a mini-backtest to detect new signals and executes orders.
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
    ) -> None:
        self.config = config or RunnerConfig()
        self.registry = registry
        self.orchestrator = orchestrator
        self.storage = storage
        self.order_store = order_store
        self.artifact_store = artifact_store
        self.state_store = state_store or self._default_state_store()

        self._running = False
        self._task: asyncio.Task | None = None
        self._strategy_states: dict[str, StrategyBarState] = {}

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
        """Stop the live runner."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        log.info("live_runner.stopped")

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

        loop = asyncio.get_event_loop()
        for bar_time in bars_to_process:
            # Run mini-backtest in executor to not block event loop
            try:
                orders = await loop.run_in_executor(
                    None,
                    lambda t=bar_time: self._run_mini_backtest(strategy, t),
                )
            except Exception as exc:
                log.error(
                    "live_runner.backtest_failed",
                    strategy_id=sid,
                    bar_time=bar_time,
                    error=str(exc),
                )
                break

            if orders is None:
                break

            log.info(
                "live_runner.backtest_result",
                strategy_id=sid,
                orders_count=len(orders),
                bar_time=bar_time,
            )
            if orders:
                await self._process_orders(strategy, orders)

            state.last_bar_time_ms = max(state.last_bar_time_ms, bar_time)

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

    def _run_mini_backtest(self, strategy, up_to_bar_time_ms: int) -> list[dict] | None:
        """Run a mini-backtest on recent bars to detect new order signals."""
        try:
            from openpine.runtime.engine import (
                BacktestEngineAdapter,
                BacktestRunConfig,
                load_strategy_class_from_artifact,
            )
            from marketdata_provider.contracts import (
                BarQuery,
                InstrumentKey,
                parse_timeframe,
            )

            # Load strategy class
            strategy_class = load_strategy_class_from_artifact(
                strategy.pine_id,
                strategy.artifact_id,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
            )

            # Load recent bars
            tf = parse_timeframe(strategy.timeframe)
            duration_ms = tf.duration_ms or 60000
            lookback_ms = duration_ms * self.config.lookback_bars
            end_ms = up_to_bar_time_ms + (tf.duration_ms or 60000)
            instrument_key = self._instrument_key(strategy)
            timeframe_key = self._timeframe_key(strategy)
            snapshot = self._load_resume_snapshot(
                strategy,
                instrument_key=instrument_key,
                timeframe=timeframe_key,
                at_or_before_bar_time=up_to_bar_time_ms,
            )
            resume_state = snapshot.state_data if snapshot is not None else None
            snapshot_bar_time = snapshot.bar_time if snapshot is not None else None
            if resume_state is not None and not self._resume_has_runtime_state(
                resume_state
            ):
                log.debug(
                    "live_runner.resume_snapshot_rebased",
                    strategy_id=strategy.strategy_id,
                    bar_time=up_to_bar_time_ms,
                    snapshot_bar_time=snapshot_bar_time,
                    reason="missing_runtime_state",
                )
                resume_state = None
                snapshot_bar_time = None
            if snapshot_bar_time is not None and snapshot_bar_time >= up_to_bar_time_ms:
                return []
            start_ms = end_ms - lookback_ms
            if snapshot_bar_time is not None and resume_state is not None:
                resume_bar_index = self._resume_bar_index(resume_state)
                if resume_bar_index is None or resume_bar_index < 0:
                    log.warning(
                        "live_runner.resume_snapshot_ignored",
                        strategy_id=strategy.strategy_id,
                        bar_time=up_to_bar_time_ms,
                        error="resume snapshot has no usable bar_index",
                    )
                    self._mark_resume_snapshot_invalid(strategy, snapshot_bar_time)
                    resume_state = None
                    snapshot_bar_time = None
                elif resume_bar_index > max(1, self.config.lookback_bars * 2):
                    log.info(
                        "live_runner.resume_snapshot_rebased",
                        strategy_id=strategy.strategy_id,
                        bar_time=up_to_bar_time_ms,
                        snapshot_bar_time=snapshot_bar_time,
                        resume_bar_index=resume_bar_index,
                    )
                    resume_state = None
                    snapshot_bar_time = None
                else:
                    start_ms = max(
                        0, snapshot_bar_time - resume_bar_index * duration_ms
                    )

            query = BarQuery(
                instrument=InstrumentKey(
                    exchange=strategy.exchange.lower(),
                    market=strategy.market_type.lower(),
                    symbol=strategy.symbol.upper(),
                ),
                timeframe=tf,
                start_ms=start_ms,
                end_ms=end_ms,
                gap_policy="allow_with_metadata",
            )
            series = (
                self.orchestrator.load_bars(query)
                if self.orchestrator is not None
                else self._fetch_direct(query)
            )
            bars = list(series.bars)

            if not bars:
                return []

            # Build config
            decl_args = {}
            if self.artifact_store:
                try:
                    artifact_data = self.artifact_store.get_artifact(
                        strategy.artifact_id, strategy.pine_id
                    )
                    if artifact_data:
                        compile_meta = artifact_data.get("compile_meta", {})
                        declaration = compile_meta.get("translation_metadata", {}).get(
                            "declaration", {}
                        )
                        decl_args = declaration.get("arguments", {})
                except Exception:
                    pass

            commission_type = {
                "cash_per_order": "fixed_per_order",
                "cash_per_contract": "fixed_per_contract",
            }.get(
                str(decl_args.get("commission_type", "none")),
                decl_args.get("commission_type", "none"),
            )

            config = BacktestRunConfig(
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
                start_time=start_ms,
                end_time=end_ms,
                exchange=strategy.exchange,
                market_type=strategy.market_type,
                initial_capital=decl_args.get("initial_capital", 10000.0),
                default_qty_type=decl_args.get("default_qty_type", "fixed"),
                default_qty_value=decl_args.get("default_qty_value", 1.0),
                commission_type=commission_type or "none",
                commission_value=decl_args.get("commission_value", 0.0),
                slippage=decl_args.get("slippage", 0.0),
                slippage_type=decl_args.get("slippage_type", "tick"),
                exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
                pyramiding=decl_args.get("pyramiding", 0),
                margin_long=decl_args.get("margin_long", 100.0),
                margin_short=decl_args.get("margin_short", 100.0),
                process_orders_on_close=bool(
                    decl_args.get("process_orders_on_close", False)
                ),
                calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
                calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
                use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
                export_resume_state=True,
                resume_validation_policy="diagnostic",
                content_hash_enabled=True,
                collect_events=True,
                collect_order_lifecycle=True,
                capture_plots=False,
            )

            # Run backtest
            from openpine.data.provider_adapter import create_local_runtime_data_provider_adapter

            adapter = BacktestEngineAdapter()
            runtime_data_provider = None
            try:
                runtime_data_provider = create_local_runtime_data_provider_adapter(
                    exchange=config.exchange,
                    market=config.market_type,
                    prefetch_end_ms=end_ms,
                )
            except Exception:
                pass

            try:
                result = adapter.run(
                    strategy_class,
                    bars,
                    config,
                    params={},
                    resume_state=resume_state,
                    runtime_data_provider=runtime_data_provider,
                )
            except Exception as exc:
                if resume_state is None or not self._is_resume_replay_error(exc):
                    raise
                log.warning(
                    "live_runner.resume_snapshot_ignored",
                    strategy_id=strategy.strategy_id,
                    bar_time=up_to_bar_time_ms,
                    error=str(exc),
                )
                self._mark_resume_snapshot_invalid(strategy, snapshot_bar_time)
                start_ms = end_ms - lookback_ms
                query = BarQuery(
                    instrument=query.instrument,
                    timeframe=tf,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    gap_policy="allow_with_metadata",
                )
                series = (
                    self.orchestrator.load_bars(query)
                    if self.orchestrator is not None
                    else self._fetch_direct(query)
                )
                bars = list(series.bars)
                if not bars:
                    return []
                config = BacktestRunConfig(
                    **{**config.__dict__, "start_time": start_ms}
                )
                result = adapter.run(
                    strategy_class,
                    bars,
                    config,
                    params={},
                    resume_state=None,
                    runtime_data_provider=runtime_data_provider,
                )

            new_orders = self._extract_new_orders(result.raw_result, up_to_bar_time_ms)
            self._attach_risk_prices(strategy, new_orders)
            if resume_state is not None and not new_orders:
                start_ms = end_ms - lookback_ms
                query = BarQuery(
                    instrument=query.instrument,
                    timeframe=tf,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    gap_policy="allow_with_metadata",
                )
                series = (
                    self.orchestrator.load_bars(query)
                    if self.orchestrator is not None
                    else self._fetch_direct(query)
                )
                bars = list(series.bars)
                if bars:
                    config = BacktestRunConfig(
                        **{**config.__dict__, "start_time": start_ms}
                    )
                    fallback_result = adapter.run(
                        strategy_class,
                        bars,
                        config,
                        params={},
                        resume_state=None,
                        runtime_data_provider=runtime_data_provider,
                    )
                    fallback_orders = self._extract_new_orders(
                        fallback_result.raw_result, up_to_bar_time_ms
                    )
                    self._attach_risk_prices(strategy, fallback_orders)
                    if fallback_orders:
                        log.info(
                            "live_runner.resume_zero_orders_fallback",
                            strategy_id=strategy.strategy_id,
                            bar_time=up_to_bar_time_ms,
                            orders_count=len(fallback_orders),
                        )
                        result = fallback_result
                        new_orders = fallback_orders

            self._save_resume_snapshot(
                strategy,
                result=result,
                instrument_key=instrument_key,
                timeframe=timeframe_key,
                bar_time=up_to_bar_time_ms,
                data_fingerprint=self._series_fingerprint(series),
            )

            return new_orders

        except Exception as exc:
            log.error(
                "live_runner.mini_backtest_error",
                error=str(exc),
                tb=traceback.format_exc(),
            )
            return None

    @staticmethod
    def _extract_new_orders(raw, up_to_bar_time_ms: int) -> list[dict]:
        trades = getattr(raw, "trades", []) or []
        order_lifecycle = getattr(raw, "order_lifecycle", []) or []
        new_orders = []
        for trade in trades:
            exit_time = getattr(trade, "exit_time", None) or getattr(
                trade, "exit_bar_time", None
            )
            entry_time = getattr(trade, "entry_time", None) or getattr(
                trade, "entry_bar_time", None
            )
            if entry_time and entry_time >= up_to_bar_time_ms:
                new_orders.append(
                    {
                        "side": getattr(trade, "direction", "long"),
                        "entry_price": getattr(trade, "entry_price", 0),
                        "exit_price": getattr(trade, "exit_price", None),
                        "qty": getattr(trade, "qty", 0),
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "net_pnl": getattr(trade, "net_pnl", None),
                    }
                )

        for order in order_lifecycle:
            order_time = getattr(order, "created_at", None) or getattr(
                order, "time", None
            )
            if order_time and order_time >= up_to_bar_time_ms:
                new_orders.append(
                    {
                        "side": getattr(order, "side", "buy"),
                        "price": getattr(order, "price", 0),
                        "qty": getattr(order, "quantity", 0),
                        "order_time": order_time,
                        "order_type": getattr(order, "order_type", "market"),
                    }
                )
        return new_orders

    @staticmethod
    def _is_resume_replay_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "resume" in message
            or "config hash" in message
            or "content hash" in message
            or "bar index mismatch" in message
        )

    @staticmethod
    def _resume_bar_index(resume_state) -> int | None:
        value = getattr(resume_state, "bar_index", None)
        if value is None and isinstance(resume_state, dict):
            value = resume_state.get("bar_index")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resume_has_runtime_state(resume_state) -> bool:
        value = getattr(resume_state, "runtime_state", None)
        if value is None and isinstance(resume_state, dict):
            value = resume_state.get("runtime_state")
        return value is not None

    async def _process_orders(self, strategy, orders: list[dict]) -> None:
        """Process new orders: save to DB and send notifications."""
        if not orders:
            return

        sid = strategy.strategy_id
        now_ms = int(time.time() * 1000)

        saved_orders: list[dict] = []

        # Save orders to DB
        if self.storage:
            for order in orders:
                try:
                    client_order_id = self._client_order_id(strategy, order)
                    created_at = int(
                        order.get("entry_time", order.get("order_time", now_ms))
                        or now_ms
                    )
                    price = order.get("entry_price", order.get("price", 0))
                    stop_price = order.get("stop_price")
                    take_profit_price = order.get("take_profit_price")
                    qty = order.get("qty", 0)
                    self.storage.execute(
                        """INSERT OR IGNORE INTO orders
                           (order_id, strategy_id, client_order_id, symbol, side, order_type, qty,
                            limit_price, stop_price, take_profit_price, status, filled_quantity, avg_fill_price,
                            intent_json, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            client_order_id,
                            sid,
                            client_order_id,
                            strategy.symbol,
                            order.get("side", "buy"),
                            order.get("order_type", "market"),
                            qty,
                            price,
                            stop_price,
                            take_profit_price,
                            "filled",
                            qty,
                            price,
                            json.dumps(order, sort_keys=True, default=str),
                            created_at,
                            now_ms,
                        ),
                    )
                    if self.storage.execute("SELECT changes()").fetchone()[0]:
                        saved_orders.append(order)
                except Exception as exc:
                    log.error("live_runner.order_save_error", error=str(exc))

            try:
                self.storage.commit()
            except Exception:
                pass

        # Send notification via ws_manager
        for order in saved_orders:
            side = order.get("side", "buy").upper()
            price = order.get("entry_price", order.get("price", 0))
            qty = order.get("qty", 0)
            pnl = order.get("net_pnl")
            pnl_str = f" (PnL: {pnl:+.2f})" if pnl is not None else ""

            msg = f"📊 {strategy.name}: {side} {qty} {strategy.symbol} @ {price:.4f}{pnl_str}"

            # Store as a notification in ws_manager
            ws_manager.update_progress(
                f"order_{sid}_{now_ms}",
                "order",
                "new_order",
                1.0,
                msg,
                detail={
                    "strategy_id": sid,
                    "strategy_name": strategy.name,
                    "symbol": strategy.symbol,
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "pnl": pnl,
                    "time": now_ms,
                },
            )
            await ws_manager.broadcast_progress(f"order_{sid}_{now_ms}")

            log.info(
                "live_runner.order_executed",
                strategy_id=sid,
                symbol=strategy.symbol,
                side=side,
                price=price,
                qty=qty,
                pnl=pnl,
            )

    @staticmethod
    def _extract_percent_input(source_text: str, name: str) -> float | None:
        match = re.search(
            rf"\b{re.escape(name)}\s*=\s*input\.float\(\s*([-+]?\d+(?:\.\d+)?)",
            source_text,
        )
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _strategy_risk_percents(self, strategy) -> tuple[float | None, float | None]:
        if self.storage is None:
            return None, None
        try:
            row = self.storage.execute(
                "SELECT source_text FROM pine_sources WHERE pine_id = ?",
                (strategy.pine_id,),
            ).fetchone()
        except Exception as exc:
            log.warning(
                "live_runner.risk_source_load_failed",
                strategy_id=strategy.strategy_id,
                error=str(exc),
            )
            return None, None
        if not row or not row[0]:
            return None, None
        source_text = str(row[0])
        return (
            self._extract_percent_input(source_text, "tpPct"),
            self._extract_percent_input(source_text, "slPct"),
        )

    def _attach_risk_prices(self, strategy, orders: list[dict]) -> None:
        if not orders:
            return
        tp_pct, sl_pct = self._strategy_risk_percents(strategy)
        if tp_pct is None and sl_pct is None:
            return

        for order in orders:
            entry_price = order.get("entry_price", order.get("price"))
            try:
                entry_price = float(entry_price)
            except (TypeError, ValueError):
                continue

            side = str(order.get("side", "")).lower()
            is_short = side in {"sell", "short"}
            if tp_pct is not None and order.get("take_profit_price") is None:
                multiplier = 1 - tp_pct / 100 if is_short else 1 + tp_pct / 100
                order["take_profit_price"] = entry_price * multiplier
            if sl_pct is not None and order.get("stop_price") is None:
                multiplier = 1 + sl_pct / 100 if is_short else 1 - sl_pct / 100
                order["stop_price"] = entry_price * multiplier

    @staticmethod
    def _client_order_id(strategy, order: dict) -> str:
        payload = {
            "strategy_id": strategy.strategy_id,
            "symbol": strategy.symbol,
            "side": order.get("side"),
            "order_type": order.get("order_type", "market"),
            "qty": order.get("qty"),
            "price": order.get("entry_price", order.get("price")),
            "stop_price": order.get("stop_price"),
            "take_profit_price": order.get("take_profit_price"),
            "entry_time": order.get("entry_time", order.get("order_time")),
            "exit_time": order.get("exit_time"),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:24]
        return f"live_{strategy.strategy_id}_{digest}"

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
        from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

        return create_local_marketdata_provider_adapter().fetch_bars(query)

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
