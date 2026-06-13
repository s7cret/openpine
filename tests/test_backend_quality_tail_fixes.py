from __future__ import annotations

import builtins
import importlib
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import pandas as pd

from openpine.runtime.engine import _MinimalRuntimeConfig
from openpine.artifacts.store import ArtifactStore
from openpine.data.candle_storage import CandleStorage
from openpine.storage.manifests import ManifestStore
from openpine.storage import (
    BackendHealth,
    BacktestResultStore,
    BacktestRunRequest,
    MigrationRunner,
    SQLiteStorage,
)
from openpine.storage.adapters import DuckDBAnalyticsAdapter
from openpine.workers.pool import AggregationWorkerPool, FeatureWorkerPool
from openpine.compile import adapter as compile_adapter
from openpine.cli import data as cli_data
from openpine.registry.strategies import SQLiteStrategyRegistry
import openpine._compat.parquet as parquet_compat
import openpine.batch.runner as batch_runner
import openpine.storage.backtest_storage as backtest_storage
import openpine.storage.backup as backup_mod

cli_main = importlib.import_module("openpine.cli.main")


class _RecordingDuckConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        return SimpleNamespace(fetchone=lambda: (0,))


def test_duckdb_health_check_uses_bound_glob_parameter(tmp_path: Path) -> None:
    conn = _RecordingDuckConn()
    lake_dir = tmp_path / "lake'quote"
    lake_dir.mkdir()
    adapter = DuckDBAnalyticsAdapter(db_path=tmp_path / "analytics.duckdb", data_dir=lake_dir)
    adapter._duckdb_available = True
    adapter._conn = conn

    info = adapter.health_check()

    assert info.health == BackendHealth.AVAILABLE
    glob_call = next(call for call in conn.calls if "glob(" in call[0])
    assert glob_call[0] == "SELECT COUNT(*) FROM glob(?)"
    assert glob_call[1] == (f"{lake_dir}/**/*.parquet",)


class _FailingDuckCountConn:
    def execute(self, sql: str, params: tuple = ()):  # noqa: ARG002 - fake connection
        if "glob(" in sql:
            raise RuntimeError("glob failed")
        return SimpleNamespace(fetchone=lambda: (1,))


def test_duckdb_health_check_reports_parquet_count_error(tmp_path: Path) -> None:
    adapter = DuckDBAnalyticsAdapter(db_path=tmp_path / "analytics.duckdb", data_dir=tmp_path / "lake")
    adapter._duckdb_available = True
    adapter._conn = _FailingDuckCountConn()

    info = adapter.health_check()

    assert info.health == BackendHealth.AVAILABLE
    assert info.extra["parquet_files"] is None
    assert "glob failed" in info.extra["parquet_files_error"]


def test_candle_storage_rejects_partition_path_traversal(tmp_path: Path) -> None:
    storage = CandleStorage(
        data_root=tmp_path / "data",
        sqlite_path=tmp_path / "candles.sqlite",
    )

    with pytest.raises(ValueError, match="escapes candle storage root"):
        storage._partition_path(
            "binance",
            "spot",
            "../../../../escape",
            "trade",
            "1m",
            1704067200000,
        )


def test_candle_storage_rejects_symlink_escape_inside_root(tmp_path: Path) -> None:
    storage = CandleStorage(
        data_root=tmp_path / "data",
        sqlite_path=tmp_path / "candles.sqlite",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.candles_root.mkdir(parents=True)
    (storage.candles_root / "exchange=binance").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValueError, match="escapes candle storage root"):
        storage._partition_path(
            "binance",
            "spot",
            "BTCUSDT",
            "trade",
            "1m",
            1704067200000,
        )


def test_artifact_store_rejects_path_traversal_components(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    outside = tmp_path / "escape"

    with pytest.raises(ValueError, match="escapes artifact storage root"):
        store.save_artifact(
            artifact_id="../escape-artifact",
            source_id="source-1",
            params_hash="params",
            python_code="print('x')",
            compile_meta={},
        )
    with pytest.raises(ValueError, match="escapes artifact storage root"):
        store.get_artifact_path("artifact-1", "../escape-source")

    assert not outside.exists()


def test_artifact_store_rejects_symlink_escape_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "source-1").symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(root=root)

    with pytest.raises(ValueError, match="escapes artifact storage root"):
        store.get_artifact_path("artifact-1", "source-1")


