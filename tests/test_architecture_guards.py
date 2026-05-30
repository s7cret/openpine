from __future__ import annotations

from openpine.compile import CompileProfile, SubprocessCompilerAdapter
from openpine.optimizer import OptimizerRunConfig, OptimizerService


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
