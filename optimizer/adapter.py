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
from types import ModuleType
from typing import Literal, Protocol


@dataclass(frozen=True)
class OptimizerRunConfig:
    """Configuration for an optimizer run."""

    strategy_id: str
    trials: int
    artifact_id: str | None = None
    params_hash: str | None = None
    data_query: dict | None = None


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
class DryRunValidationResult:
    """Result of explicit config validation, never a production optimizer result."""

    strategy_id: str
    trials_requested: int
    status: Literal["valid", "invalid"]
    reason: str | None = None


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


class LocalOptimizerAdapter:
    """Adapter boundary for the installed optimizer package.

    The adapter intentionally normalizes the external package output into
    OpenPine's stable OptimizerResult contract.
    """

    def __init__(self) -> None:
        self._module: ModuleType | None = None
        self._results: dict[str, OptimizerResult] = {}

    def detect(self) -> OptimizerLibraryDetection:
        """Return whether the local optimizer library is importable and usable."""
        try:
            module = self._load_module()
        except Exception as exc:  # pragma: no cover - exact import failures vary.
            self._module = None
            return OptimizerLibraryDetection(
                available=False,
                root="installed-package",
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
                root="installed-package",
                reason=f"optimizer API missing: {', '.join(missing)}",
            )

        self._module = module
        return OptimizerLibraryDetection(
            available=True,
            root="installed-package",
            version=getattr(module, "__version__", None),
        )

    def start_optimization(self, config: OptimizerRunConfig) -> OptimizerResultRef:
        optimization_id = f"opt_{uuid.uuid4().hex[:12]}"
        detection = self.detect()
        if not detection.available:
            result = self._failed_result(optimization_id, config, detection.reason)
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
        raise KeyError(f"Unknown optimization_id: {optimization_id}")

    def _load_module(self) -> ModuleType:
        if self._module is not None:
            return self._module

        invalidate_caches()
        return import_module("optimizer")

    def _run_local_optimizer(
        self,
        optimization_id: str,
        config: OptimizerRunConfig,
        detection: OptimizerLibraryDetection,
    ) -> OptimizerResult:
        module = self._module
        if module is None:
            return self._failed_result(optimization_id, config, "optimizer module not loaded")

        if not config.artifact_id or not config.data_query:
            return self._failed_result(
                optimization_id,
                config,
                "production optimization requires artifact_id and data_query",
            )

        try:
            runner_cls = getattr(module, "BacktestEngineRunnerAdapter", None)
            if runner_cls is None:
                return self._failed_result(
                    optimization_id,
                    config,
                    "optimizer BacktestEngineRunnerAdapter is unavailable",
                )
            return self._failed_result(
                optimization_id,
                config,
                "OpenPine real optimizer runner wiring is not implemented yet",
            )
        except Exception as exc:
            return self._failed_result(
                optimization_id,
                config,
                f"optimizer call failed: {exc}",
            )

    def _failed_result(
        self,
        optimization_id: str,
        config: OptimizerRunConfig,
        reason: str | None,
    ) -> OptimizerResult:
        metrics = {"optimizer_adapter": "local"}
        if reason:
            metrics["failure_reason"] = reason
        return OptimizerResult(
            optimization_id=optimization_id,
            strategy_id=config.strategy_id,
            trials_requested=config.trials,
            trials_completed=0,
            status="failed",
            uses_backtest_engine_path=False,
            metrics=metrics,
        )


class OptimizerService:
    """OpenPine service wrapper around an OptimizerAdapter."""

    def __init__(self, adapter: OptimizerAdapter | None = None) -> None:
        self.adapter = adapter or LocalOptimizerAdapter()

    def validate_config(self, strategy_id: str, trials: int) -> DryRunValidationResult:
        """Validate optimizer CLI inputs without returning a production result."""
        if trials < 1:
            return DryRunValidationResult(
                strategy_id=strategy_id,
                trials_requested=trials,
                status="invalid",
                reason="trials must be >= 1",
            )
        return DryRunValidationResult(
            strategy_id=strategy_id,
            trials_requested=trials,
            status="valid",
        )
