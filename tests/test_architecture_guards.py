from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import sys

from openpine.compile import CompileProfile, SubprocessCompilerAdapter
from openpine.optimizer import LocalOptimizerAdapter, OptimizerRunConfig, OptimizerService


def test_production_compile_profile_rejects_stub_flags() -> None:
    adapter = SubprocessCompilerAdapter(prefer_library=False)

    result = adapter.compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
        allow_unsupported_request_stubs=True,
    )

    assert not result.success
    assert "unsafe compile allowances" in result.errors[0]


def test_optimizer_dry_run_validation_is_not_production_result() -> None:
    result = OptimizerService().validate_config(strategy_id="s1", trials=1)

    assert result.status == "valid"
    assert not hasattr(result, "optimization_id")
    assert not hasattr(result, "trials_completed")


def test_optimizer_production_without_real_runner_fails_closed() -> None:
    service = OptimizerService()
    ref = service.adapter.start_optimization(OptimizerRunConfig(strategy_id="s1", trials=1))
    result = service.adapter.get_result(ref.optimization_id)

    assert result.status == "failed"
    assert result.trials_completed == 0
    assert result.metrics["failure_reason"]


@dataclass
class _FakeBacktestResult:
    net_profit: float
    max_drawdown_percent: float = 1.0
    profit_factor: float = 2.0
    sharpe_ratio: float = 1.5
    status: str = "completed"
    closed_trades: tuple[dict, ...] = ({"id": "t1"},)
    equity_curve: tuple[dict, ...] = ({"equity": 1000.0},)
    warnings: tuple = ()
    errors: tuple = ()


class _FakeEngine:
    def run(self, strategy, *, bars, params):
        return _FakeBacktestResult(net_profit=float(params["x"]))


class _FakeStrategy:
    pass


def _external_optimizer_module():
    openpine_root = Path(__file__).resolve().parents[1]
    original_path = list(sys.path)
    original_module = sys.modules.pop("optimizer", None)
    try:
        sys.path = [
            entry
            for entry in sys.path
            if Path(entry or ".").resolve() != openpine_root
        ]
        return import_module("optimizer")
    finally:
        sys.path = original_path
        if original_module is not None:
            sys.modules["optimizer"] = original_module


def test_optimizer_production_uses_real_backtest_runner(tmp_path) -> None:
    service = OptimizerService(adapter=LocalOptimizerAdapter(_external_optimizer_module()))
    ref = service.adapter.start_optimization(
        OptimizerRunConfig(
            strategy_id="s1",
            artifact_id="art1",
            data_query={"instrument": "binance/spot/BTCUSDT", "timeframe": "15m"},
            trials=1,
            parameters=(
                {
                    "name": "x",
                    "type": "int",
                    "default": 7,
                    "min": 7,
                    "max": 7,
                    "step": 1,
                },
            ),
            engine_factory=_FakeEngine,
            strategy=_FakeStrategy,
            bars=({"time": 1, "close": 1.0},),
            output_dir=tmp_path,
            storage_backend="json",
        )
    )
    result = service.adapter.get_result(ref.optimization_id)

    assert result.status == "completed"
    assert result.trials_completed == 1
    assert result.uses_backtest_engine_path is True
    assert result.best_params == {"x": 7}
    assert result.metrics["net_profit"] == 7.0
