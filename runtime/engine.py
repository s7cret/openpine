"""BacktestEngine adapter for OpenPine runtime execution."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpine.contracts import Bar
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
    data_provider: Any | None = None
    initial_capital: float = 10_000.0
    default_qty_type: str = "fixed"
    default_qty_value: float = 1.0
    commission_type: str = "none"
    commission_value: float = 0.0
    exit_matching: str = "fifo"
    pyramiding: int = 0
    qty_step: float | None = None
    qty_rounding_mode: str = "none"
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


class BacktestArtifactError(RuntimeError):
    """Raised when a compiled strategy artifact cannot be loaded safely."""


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

    artifact_dir = Path(str(artifact["artifact_dir"]))
    strategy_path = artifact_dir / "generated_strategy.py"
    if not strategy_path.exists():
        raise BacktestArtifactError(
            f"Artifact {artifact_id} has no generated_strategy.py. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        )

    module = _load_generated_module(strategy_path, source_id, artifact_id)
    strategy_class = _select_strategy_class(module, artifact.get("compile_meta", {}))
    if getattr(strategy_class, "__name__", "") in ("GeneratedStrategy", "GeneratedIndicator"):
        return _adapt_generated_strategy(strategy_class, symbol=symbol, timeframe=timeframe)
    return strategy_class


def load_generated_class_from_artifact(source_id: str, artifact_id: str) -> type:
    """Load the raw AST2Python generated class from a compiled artifact."""
    from openpine.artifacts import ArtifactStore

    store = ArtifactStore()
    try:
        artifact = store.get_artifact(artifact_id, source_id)
    except FileNotFoundError as exc:
        raise BacktestArtifactError(
            f"Compiled artifact not found: {artifact_id}. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        ) from exc

    artifact_dir = Path(str(artifact["artifact_dir"]))
    strategy_path = artifact_dir / "generated_strategy.py"
    if not strategy_path.exists():
        raise BacktestArtifactError(
            f"Artifact {artifact_id} has no generated_strategy.py. "
            f"Recompile the Pine source with: openpine pine compile <pine-name>"
        )

    module = _load_generated_module(strategy_path, source_id, artifact_id)
    return _select_strategy_class(module, artifact.get("compile_meta", {}))


def _load_generated_module(path: Path, source_id: str, artifact_id: str) -> Any:
    module_name = (
        "openpine_generated_"
        f"{source_id.replace('-', '_').replace(':', '_')}_"
        f"{artifact_id.replace('-', '_').replace(':', '_')}"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise BacktestArtifactError(f"Cannot import generated strategy artifact: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise BacktestArtifactError(
            f"Failed to import generated strategy artifact {path}: {exc}"
        ) from exc
    return module


def _select_strategy_class(module: Any, compile_meta: dict[str, Any]) -> type:
    candidates = [
        compile_meta.get("class_name"),
        "GeneratedStrategy",
        "GeneratedIndicator",
        "Strategy",
    ]
    for name in candidates:
        if isinstance(name, str):
            value = getattr(module, name, None)
            if isinstance(value, type) and callable(getattr(value, "_process_bar", None)):
                return value

    for value in vars(module).values():
        if isinstance(value, type) and callable(getattr(value, "_process_bar", None)):
            # Skip imported base classes that are not defined in this module
            if value.__module__ != getattr(module, "__name__", None):
                continue
            return value

    raise BacktestArtifactError(
        "Generated artifact does not expose a strategy class. "
        "Expected GeneratedStrategy or a class with _process_bar()."
    )


def _adapt_generated_strategy(
    generated_strategy_class: type,
    *,
    symbol: str,
    timeframe: str,
) -> type:
    try:
        import_library("backtest_engine")
        from backtest_engine.adapters.generated_strategy import (
            GeneratedStrategyAdapterOptions,
            make_generated_strategy_adapter,
        )
    except Exception as exc:
        raise BacktestArtifactError(
            "BacktestEngine generated-strategy adapter is unavailable. "
            "Install/repair the local backtest_engine package."
        ) from exc

    options = GeneratedStrategyAdapterOptions(symbol=symbol, timeframe=timeframe)
    return make_generated_strategy_adapter(generated_strategy_class, options=options)


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
    ) -> BacktestRunResult:
        """Run a strategy through the external BacktestEngine."""
        engine_bars = [self._to_engine_bar(bar) for bar in bars]
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
            exit_matching=config.exit_matching,
            pyramiding=config.pyramiding,
            qty_step=config.qty_step,
            qty_rounding=config.qty_rounding_mode,
        )
        setattr(engine_config, "exchange", config.exchange)
        setattr(engine_config, "market_type", config.market_type)
        setattr(engine_config, "data_provider", config.data_provider)
        engine = self._module.BacktestEngine(engine_config)
        runtime_kwargs = {
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "plot_from_ms": config.plot_from_ms,
            "plot_to_ms": config.plot_to_ms,
        }
        if progress_callback is not None:
            runtime_kwargs["progress_callback"] = progress_callback

        result = engine.run(
            strategy_class,
            params=params or {},
            bars=engine_bars,
            execution_backend=execution_backend,
            runtime_kwargs=runtime_kwargs,
        )
        return BacktestRunResult(
            status=getattr(result, "status", "ok"),
            bars_processed=len(engine_bars),
            raw_result=result,
            process_next_bar_available=self.process_next_bar_available,
        )

    def _to_engine_bar(self, bar: Bar) -> Any:
        # Handle both marketdata_provider.Bar (time, time_close) and adapter Bar (timestamp, close_time_ms)
        bar_time = getattr(bar, "timestamp", getattr(bar, "time", 0))
        bar_time_close = getattr(bar, "close_time_ms", getattr(bar, "time_close", 0))
        return self._module.Bar(
            time=int(bar_time),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
            time_close=int(bar_time_close),
        )
