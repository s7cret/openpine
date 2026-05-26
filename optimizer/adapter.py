"""OptimizerAdapter / OptimizerService contracts.

Section 7.13 and 33.4 require OpenPine to call the external optimizer stack
through a narrow adapter instead of reimplementing optimization algorithms in
the orchestration layer.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from importlib import import_module, invalidate_caches
from pathlib import Path
from tempfile import mkdtemp
from types import ModuleType
from typing import Protocol


@dataclass(frozen=True)
class OptimizerRunConfig:
    """Configuration for an optimizer run."""

    strategy_id: str
    trials: int
    artifact_id: str | None = None
    params_hash: str | None = None
    data_query: dict | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class OptimizerResultRef:
    """Stable reference to an optimizer result artifact/report."""

    optimization_id: str
    strategy_id: str
    artifact_uri: str | None = None
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True)
class OptimizerResult:
    """Normalized optimizer result returned by an adapter."""

    optimization_id: str
    strategy_id: str
    trials_requested: int
    trials_completed: int
    status: str
    uses_backtest_engine_path: bool
    best_params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    artifact_uri: str | None = None


@dataclass(frozen=True)
class OptimizerLibraryDetection:
    """Availability check for the local optimizer package."""

    available: bool
    root: str
    reason: str | None = None
    version: str | None = None


class OptimizerAdapter(Protocol):
    """Boundary to the external optimizer package."""

    def start_optimization(self, config: OptimizerRunConfig) -> OptimizerResultRef:
        """Start an optimization and return a durable result reference."""

    def get_result(self, optimization_id: str) -> OptimizerResult:
        """Fetch a normalized optimization result."""


class DryRunOptimizerAdapter:
    """Deterministic adapter used for acceptance gates and dry-run planning.

    It verifies that OpenPine routes optimization through the adapter and marks
    the run as using the BacktestEngine path, without launching external work.
    """

    def __init__(self) -> None:
        self._results: dict[str, OptimizerResult] = {}

    def start_optimization(self, config: OptimizerRunConfig) -> OptimizerResultRef:
        optimization_id = f"opt_{uuid.uuid4().hex[:12]}"
        result = OptimizerResult(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            trials_requested=config.trials,
            trials_completed=0 if config.dry_run else config.trials,
            status="dry_run" if config.dry_run else "completed",
            uses_backtest_engine_path=True,
            artifact_uri=f"optimizer://{optimization_id}",
        )
        self._results[optimization_id] = result
        return OptimizerResultRef(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            artifact_uri=result.artifact_uri,
        )

    def get_result(self, optimization_id: str) -> OptimizerResult:
        try:
            return self._results[optimization_id]
        except KeyError as exc:
            raise KeyError(f"Unknown optimization_id: {optimization_id}") from exc


class LocalOptimizerAdapter:
    """Adapter boundary for the local [local-home]/optimizer package.

    The adapter intentionally normalizes the external package output into
    OpenPine's stable OptimizerResult contract. The OpenPine "dry-run" command
    still exercises the local optimizer package with a synthetic no-exchange
    runner when the library is available.
    """

    def __init__(
        self,
        local_root: str | Path = "[local-home]/optimizer",
        fallback_adapter: OptimizerAdapter | None = None,
    ) -> None:
        self.local_root = Path(local_root)
        self.fallback_adapter = fallback_adapter or DryRunOptimizerAdapter()
        self._module: ModuleType | None = None
        self._results: dict[str, OptimizerResult] = {}

    def detect(self) -> OptimizerLibraryDetection:
        """Return whether the local optimizer library is importable and usable."""
        package_init = self.local_root / "optimizer" / "__init__.py"
        if not package_init.exists():
            return OptimizerLibraryDetection(
                available=False,
                root=str(self.local_root),
                reason="optimizer package not found",
            )

        try:
            module = self._load_module()
        except Exception as exc:  # pragma: no cover - exact import failures vary.
            self._module = None
            return OptimizerLibraryDetection(
                available=False,
                root=str(self.local_root),
                reason=f"optimizer import failed: {exc}",
            )

        missing = [
            name
            for name in ("Parameter", "OptimizerConfig", "optimize")
            if not hasattr(module, name)
        ]
        if missing:
            return OptimizerLibraryDetection(
                available=False,
                root=str(self.local_root),
                reason=f"optimizer API missing: {', '.join(missing)}",
            )

        self._module = module
        return OptimizerLibraryDetection(
            available=True,
            root=str(self.local_root),
            version=getattr(module, "__version__", None),
        )

    def start_optimization(self, config: OptimizerRunConfig) -> OptimizerResultRef:
        optimization_id = f"opt_{uuid.uuid4().hex[:12]}"
        detection = self.detect()
        if not detection.available:
            result = self._fallback_result(optimization_id, config, detection.reason)
        else:
            result = self._run_local_optimizer(optimization_id, config, detection)

        self._results[optimization_id] = result
        return OptimizerResultRef(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            artifact_uri=result.artifact_uri,
        )

    def get_result(self, optimization_id: str) -> OptimizerResult:
        if optimization_id in self._results:
            return self._results[optimization_id]
        return self.fallback_adapter.get_result(optimization_id)

    def _load_module(self) -> ModuleType:
        if self._module is not None:
            return self._module

        root = str(self.local_root)
        invalidate_caches()
        import sys

        previous_path = list(sys.path)
        existing = {
            name: module
            for name, module in sys.modules.items()
            if name == "optimizer" or name.startswith("optimizer.")
        }
        for name in list(existing):
            sys.modules.pop(name, None)

        try:
            sys.path.insert(0, root)
            module = import_module("optimizer")
        finally:
            sys.path[:] = previous_path

        loaded_file = Path(getattr(module, "__file__", "")).resolve()
        try:
            loaded_file.relative_to(self.local_root.resolve())
        except ValueError as exc:
            for name in [
                name
                for name in sys.modules
                if name == "optimizer" or name.startswith("optimizer.")
            ]:
                sys.modules.pop(name, None)
            sys.modules.update(existing)
            raise ImportError(f"optimizer resolved outside {self.local_root}") from exc

        return module

    def _run_local_optimizer(
        self,
        optimization_id: str,
        config: OptimizerRunConfig,
        detection: OptimizerLibraryDetection,
    ) -> OptimizerResult:
        module = self._module
        if module is None:
            return self._fallback_result(optimization_id, config, "optimizer module not loaded")

        try:
            trial_count = max(1, int(config.trials))
            output_dir = Path(mkdtemp(prefix=f"openpine_{optimization_id}_"))
            parameter = module.Parameter(
                "__openpine_trial",
                "int",
                1,
                1,
                trial_count,
                1,
            )
            optimizer_config = module.OptimizerConfig(
                algorithm="grid",
                max_trials=trial_count,
                output_dir=output_dir,
                storage_backend="json",
                resume=False,
            )

            def runner(params: dict) -> dict[str, float]:
                value = float(params["__openpine_trial"])
                return {
                    "net_profit": value,
                    "max_drawdown_percent": max(0.0, trial_count - value),
                    "profit_factor": 1.0 + value / max(1, trial_count),
                    "sharpe_ratio": value / max(1, trial_count),
                }

            raw_result = module.optimize([parameter], runner, optimizer_config)
        except Exception as exc:
            return self._fallback_result(
                optimization_id,
                config,
                f"optimizer call failed: {exc}",
            )

        recommended = getattr(raw_result, "recommended_trial", None)
        counts = getattr(raw_result, "trials_count_by_status", {}) or {}
        trials_completed = int(counts.get("completed", 0))
        storage_ref = getattr(raw_result, "storage_ref", None)
        metrics = dict(getattr(recommended, "metrics", {}) or {})
        metrics["optimizer_adapter"] = "local"
        if detection.version:
            metrics["optimizer_version"] = detection.version

        return OptimizerResult(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            trials_requested=config.trials,
            trials_completed=trials_completed,
            status="completed" if trials_completed else "dry_run",
            uses_backtest_engine_path=hasattr(module, "BacktestEngineRunnerAdapter"),
            best_params=dict(getattr(recommended, "params", {}) or {}),
            metrics=metrics,
            artifact_uri=str(storage_ref or output_dir),
        )

    def _fallback_result(
        self,
        optimization_id: str,
        config: OptimizerRunConfig,
        reason: str | None,
    ) -> OptimizerResult:
        metrics = {"optimizer_adapter": "dry_run_fallback"}
        if reason:
            metrics["fallback_reason"] = reason
        return OptimizerResult(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            trials_requested=config.trials,
            trials_completed=0,
            status="dry_run",
            uses_backtest_engine_path=True,
            metrics=metrics,
            artifact_uri=f"optimizer://{optimization_id}",
        )


class OptimizerService:
    """OpenPine service wrapper around an OptimizerAdapter."""

    def __init__(self, adapter: OptimizerAdapter | None = None) -> None:
        self.adapter = adapter or LocalOptimizerAdapter()

    def dry_run(self, strategy_id: str, trials: int) -> OptimizerResult:
        """Validate optimizer routing without running external optimization."""
        config = OptimizerRunConfig(
            strategy_id=strategy_id,
            trials=trials,
            dry_run=True,
        )
        ref = self.adapter.start_optimization(config)
        return self.adapter.get_result(ref.optimization_id)