def test_manifest_store_rejects_strategy_id_path_traversal(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifests")
    outside = tmp_path / "escape.json"

    with pytest.raises(ValueError, match="escapes manifest storage root"):
        store.save_manifest("../escape", {"ok": True})
    with pytest.raises(ValueError, match="escapes manifest storage root"):
        store.get_manifest("../escape")

    assert not outside.exists()


def test_manifest_store_rejects_symlink_escape_inside_root(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (manifest_dir / "strategy-1.json").symlink_to(outside)
    store = ManifestStore(manifest_dir)

    with pytest.raises(ValueError, match="escapes manifest storage root"):
        store.get_manifest("strategy-1")


def test_backtest_store_rejects_strategy_id_path_traversal(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="../escape-strategy",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)

    with pytest.raises(ValueError, match="escapes backtest storage root"):
        store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])

    assert not (config.data_dir / "escape-strategy").exists()
    storage.close()


def test_backtest_store_rejects_symlink_escape_inside_root(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    outside = tmp_path / "outside"
    outside.mkdir()
    (config.data_dir / "backtests").mkdir(parents=True, exist_ok=True)
    (config.data_dir / "backtests" / "strategy-1").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValueError, match="escapes backtest storage root"):
        store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
    storage.close()


def test_backtest_store_rejects_tmp_dir_symlink_escape(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    run_dir = config.data_dir / "backtests" / "strategy-1" / run_id
    tmp_dir = run_dir.with_suffix(".tmp")
    outside = tmp_path / "outside"
    outside.mkdir()
    tmp_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="escapes backtest storage root"):
            store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
        assert not (outside / "report.json").exists()
        assert not run_dir.is_symlink()
    finally:
        storage.close()


def test_backtest_store_cleans_published_artifacts_when_db_save_fails(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    run_dir = config.data_dir / "backtests" / "strategy-1" / run_id
    monkeypatch.setattr(
        store,
        "_save_result_db_records",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db write failed")),
    )
    try:
        with pytest.raises(RuntimeError, match="db write failed"):
            store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
        assert not run_dir.exists()
    finally:
        storage.close()


def test_backtest_store_restores_existing_artifacts_when_republish_db_save_fails(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    run_dir = config.data_dir / "backtests" / "strategy-1" / run_id
    store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
    marker = run_dir / "old-marker.txt"
    marker.write_text("old artifacts", encoding="utf-8")
    monkeypatch.setattr(
        store,
        "_save_result_db_records",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db write failed")),
    )
    try:
        with pytest.raises(RuntimeError, match="db write failed"):
            store.save_result(run_id, SimpleNamespace(final_equity=200.0), trades=[])
        assert marker.read_text(encoding="utf-8") == "old artifacts"
    finally:
        storage.close()


def test_backtest_store_restores_backup_when_new_run_dir_disappears(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    run_dir = config.data_dir / "backtests" / "strategy-1" / run_id
    store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
    marker = run_dir / "old-marker.txt"
    marker.write_text("old artifacts", encoding="utf-8")

    def remove_new_run_dir_and_fail(**_kwargs) -> None:
        shutil.rmtree(run_dir)
        raise RuntimeError("db write failed")

    monkeypatch.setattr(store, "_save_result_db_records", remove_new_run_dir_and_fail)
    try:
        with pytest.raises(RuntimeError, match="db write failed"):
            store.save_result(run_id, SimpleNamespace(final_equity=200.0), trades=[])
        assert marker.read_text(encoding="utf-8") == "old artifacts"
    finally:
        storage.close()


def test_backtest_store_removes_backup_after_successful_publish_over_existing_dir(monkeypatch, tmp_path: Path) -> None:
    from openpine.config.model import OpenPineConfig

    config = OpenPineConfig(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "openpine.sqlite",
    )
    monkeypatch.setattr(OpenPineConfig, "load", classmethod(lambda cls: config))
    storage = SQLiteStorage(config.sqlite_path)
    MigrationRunner().run_migrations(storage)
    store = BacktestResultStore(storage)
    request = BacktestRunRequest(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    run_parent = config.data_dir / "backtests" / "strategy-1"
    run_dir = run_parent / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "stale.txt").write_text("stale", encoding="utf-8")
    try:
        store.save_result(run_id, SimpleNamespace(final_equity=100.0), trades=[])
        assert not list(run_parent.glob(f"{run_id}.backup-*"))
        assert not (run_dir / "stale.txt").exists()
    finally:
        storage.close()


def test_minimal_runtime_config_does_not_share_mutable_state() -> None:
    first = _MinimalRuntimeConfig()
    second = _MinimalRuntimeConfig()

    first.diagnostics.append("diag")
    first.extra["flag"] = True

    assert second.diagnostics == []
    assert second.extra == {}


def test_parquet_read_requires_concrete_pyarrow_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parquet_compat, "pyarrow_available", lambda: True)
    monkeypatch.setattr(parquet_compat, "_pq", None)

    with pytest.raises(RuntimeError, match="pyarrow parquet backend is unavailable"):
        parquet_compat.read_dataframe(tmp_path / "bars.parquet")


def test_parquet_fallback_round_trips_without_pickle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parquet_compat, "pyarrow_available", lambda: False)
    monkeypatch.setattr(
        pd,
        "read_pickle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pickle used")),
    )
    path = tmp_path / "bars.parquet"
    expected = pd.DataFrame([{"time": 1, "close": 2.5}])

    parquet_compat.write_dataframe(expected, path)
    actual = parquet_compat.read_dataframe(path)

    assert actual.to_dict("records") == expected.to_dict("records")


