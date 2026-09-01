"""BacktestEngine adapter for OpenPine runtime execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from marketdata_provider.contracts import Bar

from openpine.integrations import import_library


@dataclass(frozen=True)
class BacktestRunConfig:
    """Minimal normalized config for a backtest-engine run."""

    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    exchange: str = "binance"
    market_type: str = "spot"
    initial_capital: float = 100_000.0
    default_qty_type: str = "fixed"
    default_qty_value: float = 1.0
    commission_type: str = "none"
    commission_value: float = 0.0
    slippage: float = 0.0
    slippage_type: str = "tick"
    exit_matching: str = "fifo"
    pyramiding: int = 0
    margin_long: float = 100.0
    margin_short: float = 100.0
    process_orders_on_close: bool = False
    calc_on_order_fills: bool = False
    calc_on_every_tick: bool = False
    use_bar_magnifier: bool = False
    qty_step: float | None = None
    qty_rounding_mode: str = "none"
    mintick: float | None = None
    max_bars_back: int = 0
    score_start_time: int | None = None
    score_end_time: int | None = None
    max_pre_bars: int = 0
    warmup_metadata: dict | None = None
    export_resume_state: bool = False
    resume_validation_policy: str = "strict"
    content_hash_enabled: bool = True
    collect_events: bool = True
    collect_order_lifecycle: bool = True
    capture_plots: bool = False
    plot_from_ms: int | None = None
    plot_to_ms: int | None = None
    semantic_profile: str = ""
    generated_artifact: dict | None = None
    execution_context: dict | None = None
    instrument_id: str | None = None


@dataclass(frozen=True)
class BacktestRunResult:
    """Normalized result returned by BacktestEngineAdapter."""

    status: str
    bars_processed: int
    raw_result: Any
    uses_backtest_engine: bool = True
    process_next_bar_available: bool = False
    resume_state: Any | None = None


class BacktestArtifactError(RuntimeError):
    """Raised when a compiled strategy artifact cannot be loaded safely."""


class BacktestEngineAdapter:
    """Narrow OpenPine adapter over the local backtest-engine package."""

    def __init__(self) -> None:
        self._module = import_library("backtest_engine")

    @property
    def process_next_bar_available(self) -> bool:
        """Whether the external engine exposes a native process_next_bar API."""
        return hasattr(self._module.BacktestEngine, "process_next_bar")

    def run(
        self,
        strategy_class: type,
        bars: list[Bar],
        config: BacktestRunConfig,
        params: dict | None = None,
        execution_backend: Any | None = None,
        progress_callback: Any | None = None,
        runtime_data_provider: Any | None = None,
        resume_state: Any | None = None,
        effective_pre_bars: int | None = None,
    ) -> BacktestRunResult:
        """Run a strategy through the external BacktestEngine."""
        from openpine.admission import admit_semantic_profile
        from openpine.runtime.isolated_run import IsolatedRunError
        from openpine_contracts import AdmitError

        raw_profile = getattr(config, "semantic_profile", None)
        if raw_profile is None or not str(raw_profile).strip():
            raise IsolatedRunError("semantic_profile is required")
        try:
            admitted = admit_semantic_profile(
                profile=raw_profile,
                source="generated_artifact.v3",
            )
        except AdmitError as exc:
            raise IsolatedRunError(str(exc)) from exc
        engine_bars = [self._to_engine_bar(bar) for bar in bars]
        configured_rounding = getattr(config, "qty_rounding_mode", None)
        qty_rounding = (
            "floor"
            if configured_rounding in {None, "none", "truncate"}
            else configured_rounding
        )
        engine_config = self._module.BacktestConfig(
            symbol=config.symbol,
            timeframe=config.timeframe,
            start_time=config.start_time,
            end_time=config.end_time,
            initial_capital=config.initial_capital,
            default_qty_type=config.default_qty_type,
            default_qty_value=config.default_qty_value,
            commission_type=config.commission_type,
            commission_value=config.commission_value,
            slippage=config.slippage,
            slippage_type=config.slippage_type,
            exit_matching=config.exit_matching,
            pyramiding=config.pyramiding,
            margin_long=config.margin_long,
            margin_short=config.margin_short,
            process_orders_on_close=config.process_orders_on_close,
            calc_on_order_fills=config.calc_on_order_fills,
            calc_on_every_tick=config.calc_on_every_tick,
            use_bar_magnifier=config.use_bar_magnifier,
            qty_step=config.qty_step,
            qty_rounding=qty_rounding,
            mintick=config.mintick,
            max_bars_back=config.max_bars_back,
            score_start_time=config.score_start_time,
            score_end_time=config.score_end_time,
            max_pre_bars=config.max_pre_bars,
            warmup_metadata=config.warmup_metadata,
            export_resume_state=config.export_resume_state,
            resume_validation_policy=config.resume_validation_policy,
            content_hash_enabled=config.content_hash_enabled,
            collect_events=config.collect_events,
            collect_order_lifecycle=config.collect_order_lifecycle,
            semantic_profile=admitted.value,
        )
        engine_config.exchange = config.exchange
        engine_config.market_type = config.market_type
        engine = self._module.BacktestEngine(engine_config)
        runtime_kwargs = {
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "plot_from_ms": config.plot_from_ms,
            "plot_to_ms": config.plot_to_ms,
        }
        if progress_callback is not None:
            runtime_kwargs["progress_callback"] = progress_callback
        strategy_class.runtime_capture_plots = config.capture_plots
        strategy_class.runtime_plot_from_ms = config.plot_from_ms
        strategy_class.runtime_plot_to_ms = config.plot_to_ms
        strategy_class.runtime_request_data_end_ms = config.end_time
        if runtime_data_provider is not None:
            strategy_class.runtime_data_provider = runtime_data_provider

        callbacks = None
        if progress_callback is not None and engine_bars:
            callbacks = self._progress_callbacks(progress_callback, len(engine_bars))

        result = engine.run(
            strategy_class,
            params=params or {},
            bars=engine_bars,
            callbacks=callbacks,
            execution_backend=execution_backend,
            runtime_kwargs=runtime_kwargs,
            resume_state=resume_state,
            effective_pre_bars=effective_pre_bars,
        )
        return BacktestRunResult(
            status=getattr(result, "status", "ok"),
            bars_processed=len(engine_bars),
            raw_result=result,
            process_next_bar_available=self.process_next_bar_available,
            resume_state=getattr(result, "resume_state", None),
        )

    def _to_engine_config(self, config: BacktestRunConfig) -> Any:
        configured_rounding = getattr(config, "qty_rounding_mode", None)
        qty_rounding = (
            "floor"
            if configured_rounding in {None, "none", "truncate"}
            else configured_rounding
        )
        engine_config = self._module.BacktestConfig(
            symbol=config.symbol,
            timeframe=config.timeframe,
            start_time=config.start_time,
            end_time=config.end_time,
            initial_capital=config.initial_capital,
            default_qty_type=config.default_qty_type,
            default_qty_value=config.default_qty_value,
            commission_type=config.commission_type,
            commission_value=config.commission_value,
            slippage=config.slippage,
            slippage_type=config.slippage_type,
            exit_matching=config.exit_matching,
            pyramiding=config.pyramiding,
            margin_long=config.margin_long,
            margin_short=config.margin_short,
            process_orders_on_close=config.process_orders_on_close,
            calc_on_order_fills=config.calc_on_order_fills,
            calc_on_every_tick=config.calc_on_every_tick,
            use_bar_magnifier=config.use_bar_magnifier,
            qty_step=config.qty_step,
            qty_rounding=qty_rounding,
            mintick=config.mintick,
            max_bars_back=config.max_bars_back,
            score_start_time=config.score_start_time,
            score_end_time=config.score_end_time,
            max_pre_bars=config.max_pre_bars,
            warmup_metadata=config.warmup_metadata,
            export_resume_state=config.export_resume_state,
            resume_validation_policy=config.resume_validation_policy,
            content_hash_enabled=config.content_hash_enabled,
            collect_events=config.collect_events,
            collect_order_lifecycle=config.collect_order_lifecycle,
            semantic_profile=getattr(config, "semantic_profile", None),
        )
        engine_config.exchange = config.exchange
        engine_config.market_type = config.market_type
        for name in (
            "execution_context",
            "admitted_manifest",
            "instrument_id",
            "generated_artifact",
            "bar_envelopes",
            "run_hash",
            "protocol_artifact_dir",
        ):
            value = getattr(config, name, None)
            if value is not None:
                object.__setattr__(engine_config, name, value)
        return engine_config

    def run_isolated(
        self,
        source: bytes,
        bars: list[Bar],
        config: BacktestRunConfig,
        resume_state: Any | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> BacktestRunResult:
        from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_artifact

        if not str(getattr(config, "semantic_profile", "") or "").strip():
            raise IsolatedRunError("semantic_profile is required")
        engine_bars = [self._to_engine_bar(bar) for bar in bars]
        engine_config = self._to_engine_config(config)
        if params is None:
            isolated = run_isolated_artifact(
                source,
                bars=engine_bars,
                config=engine_config,
                resume_state=resume_state,
                htf_bars=htf_bars,
            )
        else:
            isolated = run_isolated_artifact(
                source,
                bars=engine_bars,
                config=engine_config,
                resume_state=resume_state,
                htf_bars=htf_bars,
                params=params,
            )
        raw = isolated["raw_result"]
        return BacktestRunResult(
            status=getattr(raw, "status", "ok"),
            bars_processed=len(engine_bars),
            raw_result=raw,
            process_next_bar_available=self.process_next_bar_available,
            resume_state=getattr(raw, "resume_state", None),
        )

    @staticmethod
    def _progress_callbacks(progress_callback: Any, total: int) -> Any:
        from backtest_engine.models.callbacks import BacktestCallbacks

        last_emit_at = 0.0
        last_emit_index = -1
        step = max(1, total // 1000)

        def on_bar_end(_bar: Any, index: int, _state: Any) -> None:
            nonlocal last_emit_at, last_emit_index
            done = index + 1
            now = time.perf_counter()
            if (
                done >= total
                or done - last_emit_index >= step
                or now - last_emit_at >= 1.0
            ):
                last_emit_at = now
                last_emit_index = done
                progress_callback(done, total)

        return BacktestCallbacks(on_bar_end=on_bar_end)

    def _to_engine_bar(self, bar: Bar) -> Any:
        from openpine.adapters.bars import to_engine_bar

        return to_engine_bar(bar)
