"""BacktestEngine adapter for OpenPine runtime execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marketdata_provider.contracts import Bar

from openpine.integrations import import_library
from openpine.runtime.isolated_worker import IsolatedWorkerError, make_isolated_adapter


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


class _PineConstantNamespace:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def __getattr__(self, name: str) -> str:
        return f"{self._prefix}.{name}"


def load_strategy_class_from_artifact(
    source_id: str,
    artifact_id: str,
    *,
    symbol: str,
    timeframe: str,
) -> type:
    """Load the BacktestEngine-compatible strategy class from a compiled artifact."""
    from openpine.artifacts import ArtifactStore

    store = ArtifactStore()
    try:
        artifact = store.get_artifact(artifact_id, source_id)
    except FileNotFoundError as exc:
        raise BacktestArtifactError(
            f"Compiled artifact not found: {artifact_id}. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        ) from exc

    _validate_production_compile_artifact(artifact_id, artifact.get("compile_meta", {}))

    artifact_dir = Path(str(artifact["artifact_dir"]))
    strategy_path = artifact_dir / "generated_strategy.py"
    if not strategy_path.exists():
        raise BacktestArtifactError(
            f"Artifact {artifact_id} has no generated_strategy.py. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        )

    return make_isolated_adapter(strategy_path)


def load_generated_class_from_artifact(source_id: str, artifact_id: str) -> type:
    """Generated classes stay in the isolated worker. Gateway cannot import them."""
    from openpine.artifacts import ArtifactStore

    store = ArtifactStore()
    try:
        artifact = store.get_artifact(artifact_id, source_id)
    except FileNotFoundError as exc:
        raise BacktestArtifactError(
            f"Compiled artifact not found: {artifact_id}. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        ) from exc

    _validate_production_compile_artifact(artifact_id, artifact.get("compile_meta", {}))

    artifact_dir = Path(str(artifact["artifact_dir"]))
    strategy_path = artifact_dir / "generated_strategy.py"
    if not strategy_path.exists():
        raise BacktestArtifactError(
            f"Artifact {artifact_id} has no generated_strategy.py. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        )

    raise IsolatedWorkerError(
        "generated Python cannot be imported in the gateway process"
    )


def _validate_production_compile_artifact(
    artifact_id: str, compile_meta: dict[str, Any]
) -> None:
    """Reject failed, legacy, or diagnostic compile artifacts before import."""

    status = compile_meta.get("compile_status")
    if status != "OK":
        raise BacktestArtifactError(
            f"Artifact {artifact_id} is not a successful production compile "
            f"(compile_status={status!r}). Recompile before running."
        )
    if compile_meta.get("unsafe") is True:
        reasons = compile_meta.get("unsafe_reasons") or []
        raise BacktestArtifactError(
            f"Artifact {artifact_id} is marked unsafe for production runtime: {reasons!r}"
        )


class _DataBackedRuntime:
    """Lightweight runtime that provides data_provider for request.security.

    This is used when run_native_strategy() reads engine.config.runtime -
    without it, the fallback NoopRuntime has no data_provider and
    request.security raises PineRequestError.
    """

    def __init__(
        self, data_provider: Any, *, request_data_end_ms: int | None = None
    ) -> None:
        self.data_provider = data_provider
        self.chart_bars: list[Any] = []
        self.request_data_end_ms = request_data_end_ms
        self.request_depth: int = 0
        self.bar_index: int = -1
        self.config = _MinimalRuntimeConfig()

    def begin_bar(self, bar: Any, bar_index: int) -> None:
        self.bar_index = bar_index
        self.chart_bars.append(bar)

    def end_bar(self) -> None:
        pass


class _MinimalRuntimeConfig:
    """Minimal config shim for request.security."""

    supports_nested_security: bool = True
    strict_tv_parity: bool = False
    reference_history_mode: str = "unsupported"
    max_recalculations_per_bar: int = 16
    allow_incomplete_bar_time_close: bool = True
    diagnostics_as_errors: bool = False
    process_orders_on_close: bool | None = None

    def __init__(self) -> None:
        self.diagnostics = []
        self.extra = {}

    def emit_diagnostic(self, *args: Any, **kwargs: Any) -> None:
        pass


def _make_data_provider_runtime(
    data_provider: Any,
    *,
    request_data_end_ms: int | None = None,
) -> _DataBackedRuntime:
    """Create a runtime with data_provider for request.security support."""
    return _DataBackedRuntime(data_provider, request_data_end_ms=request_data_end_ms)


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
        engine_bars = [self._to_engine_bar(bar) for bar in bars]
        qty_rounding = (
            "floor"
            if getattr(config, "qty_rounding_mode", None) == "truncate"
            else getattr(config, "qty_rounding_mode", None) or "floor"
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
