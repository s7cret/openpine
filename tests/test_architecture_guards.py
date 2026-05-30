from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
import sys
import tomllib

from openpine.compile import CompileProfile, SubprocessCompilerAdapter
from openpine.optimizer import LocalOptimizerAdapter, OptimizerRunConfig, OptimizerService


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_EXCLUDES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "docs",
    "reports",
    "tests",
    "openpine.egg-info",
}
CANONICAL_MARKETDATA_CONTRACTS = {"Bar", "BarQuery", "Timeframe"}
FORBIDDEN_MARKETDATA_IMPORTS = (
    "marketdata_provider.core",
    "marketdata_provider.exchanges",
    "marketdata_provider.streaming",
    "marketdata_provider.timeframes",
)


def _production_python_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if not set(path.relative_to(ROOT).parts) & PRODUCTION_EXCLUDES
    )


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_cli_is_package_entrypoint_not_root_module() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert not (ROOT / "cli.py").exists()
    assert (ROOT / "cli" / "__init__.py").is_file()
    assert (ROOT / "cli" / "main.py").is_file()
    assert pyproject["project"]["scripts"]["openpine"] == "openpine.cli.main:main"

    import openpine.cli as cli_pkg

    assert hasattr(cli_pkg, "__path__")
    assert cli_pkg.cli.name == "cli"


def test_pyproject_includes_all_package_directories() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared_packages = set(pyproject["tool"]["setuptools"]["packages"])
    package_dirs = {
        "openpine" if path == ROOT else "openpine." + path.relative_to(ROOT).as_posix().replace("/", ".")
        for path in ROOT.rglob("*")
        if path.is_dir()
        and (path / "__init__.py").is_file()
        and not set(path.relative_to(ROOT).parts) & PRODUCTION_EXCLUDES
    }

    assert package_dirs <= declared_packages


def test_no_executable_legacy_scripts_remain() -> None:
    scripts_dir = ROOT / "scripts"
    script_files = (
        sorted(
            path.relative_to(ROOT)
            for path in scripts_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        )
        if scripts_dir.exists()
        else []
    )

    assert script_files == []


def test_production_source_does_not_mutate_sys_path_or_hardcode_home_paths() -> None:
    sys_path_mutations: list[str] = []
    home_paths: list[str] = []

    for path in _production_python_files():
        module = _parse(path)
        relative_path = path.relative_to(ROOT).as_posix()

        for node in ast.walk(module):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "path"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "sys"
            ):
                sys_path_mutations.append(f"{relative_path}:{node.lineno}")

            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "/home/" in node.value
            ):
                home_paths.append(f"{relative_path}:{node.lineno}")

    assert sys_path_mutations == []
    assert home_paths == []


def test_production_source_does_not_use_notimplemented_control_flow() -> None:
    offenders: list[str] = []
    for path in _production_python_files():
        module = _parse(path)
        relative_path = path.relative_to(ROOT).as_posix()
        for node in ast.walk(module):
            if isinstance(node, ast.ExceptHandler):
                handled = node.type
                if isinstance(handled, ast.Name) and handled.id == "NotImplementedError":
                    offenders.append(f"{relative_path}:{node.lineno}")

    assert offenders == []


def test_openpine_production_does_not_define_duplicate_marketdata_contracts() -> None:
    duplicate_definitions: list[str] = []

    for path in _production_python_files():
        module = _parse(path)
        relative_path = path.relative_to(ROOT).as_posix()

        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef) and node.name in CANONICAL_MARKETDATA_CONTRACTS:
                duplicate_definitions.append(f"{relative_path}:{node.lineno}:{node.name}")

    assert duplicate_definitions == []