def test_parquet_fallback_legacy_pickle_requires_trusted_opt_in(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parquet_compat, "pyarrow_available", lambda: False)
    monkeypatch.delenv("OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET", raising=False)
    path = tmp_path / "legacy.parquet"
    pd.DataFrame([{"time": 1}]).to_pickle(path)

    with pytest.raises(RuntimeError, match="legacy pickle parquet fallback loading is disabled"):
        parquet_compat.read_dataframe(path)


def test_parquet_fallback_rejects_non_dataframe_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parquet_compat, "pyarrow_available", lambda: False)
    monkeypatch.setattr(pd, "read_json", lambda *_args, **_kwargs: {"not": "dataframe"})

    with pytest.raises(RuntimeError, match="legacy pickle parquet fallback loading is disabled"):
        parquet_compat.read_dataframe(tmp_path / "bad.parquet")


def test_parquet_fallback_legacy_pickle_opt_in_loads_trusted_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parquet_compat, "pyarrow_available", lambda: False)
    monkeypatch.setenv("OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET", "1")
    path = tmp_path / "legacy.parquet"
    expected = pd.DataFrame([{"time": 1, "close": 2.5}])
    expected.to_pickle(path)

    actual = parquet_compat.read_dataframe(path)

    assert actual.to_dict("records") == expected.to_dict("records")


def test_worker_pool_job_type_sets_are_immutable() -> None:
    with pytest.raises(AttributeError):
        AggregationWorkerPool.JOB_TYPES.add("optimizer")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        FeatureWorkerPool.JOB_TYPES.add("backfill")  # type: ignore[attr-defined]


def test_subprocess_ast_json_invariant_returns_compile_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        compile_adapter,
        "_parse_with_pine2ast_subprocess",
        lambda **_kwargs: (None, None),
    )

    ast_json, error = compile_adapter._subprocess_ast_json_or_error(
        pine2ast_path=tmp_path / "pine2ast",
        src_path=tmp_path / "source.pine",
        source_text="plot(close)",
        profile=compile_adapter._profile_from_kwargs({"profile": "diagnostic"}),
        timeout=1,
        compile_meta={},
    )

    assert ast_json is None
    assert error is not None
    assert error.success is False
    assert "returned no result" in error.errors[0]


