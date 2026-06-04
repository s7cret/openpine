"""Live Strategy Runner — processes new bars for running strategies.

When a bar closes on a strategy's timeframe, runs a mini-backtest on recent
bars to detect new order signals, executes them via PaperExecutionAdapter,
persists orders, and sends notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
from dataclasses import dataclass, field

import structlog

from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)


@dataclass
class RunnerConfig:
    """Configuration for the live strategy runner."""
    check_interval_seconds: float = 5.0  # how often to check for new bars
    lookback_bars: int = 500  # bars to load for mini-backtest
    recheck_bars: int = 1  # re-run recent closed bars to catch late signals
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
    ) -> None:
        self.config = config or RunnerConfig()
        self.registry = registry
        self.orchestrator = orchestrator
        self.storage = storage
        self.order_store = order_store
        self.artifact_store = artifact_store

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

        # Get or create state
        if sid not in self._strategy_states:
            self._strategy_states[sid] = StrategyBarState(strategy_id=sid)
        state = self._strategy_states[sid]

        # Calculate the latest closed bar time
        # A bar at time T closes at T + duration_ms
        current_bar_start = now_ms - (now_ms % tf.duration_ms)
        latest_closed_bar_time = current_bar_start - tf.duration_ms

        # Skip until a new closed bar exists. Recent bars are rechecked only
        # when time advances, which catches late signals without running the
        # mini-backtest every five seconds on the same candle.
        if latest_closed_bar_time <= state.last_bar_time_ms:
            return

        state.last_check_ms = now_ms
        bars_to_process = self._bars_to_process(state, latest_closed_bar_time, tf.duration_ms)

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
                log.error("live_runner.backtest_failed", strategy_id=sid, bar_time=bar_time, error=str(exc))
                break

            if orders is None:
                break

            log.info("live_runner.backtest_result", strategy_id=sid, orders_count=len(orders), bar_time=bar_time)
            if orders:
                await self._process_orders(strategy, orders)

            state.last_bar_time_ms = max(state.last_bar_time_ms, bar_time)

    def _bars_to_process(self, state: StrategyBarState, latest_closed_bar_time: int, duration_ms: int) -> list[int]:
        """Return closed bar starts to process, including bounded catch-up."""
        if state.last_bar_time_ms <= 0:
            return [latest_closed_bar_time]

        first_unprocessed = state.last_bar_time_ms + duration_ms
        recheck_from = max(0, state.last_bar_time_ms - self.config.recheck_bars * duration_ms)
        catchup_floor = latest_closed_bar_time - max(0, self.config.max_catchup_bars - 1) * duration_ms
        start = max(min(first_unprocessed, recheck_from), catchup_floor)

        return list(range(start, latest_closed_bar_time + duration_ms, duration_ms))

    def _run_mini_backtest(self, strategy, up_to_bar_time_ms: int) -> list[dict] | None:
        """Run a mini-backtest on recent bars to detect new order signals."""
        try:
            from openpine.runtime.engine import (
                BacktestEngineAdapter,
                BacktestRunConfig,
                load_strategy_class_from_artifact,
            )
            from openpine.data.direct_provider import DirectBinanceProvider
            from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

            # Load strategy class
            strategy_class = load_strategy_class_from_artifact(
                strategy.pine_id,
                strategy.artifact_id,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
            )

            # Load recent bars
            tf = parse_timeframe(strategy.timeframe)
            lookback_ms = (tf.duration_ms or 60000) * self.config.lookback_bars
            end_ms = up_to_bar_time_ms + (tf.duration_ms or 60000)
            start_ms = end_ms - lookback_ms

            provider = DirectBinanceProvider()
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
            series = provider.fetch_bars(query)
            bars = list(series.bars)

            if not bars:
                return []

            # Build config
            decl_args = {}
            if self.artifact_store:
                try:
                    artifact_data = self.artifact_store.get_artifact(strategy.artifact_id, strategy.pine_id)
                    if artifact_data:
                        compile_meta = artifact_data.get("compile_meta", {})
                        declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
                        decl_args = declaration.get("arguments", {})
                except Exception:
                    pass

            commission_type = {
                "cash_per_order": "fixed_per_order",
                "cash_per_contract": "fixed_per_contract",
            }.get(str(decl_args.get("commission_type", "none")), decl_args.get("commission_type", "none"))

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
                process_orders_on_close=bool(decl_args.get("process_orders_on_close", False)),
                calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
                calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
                use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
                export_resume_state=False,
                content_hash_enabled=True,
                collect_events=True,
                collect_order_lifecycle=True,
                capture_plots=False,
            )

            # Run backtest
            from openpine.data.direct_data_provider import DirectBinanceDataProvider
            adapter = BacktestEngineAdapter()
            runtime_data_provider = None
            try:
                runtime_data_provider = DirectBinanceDataProvider(market=config.market_type)
            except Exception:
                pass

            result = adapter.run(
                strategy_class,
                bars,
                config,
                params={},
                runtime_data_provider=runtime_data_provider,
            )

            # Extract trades/orders from result
            raw = result.raw_result
            trades = getattr(raw, "trades", []) or []
            order_lifecycle = getattr(raw, "order_lifecycle", []) or []

            # Filter to only trades that closed at or after our target bar time
            new_orders = []
            for trade in trades:
                exit_time = getattr(trade, "exit_time", None) or getattr(trade, "exit_bar_time", None)
                entry_time = getattr(trade, "entry_time", None) or getattr(trade, "entry_bar_time", None)
                # Only include trades that entered or exited on the latest bar
                if entry_time and entry_time >= up_to_bar_time_ms:
                    new_orders.append({
                        "side": getattr(trade, "direction", "long"),
                        "entry_price": getattr(trade, "entry_price", 0),
                        "exit_price": getattr(trade, "exit_price", None),
                        "qty": getattr(trade, "qty", 0),
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                        "net_pnl": getattr(trade, "net_pnl", None),
                    })

            # Also check order_lifecycle for orders on the latest bar
            for order in order_lifecycle:
                order_time = getattr(order, "created_at", None) or getattr(order, "time", None)
                if order_time and order_time >= up_to_bar_time_ms:
                    new_orders.append({
                        "side": getattr(order, "side", "buy"),
                        "price": getattr(order, "price", 0),
                        "qty": getattr(order, "quantity", 0),
                        "order_time": order_time,
                        "order_type": getattr(order, "order_type", "market"),
                    })

            return new_orders

        except Exception as exc:
            log.error("live_runner.mini_backtest_error", error=str(exc), tb=traceback.format_exc())
            return None

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
                    created_at = int(order.get("entry_time", order.get("order_time", now_ms)) or now_ms)
                    price = order.get("entry_price", order.get("price", 0))
                    qty = order.get("qty", 0)
                    self.storage.execute(
                        """INSERT OR IGNORE INTO orders
                           (order_id, strategy_id, client_order_id, symbol, side, order_type, qty,
                            limit_price, status, filled_quantity, avg_fill_price,
                            intent_json, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            client_order_id,
                            sid,
                            client_order_id,
                            strategy.symbol,
                            order.get("side", "buy"),
                            order.get("order_type", "market"),
                            qty,
                            price,
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
    def _client_order_id(strategy, order: dict) -> str:
        payload = {
            "strategy_id": strategy.strategy_id,
            "symbol": strategy.symbol,
            "side": order.get("side"),
            "order_type": order.get("order_type", "market"),
            "qty": order.get("qty"),
            "price": order.get("entry_price", order.get("price")),
            "entry_time": order.get("entry_time", order.get("order_time")),
            "exit_time": order.get("exit_time"),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
        return f"live_{strategy.strategy_id}_{digest}"