def test_openpine_uses_marketdata_provider_stable_api_only() -> None:
    violations: list[str] = []

    for path in _production_python_files():
        module = _parse(path)
        relative_path = path.relative_to(ROOT).as_posix()

        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(FORBIDDEN_MARKETDATA_IMPORTS):
                    violations.append(f"{relative_path}:{node.lineno}:{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_MARKETDATA_IMPORTS):
                        violations.append(f"{relative_path}:{node.lineno}:{alias.name}")

    assert violations == []


def test_data_orchestrator_has_no_legacy_candle_storage_boundary() -> None:
    source = (ROOT / "data" / "orchestrator.py").read_text(encoding="utf-8")

    assert "CandleStorage" not in source
    assert "read_candles" not in source
    assert "write_candles" not in source
    assert "_LegacyCandleStorageAdapter" not in source


def test_data_inspect_cli_uses_orchestrator_boundary() -> None:
    source = (ROOT / "cli" / "main.py").read_text(encoding="utf-8")
    inspect_start = source.index('@data.command("inspect")')
    doctor_start = source.index('@data.command("doctor")')
    inspect_source = source[inspect_start:doctor_start]

    assert "DataOrchestrator" in inspect_source
    assert "CandleStorage" not in inspect_source
    assert "read_candles" not in inspect_source
    assert "pd.read_parquet" not in inspect_source


def test_data_package_does_not_export_legacy_planner_models() -> None:
    import openpine.data as data

    exported = set(data.__all__)

    assert not {name for name in exported if name.startswith("Legacy")}
    assert "CandleStorage" not in exported
    assert "DataPlanner" not in exported
    assert "DataRequirement" not in exported
    assert "AggregationRequirement" not in exported
    assert "DataPlan" not in exported
    assert "EnsureDataResult" not in exported


def test_data_orchestrator_has_no_placeholder_planning_api() -> None:
    source = (ROOT / "data" / "orchestrator.py").read_text(encoding="utf-8")

    assert "Placeholder" not in source
    assert "build_data_plan" not in source
    assert "ensure_data" not in source
    assert "schedule_backfill" not in source
    assert "list_manifests" not in source
    assert "return []" not in source


def test_parquet_storage_has_no_jsonl_fallback() -> None:
    source = (ROOT / "storage" / "adapters.py").read_text(encoding="utf-8")

    assert "JSONL_SUFFIX" not in source
    assert "_write_ohlcv_jsonl" not in source
    assert "_read_ohlcv_jsonl" not in source
    assert "write to JSONL" not in source
    assert "json.dumps" not in source
    assert "json.loads" not in source


def test_openpine_has_single_data_planning_model_family() -> None:
    production_files = [
        ROOT / "contracts" / "__init__.py",
        ROOT / "data" / "models.py",
        ROOT / "data" / "planner.py",
    ]
    definitions: list[str] = []
    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions.extend(
            f"{path.relative_to(ROOT)}:{node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name in {"DataRequirement", "AggregationRequirement", "DataPlan"}
        )

    assert definitions == [
        "data/planner.py:DataRequirement",
        "data/planner.py:AggregationRequirement",
        "data/planner.py:DataPlan",
    ]


def test_batch_runner_does_not_parse_tv_corpus_csv_directly() -> None:
    source = (ROOT / "batch" / "runner.py").read_text(encoding="utf-8")

    assert "import csv" not in source
    assert "DictReader" not in source
    assert "read_csv(" not in source
    assert "def read_chart" not in source
    assert "def load_manifest" not in source


def test_batch_runner_uses_single_strategy_export_boundary() -> None:
    source = (ROOT / "batch" / "runner.py").read_text(encoding="utf-8")

    assert "export_strategy_result" in source
    assert "export_trades(" not in source
    assert "export_equity_curve(" not in source


def test_export_package_has_separate_writer_modules() -> None:
    export_root = ROOT / "export"
    expected_modules = {
        "__init__.py",
        "batch.py",
        "equity.py",
        "json.py",
        "plots.py",
        "schemas.py",
        "trades.py",
        "window.py",
    }
    actual_modules = {path.name for path in export_root.glob("*.py")}
    init_tree = ast.parse((export_root / "__init__.py").read_text(encoding="utf-8"))
    init_definitions = [
        node
        for node in ast.walk(init_tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    ]

    assert expected_modules <= actual_modules
    assert init_definitions == []


def test_backtest_run_config_does_not_carry_engine_data_provider() -> None:
    from dataclasses import fields

    from openpine.runtime.engine import BacktestRunConfig

    names = {field.name for field in fields(BacktestRunConfig)}
    source = (ROOT / "runtime" / "engine.py").read_text(encoding="utf-8")

    assert "data_provider" not in names
    assert 'setattr(engine_config, "data_provider"' not in source


def test_production_compile_profile_rejects_stub_flags() -> None:
    adapter = SubprocessCompilerAdapter(prefer_library=False)

    result = adapter.compile(
        "//@version=6\nindicator('x')\nplot(close)\n",
        profile=CompileProfile.production(),
        allow_unsupported_request_stubs=True,
    )

    assert not result.success
    assert "unsafe compile allowances" in result.errors[0]


def test_openpine_compile_profile_reexports_ast2python_contract() -> None:
    from ast2python.profiles import CompileProfile as AstCompileProfile

    assert CompileProfile is AstCompileProfile


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
            params_hash="params1",
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
    assert result.artifact_id == "art1"
    assert result.params_hash == "params1"
    assert result.data_query == {
        "instrument": "binance/spot/BTCUSDT",
        "timeframe": "15m",
    }
    assert result.best_params == {"x": 7}
    assert result.metrics["net_profit"] == 7.0
    assert result.metrics["optimizer_result_type"] == "OptimizerRunResult"
    assert result.metrics["runner_adapter"] == "BacktestEngineRunnerAdapter"
    assert result.metrics["runner_request_contract"] == "openpine.optimizer_runner.v1"
    assert result.trial_status_counts == {"completed": 1, "failed": 0}
    assert len(result.trial_metadata) == 1
    assert result.trial_metadata[0]["status"] == "completed"
    assert result.trial_metadata[0]["params_hash"]
    assert "runner_fingerprint" in result.trial_metadata[0]
    assert ref.artifact_uri == result.artifact_uri


class _FakeFailingEngine:
    def run(self, strategy, *, bars, params):
        return _FakeBacktestResult(
            net_profit=0.0,
            status="failed",
            errors=("forced failure",),
        )


def test_optimizer_failed_trials_are_not_reported_completed(tmp_path) -> None:
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
            engine_factory=_FakeFailingEngine,
            strategy=_FakeStrategy,
            bars=({"time": 1, "close": 1.0},),
            output_dir=tmp_path,
            storage_backend="json",
        )
    )
    result = service.adapter.get_result(ref.optimization_id)

    assert result.status == "failed"
    assert result.trials_completed == 0
    assert result.uses_backtest_engine_path is True
    assert result.best_params == {}
    assert result.trial_status_counts == {"failed": 1, "completed": 0}
    assert result.trial_metadata[0]["status"] == "failed"
    assert result.trial_metadata[0]["error_message"]