def test_cli_data_backfill_window_fails_closed_on_parser_invariant(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_data,
        "_parse_cli_ymd_ms",
        lambda _value, *, option_name: (None, None),
    )

    with pytest.raises(RuntimeError, match="--from date parser returned no timestamp"):
        cli_data._parse_data_backfill_window(
            from_date="2026-01-01",
            to_date="2026-01-02",
            now_ms=1,
        )

    calls = iter([(1, None), (None, None)])
    monkeypatch.setattr(
        cli_data,
        "_parse_cli_ymd_ms",
        lambda _value, *, option_name: next(calls),
    )
    with pytest.raises(RuntimeError, match="--to date parser returned no timestamp"):
        cli_data._parse_data_backfill_window(
            from_date="2026-01-01",
            to_date="2026-01-02",
            now_ms=1,
        )


def test_cli_data_backfill_fails_closed_on_incomplete_window(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_data,
        "_parse_data_backfill_window",
        lambda **_kwargs: (None, 1, None),
    )

    with pytest.raises(RuntimeError, match="incomplete bounds"):
        cli_data.data_backfill.callback(
            symbol="BTCUSDT",
            timeframe="1m",
            from_date="2026-01-01",
            to_date=None,
            exchange="binance",
            market="spot",
            price_type="trade",
            wait=False,
            timeout=1,
        )


def _minimal_compile_apis() -> compile_adapter._LibraryApis:
    return compile_adapter._LibraryApis(
        parse_code=lambda *_args, **_kwargs: SimpleNamespace(ast=object(), diagnostics=[], ok=True),
        parse_options=lambda **_kwargs: SimpleNamespace(),
        ast_to_json=lambda _ast: "{}",
        translate_ast=lambda *_args, **_kwargs: SimpleNamespace(
            code="def run(ctx):\n    return None\n",
            metadata={},
            source_map=[],
        ),
        versions={},
    )


def test_delete_strategy_ignores_missing_optional_backtest_tables(tmp_path: Path) -> None:
    registry = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1h",
        params={},
        name="minimal-delete-strategy",
    )

    registry.delete_strategy(strategy.strategy_id)

    with pytest.raises(KeyError):
        registry.get_strategy(strategy.strategy_id)
    registry.close()


def test_delete_strategy_rolls_back_when_backtest_dir_cleanup_fails(monkeypatch, tmp_path: Path) -> None:
    import shutil
    import openpine.config as config_mod

    registry = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    strategy = registry.register_strategy(
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1h",
        params={},
        name="cleanup-strategy",
    )
    data_dir = tmp_path / "data"
    bt_dir = data_dir / "backtests" / strategy.strategy_id
    bt_dir.mkdir(parents=True)
    monkeypatch.setattr(config_mod, "DEFAULT_CONFIG", SimpleNamespace(data_dir=data_dir))

    def fail_when_not_ignored(path, ignore_errors=False):  # noqa: ANN001 - fake shutil API
        assert Path(path) == bt_dir
        if ignore_errors:
            return
        raise OSError("permission denied")

    monkeypatch.setattr(shutil, "rmtree", fail_when_not_ignored)

    with pytest.raises(RuntimeError, match="backtest data directory"):
        registry.delete_strategy(strategy.strategy_id)
    registry.close()

    reloaded = SQLiteStrategyRegistry(db_path=tmp_path / "openpine.sqlite")
    try:
        assert reloaded.get_strategy(strategy.strategy_id).name == "cleanup-strategy"
    finally:
        reloaded.close()
    assert bt_dir.exists()


def test_compile_library_invariant_returns_compile_error(monkeypatch) -> None:
    monkeypatch.setattr(
        compile_adapter,
        "_parse_with_library_api",
        lambda **_kwargs: (None, None),
    )

    result = compile_adapter.SubprocessCompilerAdapter()._compile_with_library(
        _minimal_compile_apis(),
        "plot(close)",
        profile="diagnostic",
    )

    assert result.success is False
    assert "returned no AST" in result.errors[0]


def test_compile_subprocess_invariants_return_compile_errors(monkeypatch, tmp_path: Path) -> None:
    adapter = compile_adapter.SubprocessCompilerAdapter()
    profile_kwargs = {"profile": "diagnostic"}

    monkeypatch.setattr(compile_adapter, "_resolve_subprocess_tools", lambda: (None, []))
    missing_tools = adapter._compile_with_subprocess("plot(close)", **profile_kwargs)
    assert missing_tools.success is False
    assert "returned no tools" in missing_tools.errors[0]

    tools = compile_adapter._SubprocessTools(tmp_path / "pine2ast", tmp_path / "ast2python")
    source_path = tmp_path / "source.pine"
    source_path.write_text("plot(close)", encoding="utf-8")
    monkeypatch.setattr(compile_adapter, "_resolve_subprocess_tools", lambda: (tools, []))
    monkeypatch.setattr(compile_adapter, "_write_temp_pine_source", lambda _source: source_path)
    monkeypatch.setattr(compile_adapter, "_subprocess_ast_json_or_error", lambda **_kwargs: (None, None))
    missing_ast = adapter._compile_with_subprocess("plot(close)", **profile_kwargs)
    assert missing_ast.success is False
    assert "returned no AST JSON" in missing_ast.errors[0]

    ast_path = tmp_path / "ast.json"
    ast_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(compile_adapter, "_subprocess_ast_json_or_error", lambda **_kwargs: ("{}", None))
    monkeypatch.setattr(compile_adapter, "_translate_ast_with_subprocess", lambda **_kwargs: (None, None, ast_path))
    missing_code = adapter._compile_with_subprocess("plot(close)", **profile_kwargs)
    assert missing_code.success is False
    assert "returned no Python code" in missing_code.errors[0]


class _FetchAllRows:
    def fetchall(self) -> list[tuple[str]]:
        return []


class _RegistryConnWithOptionalFailure:
    def __init__(self, exc: sqlite3.OperationalError, fail_substring: str = "orders") -> None:
        self.exc = exc
        self.fail_substring = fail_substring
        self.committed = False

    def execute(self, sql: str, params: tuple = ()):
        if self.fail_substring in sql:
            raise self.exc
        return _FetchAllRows()

    def commit(self) -> None:
        self.committed = True


def _registry_with_conn(conn) -> SQLiteStrategyRegistry:
    registry = SQLiteStrategyRegistry.__new__(SQLiteStrategyRegistry)
    registry._conn = conn
    registry._mem = {"s1": object()}
    registry.get_strategy = lambda strategy_id: object()  # type: ignore[method-assign]
    return registry


def test_delete_strategy_only_ignores_missing_optional_tables() -> None:
    missing_conn = _RegistryConnWithOptionalFailure(sqlite3.OperationalError("no such table: orders"))
    _registry_with_conn(missing_conn).delete_strategy("s1")
    assert missing_conn.committed is True

    locked_backtests = _RegistryConnWithOptionalFailure(
        sqlite3.OperationalError("database is locked"),
        fail_substring="backtest_runs",
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _registry_with_conn(locked_backtests).delete_strategy("s1")

    locked_conn = _RegistryConnWithOptionalFailure(sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _registry_with_conn(locked_conn).delete_strategy("s1")

    locked_positions = _RegistryConnWithOptionalFailure(
        sqlite3.OperationalError("database is locked"),
        fail_substring="strategy_positions",
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _registry_with_conn(locked_positions).delete_strategy("s1")

    locked_trades = _RegistryConnWithOptionalFailure(
        sqlite3.OperationalError("database is locked"),
        fail_substring="strategy_trades",
    )
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _registry_with_conn(locked_trades).delete_strategy("s1")


def test_optional_duckdb_check_reports_missing_dependency(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "duckdb":
            raise ImportError("missing duckdb")
        return real_import(name, globals, locals, fromlist, level)

    messages: list[str] = []
    monkeypatch.setattr(builtins, "__import__", fake_import)

    cli_main._check_optional_duckdb(
        SimpleNamespace(duckdb_path="/tmp/openpine-missing.duckdb"),
        SimpleNamespace(print=messages.append),
    )

    assert messages == ["  [dim]  DuckDB not installed (optional)[/dim]"]


def test_backup_checkpoint_failure_is_not_silenced(monkeypatch, tmp_path: Path) -> None:
    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", fail_connect)

    with pytest.raises(RuntimeError, match="SQLite checkpoint failed") as exc_info:
        backup_mod._checkpoint_sqlite(tmp_path / "openpine.sqlite")

    assert "database is locked" in str(exc_info.value)
    assert "openpine.sqlite" in str(exc_info.value)


class _BusyCheckpointConn:
    def execute(self, _sql: str):
        return SimpleNamespace(fetchone=lambda: (1, 3, 2))

    def close(self) -> None:
        return None


class _BusyCloseFailCheckpointConn:
    def execute(self, _sql: str):
        return SimpleNamespace(fetchone=lambda: (1, 3, 2))

    def close(self) -> None:
        raise RuntimeError("close after busy")


class _CloseFailCheckpointConn:
    def execute(self, _sql: str):
        return SimpleNamespace(fetchone=lambda: (0, 3, 3))

    def close(self) -> None:
        raise RuntimeError("close boom")


class _NoRowCheckpointConn:
    def execute(self, _sql: str):
        return SimpleNamespace(fetchone=lambda: None)

    def close(self) -> None:
        return None


def test_backup_checkpoint_validates_busy_and_wraps_close_failures(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(backup_mod.sqlite3, "connect", lambda *_args, **_kwargs: _BusyCheckpointConn())
    with pytest.raises(RuntimeError, match="busy=1"):
        backup_mod._checkpoint_sqlite(tmp_path / "busy.sqlite")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", lambda *_args, **_kwargs: _BusyCloseFailCheckpointConn())
    with pytest.raises(RuntimeError, match="busy=1"):
        backup_mod._checkpoint_sqlite(tmp_path / "busy-close.sqlite")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", lambda *_args, **_kwargs: _NoRowCheckpointConn())
    backup_mod._checkpoint_sqlite(tmp_path / "no-row.sqlite")

    monkeypatch.setattr(backup_mod.sqlite3, "connect", lambda *_args, **_kwargs: _CloseFailCheckpointConn())
    with pytest.raises(RuntimeError, match="SQLite checkpoint close failed") as exc_info:
        backup_mod._checkpoint_sqlite(tmp_path / "close.sqlite")
    assert "close boom" in str(exc_info.value)


def test_backtest_artifact_row_fails_on_unreadable_parquet(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "trades.parquet"
    path.write_bytes(b"not parquet")
    monkeypatch.setattr(
        backtest_storage.parquet,
        "row_count",
        lambda _path: (_ for _ in ()).throw(ValueError("corrupt parquet")),
    )

    with pytest.raises(RuntimeError, match="Failed to count rows for trades artifact") as exc_info:
        backtest_storage.BacktestResultStore._artifact_db_row(
            run_id="run1",
            strategy_id="strategy1",
            artifact_type=backtest_storage.ARTIFACT_TYPE_TRADES,
            path=path,
            now=1,
        )

    assert "corrupt parquet" in str(exc_info.value)
    assert "trades.parquet" in str(exc_info.value)


def test_batch_revision_git_failure_is_visible_and_invalidates_meta(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    package_dir = repo / "pkg"
    (repo / ".git").mkdir(parents=True)
    package_dir.mkdir()
    module = ModuleType("revision_failure_mod")
    module.__file__ = str(package_dir / "__init__.py")
    monkeypatch.setitem(__import__("sys").modules, "revision_failure_mod", module)
    monkeypatch.setattr(batch_runner, "LIBRARY_NAMES", ("revision_failure_mod",))

    def fail_git(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

    monkeypatch.setattr(batch_runner.subprocess, "check_output", fail_git)

    revisions = batch_runner._get_library_revisions()
    assert revisions["revision_failure_mod"].startswith("error:CalledProcessError")

    meta_path = tmp_path / "run_meta.json"
    meta_path.write_text(
        __import__("json").dumps(
            {
                "schema_version": batch_runner.RUN_META_SCHEMA_VERSION,
                "compile_profile": batch_runner.PRODUCTION_COMPILE_PROFILE,
                "run_id": "run1",
                "batch_id": "batch1",
                "source_id": "source1",
                "strategy_or_indicator": "indicator",
                "calculation_window": {"from_ms": 1, "to_ms": 2},
                "export_window": {"from_ms": 1, "to_ms": 2},
                "library_revisions": revisions,
            }
        ),
        encoding="utf-8",
    )

    assert batch_runner._run_meta_valid(meta_path) is False


def test_batch_revision_missing_git_and_import_failure_are_visible(monkeypatch) -> None:
    monkeypatch.setattr(batch_runner.shutil, "which", lambda _name: None)
    with pytest.raises(FileNotFoundError, match="git executable not found"):
        batch_runner._git_executable()

    monkeypatch.setattr(batch_runner, "LIBRARY_NAMES", ("broken_import_mod",))

    def fail_import(name: str):
        if name == "broken_import_mod":
            raise RuntimeError("import exploded")
        return importlib.import_module(name)

    monkeypatch.setattr(batch_runner.importlib, "import_module", fail_import)
    revisions = batch_runner._get_library_revisions()
    assert revisions["broken_import_mod"].startswith("error:RuntimeError:import exploded")


def test_batch_revision_dependency_module_not_found_is_visible(monkeypatch) -> None:
    monkeypatch.setattr(batch_runner, "LIBRARY_NAMES", ("pkg_with_missing_dep",))

    def fail_import(name: str):
        if name == "pkg_with_missing_dep":
            raise ModuleNotFoundError("No module named 'missing_dep'", name="missing_dep")
        return importlib.import_module(name)

    monkeypatch.setattr(batch_runner.importlib, "import_module", fail_import)
    revisions = batch_runner._get_library_revisions()
    assert revisions["pkg_with_missing_dep"].startswith("error:ModuleNotFoundError")


def test_batch_revision_module_without_file_uses_version(monkeypatch) -> None:
    module = ModuleType("namespace_mod")
    setattr(module, "__version__", "namespace-1")
    monkeypatch.setitem(__import__("sys").modules, "namespace_mod", module)
    monkeypatch.setattr(batch_runner, "LIBRARY_NAMES", ("namespace_mod",))

    assert batch_runner._get_library_revisions() == {"namespace_mod": "namespace-1"}


def test_batch_revision_git_executable_is_forced_absolute(monkeypatch, tmp_path: Path) -> None:
    relative_git = Path("tools") / "git"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_runner.shutil, "which", lambda name: str(relative_git) if name == "git" else None)

    assert batch_runner._git_executable() == str((tmp_path / relative_git).resolve())


def test_batch_revision_uses_resolved_git_executable(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    package_dir = repo / "pkg"
    (repo / ".git").mkdir(parents=True)
    package_dir.mkdir()
    module = ModuleType("absolute_git_mod")
    module.__file__ = str(package_dir / "__init__.py")
    monkeypatch.setitem(__import__("sys").modules, "absolute_git_mod", module)
    monkeypatch.setattr(batch_runner, "LIBRARY_NAMES", ("absolute_git_mod",))
    monkeypatch.setattr(batch_runner.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    seen_cmds: list[list[str]] = []

    def fake_check_output(cmd, **_kwargs):
        seen_cmds.append(cmd)
        return b"abcdef12\n"

    monkeypatch.setattr(batch_runner.subprocess, "check_output", fake_check_output)

    assert batch_runner._get_library_revisions()["absolute_git_mod"] == "abcdef12"
    assert seen_cmds == [["/usr/bin/git", "rev-parse", "--short=8", "HEAD"]]
