from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner
from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.batch import runner, tv_corpus
from openpine.batch.tv_corpus import ChartExport, ExportEntry
from openpine.cli import runtime_helpers
from openpine.cli.config import config as config_group
from openpine.cli.reports import reports as reports_group
from openpine.cli.storage import storage as storage_group


class _Console:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *parts, **_kwargs) -> None:
        self.messages.append(" ".join(str(part) for part in parts))


def _write_chart(path: Path, *, times: tuple[int, ...] = (60, 120)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["time,open,high,low,close,Volume"]
    for idx, value in enumerate(times, start=1):
        rows.append(f"{value},{idx},{idx + 1},{idx - 1},{idx + 0.5},{idx * 10}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _chart(root: Path, timeframe: str = "15m", name: str | None = None) -> ChartExport:
    name = name or f"chart_{timeframe}.csv"
    path = _write_chart(root / name)
    return ChartExport(timeframe=timeframe, path=path, bars=2, start_ms=60_000, end_ms=120_000)


def _entry(root: Path, *, kind: str = "strategy", charts: tuple[ChartExport, ...] | None = None) -> ExportEntry:
    root.mkdir(parents=True, exist_ok=True)
    pine = root / "source.pine"
    pine.write_text(f'{kind}("demo")\n', encoding="utf-8")
    return ExportEntry(
        export_id=46,
        folder=root.name,
        kind=kind,
        source_group="grp",
        root=root,
        pine_path=pine,
        charts=charts or (_chart(root),),
    )


def _bar(time_ms: int = 60_000) -> Bar:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=True,
    )


def _runner_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    values = dict(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        root=tmp_path,
        manifest=tmp_path / "manifest.csv",
        calculation_from="60",
        calculation_to="120",
        _calculation_to_by_timeframe={},
        provider_only_bars=True,
        timeframe=None,
        progress_every=0,
        qty_step=0.001,
        qty_rounding_mode="truncate",
        skip_completed=False,
        phase="run",
        stop_on_error=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_runner_revision_registry_and_calculation_bar_leftovers(monkeypatch, tmp_path: Path):
    fake_module_file = tmp_path / "pkg" / "module.py"
    fake_module_file.parent.mkdir()

    def fake_import_module(name: str):
        return SimpleNamespace(__file__=str(fake_module_file), __version__=f"{name}-rev")

    # ``runner.importlib`` is the stdlib importlib module, so keep this patch
    # scoped to the revision probe and restore it before any other imports.
    with monkeypatch.context() as scoped:
        scoped.setattr(runner.importlib, "import_module", fake_import_module)
        revisions = runner._get_library_revisions()
    assert revisions == {name: f"{name}-rev" for name in runner.LIBRARY_NAMES}

    import openpine.pine.registry as pine_registry_mod
    import openpine.registry as strategy_registry_mod

    class FakeSourceRegistry:
        pass

    class FakeStrategyRegistry:
        pass

    monkeypatch.setattr(pine_registry_mod, "SQLitePineSourceRegistry", FakeSourceRegistry)
    monkeypatch.setattr(strategy_registry_mod, "SQLiteStrategyRegistry", FakeStrategyRegistry)
    assert isinstance(runner.load_source_registry(), FakeSourceRegistry)
    assert isinstance(runner.load_strategy_registry(), FakeStrategyRegistry)

    entry = _entry(tmp_path / "missing_calc_from")
    with pytest.raises(ValueError, match="calculation-from"):
        runner.load_calculation_bars(
            entry,
            entry.charts[0],
            _runner_args(tmp_path, calculation_from=None),
            {},
        )

    class EmptyOrchestrator:
        def __init__(self, provider=None):
            self.provider = provider

        def load_bars(self, _query):
            return SimpleNamespace(bars=[])

    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", EmptyOrchestrator)
    monkeypatch.setattr(
        "openpine.data.provider_adapter.create_local_marketdata_provider_adapter",
        lambda: SimpleNamespace(_provider=SimpleNamespace(last_fetch_info={"source": "empty"})),
    )
    runner.BAR_CACHE.clear()
    with pytest.raises(RuntimeError, match="no calculation bars"):
        runner.load_calculation_bars(
            entry,
            entry.charts[0],
            _runner_args(tmp_path, symbol="EMPTYUSDT"),
            {},
        )

    class OneBarOrchestrator:
        def __init__(self, provider=None):
            self.provider = provider

        def load_bars(self, _query):
            return SimpleNamespace(bars=[_bar(60_000)])

    overlay_seen: list[float] = []
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", OneBarOrchestrator)
    monkeypatch.setattr(
        runner,
        "load_visible_bars_by_time",
        lambda **_kwargs: (
            {60_000: {"open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5, "volume": 99.0}},
            {"cache": "fake-tv"},
        ),
    )

    def fake_overlay(*, provider_bars, chart, symbol, exchange, market_type):
        overlay_seen.append(float(provider_bars[0].open))
        return provider_bars

    monkeypatch.setattr(runner, "_merge_tv_visible_bars", fake_overlay)
    runner.BAR_CACHE.clear()
    bars, meta = runner.load_calculation_bars(
        entry,
        entry.charts[0],
        _runner_args(tmp_path, provider_only_bars=False, symbol="PATCHUSDT"),
        {},
    )
    assert bars[0].open == 9.0
    assert overlay_seen == [9.0]
    assert meta["tv_corpus_patched_bars"] == 1
    assert meta["bar_source"] == "provider_with_tv_visible_overlay"


def test_runner_bar_index_meta_completion_and_timeframe_skip_branches(monkeypatch, tmp_path: Path):
    chart = ChartExport("15m", tmp_path / "unused.csv", 3, 1_000, 3_000)
    assert runner._infer_tv_bar_index_offset(chart, [SimpleNamespace(time=999)]) == (0, None)

    monkeypatch.setattr(tv_corpus, "read_chart", lambda _path: pd.DataFrame())
    assert runner._infer_tv_bar_index_offset(chart, [SimpleNamespace(time=1_000)]) == (0, None)

    monkeypatch.setattr(
        tv_corpus,
        "read_chart",
        lambda _path: pd.DataFrame({"BAR_WITH_NA": [1.0, None]}),
    )
    assert runner._infer_tv_bar_index_offset(
        chart, [SimpleNamespace(time=1_000), SimpleNamespace(time=2_000)]
    ) == (0, None)

    monkeypatch.setattr(
        tv_corpus,
        "read_chart",
        lambda _path: pd.DataFrame({"BAR_FLOAT": [1.5, 2.5]}),
    )
    assert runner._infer_tv_bar_index_offset(
        chart, [SimpleNamespace(time=1_000), SimpleNamespace(time=2_000)]
    ) == (0, None)

    monkeypatch.setattr(
        tv_corpus,
        "read_chart",
        lambda _path: pd.DataFrame(
            {"BAR_A": [10, 11, 12], "BAR_B": [20, 21, 22]}
        ),
    )
    offset, meta = runner._infer_tv_bar_index_offset(
        chart,
        [SimpleNamespace(time=1_000), SimpleNamespace(time=2_000), SimpleNamespace(time=3_000)],
    )
    assert offset == 0
    assert meta == {"status": "ambiguous", "candidates": [("BAR_A", 10), ("BAR_B", 20)]}

    assert runner._infer_tv_bar_index_offset_from_periodic_na(
        pd.DataFrame({"too_short_period": [None, None, None, 1]}), 0
    ) == (0, None)
    assert runner._infer_tv_bar_index_offset_from_periodic_na(
        pd.DataFrame(
            {
                "even_na": [None, 1, None, 1, None, 1],
                "odd_na": [1, None, 1, None, 1, None],
            }
        ),
        0,
    ) == (0, None)

    valid_meta = {
        "schema_version": runner.RUN_META_SCHEMA_VERSION,
        "compile_profile": runner.PRODUCTION_COMPILE_PROFILE,
        "run_id": "run",
        "batch_id": "batch",
        "source_id": "pine",
        "strategy_or_indicator": "strategy",
        "calculation_window": {"from_ms": 1, "to_ms": 2},
        "export_window": {"from_ms": 1, "to_ms": 2},
        "library_revisions": {name: "rev" for name in runner.LIBRARY_NAMES},
    }
    meta_path = tmp_path / "run_meta.json"
    for patch in (
        {"run_id": ""},
        {"calculation_window": {"from_ms": 2, "to_ms": 1}},
        {"export_window": {"from_ms": 2, "to_ms": 1}},
        {"library_revisions": []},
    ):
        payload = {**valid_meta, **patch}
        meta_path.write_text(json.dumps(payload), encoding="utf-8")
        assert runner._run_meta_valid(meta_path) is False

    chart_15 = _chart(tmp_path / "entry_skip", "15m", "chart_15.csv")
    chart_1h = _chart(tmp_path / "entry_skip", "1h", "chart_1h.csv")
    entry = _entry(tmp_path / "entry_skip", charts=(chart_15, chart_1h))
    status_path = entry.root / "openpine_outputs" / "openpine_batch_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text('{"phase":"run","status":"ok","runs":[]}', encoding="utf-8")
    assert runner.completed_for_selection(
        entry, argparse.Namespace(skip_completed=True, phase="run", timeframe="5m")
    ) is False

    registered_timeframes: list[str] = []
    monkeypatch.setattr(
        runner,
        "ensure_strategy_instance",
        lambda _entry, _source, _artifact_id, timeframe: (
            registered_timeframes.append(timeframe) or f"sid-{timeframe}",
            True,
        ),
    )
    registered = runner._register_entry_strategies(
        entry,
        SimpleNamespace(id="pine"),
        "artifact",
        argparse.Namespace(timeframe="15"),
    )
    assert registered_timeframes == ["15m"]
    assert registered == [{"timeframe": "15m", "strategy_id": "sid-15m", "created": True}]

    def explode_strategy(*_args, **_kwargs):
        raise RuntimeError("runtime boom")

    monkeypatch.setattr(runner, "run_strategy", explode_strategy)
    runs = runner._run_entry_charts(
        entry=entry,
        source=SimpleNamespace(id="pine"),
        artifact_id="artifact",
        args=argparse.Namespace(timeframe="15"),
        status={"source_id": "pine", "artifact_id": "artifact"},
        batch_id="batch",
        library_revisions={name: "rev" for name in runner.LIBRARY_NAMES},
    )
    assert [run["timeframe"] for run in runs] == ["15m"]
    assert runs[0]["status"] == "run_error"
    assert runs[0]["error_type"] == "RuntimeError"
    assert (entry.root / "openpine_outputs" / "15m" / "run_meta.json").exists()
    assert not (entry.root / "openpine_outputs" / "1h").exists()

    resolved = runner.resolve_calculation_to_by_timeframe(
        [entry], argparse.Namespace(phase="run", calculation_to=None, timeframe="15")
    )
    assert set(resolved) == {"15m"}


def test_tv_corpus_time_manifest_visible_bar_and_filter_leftovers(monkeypatch, tmp_path: Path):
    huge_chart = _write_chart(
        tmp_path / "huge_time.csv",
        times=(2_000_000_000_001, 2_000_000_900_001),
    )
    huge_df = tv_corpus.read_chart(huge_chart)
    assert int(huge_df["bar_time"].iloc[0]) == 2_000_000_000_001

    assert tv_corpus.infer_timeframe(
        tmp_path / "plain.csv", pd.DataFrame({"bar_time": [0, 900_000, 1_800_000]})
    ) == "15m"
    assert tv_corpus.infer_timeframe(
        tmp_path / "plain.csv", pd.DataFrame({"bar_time": [0, 3_600_000, 7_200_000]})
    ) == "1h"
    assert tv_corpus.infer_timeframe(
        tmp_path / "plain.csv", pd.DataFrame({"bar_time": [0, 86_400_000, 172_800_000]})
    ) == "1D"

    corpus_root = tmp_path / "corpus"
    missing_root = corpus_root / "exports" / "001_missing_pine"
    missing_root.mkdir(parents=True)
    manifest = corpus_root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "1,001_missing_pine,strategy,grp,source.pine,chart.csv,15m\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        tv_corpus.load_manifest(manifest, corpus_root)

    visible_root = tmp_path / "visible"
    matching_export = visible_root / "exports" / "003_match"
    _write_chart(matching_export / "BTCUSDT_15.csv", times=(1, 2))
    visible_manifest = visible_root / "manifest.csv"
    visible_manifest.parent.mkdir(parents=True, exist_ok=True)
    visible_manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files,timeframes\n"
        "1,001_other,strategy,other,source.pine,missing.csv,15m\n"
        "2,002_wrong_tf,strategy,grp,source.pine,missing.csv,1h\n"
        "3,003_match,strategy,grp,source.pine,BTCUSDT_15.csv,15m\n",
        encoding="utf-8",
    )
    import openpine.batch.persistent_cache as persistent_cache

    monkeypatch.setattr(persistent_cache, "default_cache_dir", lambda root: root / ".cache")
    monkeypatch.setattr(persistent_cache, "load_tv_corpus", lambda _cache_dir, _key: None)
    monkeypatch.setattr(persistent_cache, "path_fingerprint", lambda paths, *, root: "fingerprint")
    monkeypatch.setattr(
        persistent_cache,
        "save_tv_corpus",
        lambda _cache_dir, _key, _bars, meta: {**meta, "cache": "fake"},
    )
    bars, meta = tv_corpus.load_visible_bars_by_time(
        root=visible_root,
        manifest=visible_manifest,
        source_group="grp",
        timeframe="15m",
        symbol="BTCUSDT",
    )
    assert sorted(bars) == [1_000, 2_000]
    assert meta["charts_scanned"] == 1
    assert meta["rows_loaded"] == 2

    entries = [
        _entry(tmp_path / "filter_1"),
        _entry(tmp_path / "filter_2"),
        _entry(tmp_path / "filter_3"),
    ]
    entries = [
        ExportEntry(idx, item.folder, item.kind, item.source_group, item.root, item.pine_path, item.charts)
        for idx, item in enumerate(entries, start=1)
    ]
    filtered = tv_corpus.filter_entries(
        entries,
        kind="all",
        timeframe=None,
        limit=None,
        start_id=2,
        only_id={3},
    )
    assert [entry.export_id for entry in filtered] == [3]


def test_runtime_helper_warmup_replay_dependency_and_indicator_runtime_edges(monkeypatch):
    assert runtime_helpers._fmt_utc_seconds(0) == "1970-01-01 00:00:00"
    assert runtime_helpers._plot_record_count(object()) == 0

    strategy = SimpleNamespace(
        strategy_id="sid",
        pine_id="pine",
        artifact_id="artifact",
        params_hash="hash",
        params_json='{"length": 5}',
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="BINANCE",
        market_type="SPOT",
    )
    registry = SimpleNamespace(update_status=lambda *_args: None)
    console = _Console()
    monkeypatch.setattr(
        runtime_helpers,
        "_parse_valid_strategy_backtest_window",
        lambda **_kwargs: (100, 200, None, None),
    )
    monkeypatch.setattr(
        runtime_helpers,
        "_load_strategy_backtest_class_or_exit",
        lambda **_kwargs: (type("Strategy", (), {}), 0.01),
    )
    monkeypatch.setattr(
        runtime_helpers,
        "_load_strategy_backtest_bars",
        lambda **_kwargs: (
            [SimpleNamespace(time=80), SimpleNamespace(time=100), SimpleNamespace(time=120)],
            "provider",
            {"cache": False},
            0.02,
        ),
    )
    monkeypatch.setattr(
        runtime_helpers,
        "_strategy_backtest_declaration_args",
        lambda **_kwargs: {"commission_type": "cash_per_contract"},
    )

    class Config:
        def __init__(
            self,
            *,
            start_time=None,
            max_bars_back=None,
            score_start_time=None,
            warmup_metadata=None,
            commission_type=None,
        ):
            self.start_time = start_time
            self.max_bars_back = max_bars_back
            self.score_start_time = score_start_time
            self.warmup_metadata = warmup_metadata
            self.commission_type = commission_type

    deps = SimpleNamespace(
        parse_timeframe=lambda _tf: SimpleNamespace(duration_ms=10),
        load_strategy_class_from_artifact=object(),
        BacktestArtifactError=RuntimeError,
        BarQuery=object,
        InstrumentKey=object,
        DataOrchestrator=object,
        create_local_marketdata_provider_adapter=object(),
        ArtifactStore=object,
        BacktestRunConfig=Config,
    )
    prepared = runtime_helpers._prepare_strategy_backtest_inputs(
        strategy=strategy,
        strategy_id="sid",
        from_date="from",
        to_date="to",
        capture_plots=False,
        capture_from=None,
        capture_to=None,
        history_from=None,
        warmup_bars=3,
        gap_policy="allow",
        now_ms=999,
        registry=registry,
        deps=deps,
        perf_counter=lambda: 1.0,
        console=console,
    )
    assert prepared.start_ms == 70
    assert prepared.effective_pre_bars == 1
    assert prepared.config.start_time == 100
    assert prepared.config.max_bars_back == 3
    assert prepared.config.warmup_metadata == {"recommended_pre_bars_raw": 3}

    class ArtifactError(Exception):
        pass

    replay_updates: list[tuple[str, str]] = []
    replay_registry = SimpleNamespace(
        update_status=lambda strategy_id, status: replay_updates.append((strategy_id, status))
    )
    replay_console = _Console()
    monkeypatch.setattr(
        runtime_helpers,
        "_parse_strategy_backtest_window",
        lambda **_kwargs: (1, 2, None, None),
    )

    def raise_artifact_error(*_args, **_kwargs):
        raise ArtifactError("artifact missing")

    with pytest.raises(SystemExit) as exc:
        runtime_helpers._prepare_strategy_replay_inputs(
            strategy=strategy,
            strategy_id="sid",
            from_date="from",
            to_date="to",
            now_ms=999,
            registry=replay_registry,
            load_strategy_class=raise_artifact_error,
            artifact_error_cls=ArtifactError,
            artifact_store_cls=object,
            bar_query_cls=object,
            instrument_key_cls=object,
            parse_timeframe_func=lambda value: value,
            orchestrator_cls=object,
            config_cls=object,
            perf_counter=lambda: 1.0,
            console=replay_console,
        )
    assert exc.value.code == 1
    assert replay_updates == [("sid", "paused")]
    assert "artifact missing" in replay_console.messages[-1]

    contracts_mod = types.ModuleType("marketdata_provider.contracts")
    contracts_mod.BarQuery = type("BarQuery", (), {})
    contracts_mod.InstrumentKey = type("InstrumentKey", (), {})
    contracts_mod.parse_timeframe = lambda value: value
    orchestrator_mod = types.ModuleType("openpine.data.orchestrator")
    orchestrator_mod.DataOrchestrator = type("DataOrchestrator", (), {})
    provider_mod = types.ModuleType("openpine.data.provider_adapter")
    provider_mod.create_local_marketdata_provider_adapter = lambda: "provider"
    export_mod = types.ModuleType("openpine.export")
    export_mod.export_plot_records = lambda *args, **kwargs: 0
    export_mod.parse_time_ms = lambda value: 1
    export_mod.write_json = lambda path, payload: None
    pine_registry_mod = types.ModuleType("openpine.pine.registry")
    pine_registry_mod.SQLitePineSourceRegistry = type("SQLitePineSourceRegistry", (), {})
    engine_mod = types.ModuleType("openpine.runtime.engine")
    engine_mod.BacktestArtifactError = RuntimeError
    engine_mod.load_generated_class_from_artifact = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", contracts_mod)
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", orchestrator_mod)
    monkeypatch.setitem(sys.modules, "openpine.data.provider_adapter", provider_mod)
    monkeypatch.setitem(sys.modules, "openpine.export", export_mod)
    monkeypatch.setitem(sys.modules, "openpine.pine.registry", pine_registry_mod)
    monkeypatch.setitem(sys.modules, "openpine.runtime.engine", engine_mod)
    indicator_deps = runtime_helpers._indicator_plot_dependencies()
    assert indicator_deps.BarQuery is contracts_mod.BarQuery
    assert indicator_deps.SQLitePineSourceRegistry is pine_registry_mod.SQLitePineSourceRegistry
    assert indicator_deps.write_json is export_mod.write_json

    imported_libraries: list[str] = []
    integrations_mod = types.ModuleType("openpine.integrations")
    integrations_mod.import_library = lambda name: imported_libraries.append(name)
    pine_runtime_mod = types.ModuleType("backtest_engine.execution_backends.pine_runtime")

    class PineRuntimeBackend:
        def execute(self, generated_class, bars, **kwargs):
            assert generated_class == "Generated"
            assert bars == ["bar"]
            assert kwargs["runtime_kwargs"]["data_provider"] == "inner-provider"
            assert kwargs["runtime_kwargs"]["tv_export_barstate"] is True
            assert kwargs["runtime_kwargs"]["normalize_time_close_exclusive"] is True
            assert kwargs["is_indicator"] is True
            return {"executed": True}

    pine_runtime_mod.PineRuntimeBackend = PineRuntimeBackend
    monkeypatch.setitem(sys.modules, "openpine.integrations", integrations_mod)
    monkeypatch.setitem(sys.modules, "backtest_engine", types.ModuleType("backtest_engine"))
    monkeypatch.setitem(
        sys.modules,
        "backtest_engine.execution_backends",
        types.ModuleType("backtest_engine.execution_backends"),
    )
    monkeypatch.setitem(
        sys.modules,
        "backtest_engine.execution_backends.pine_runtime",
        pine_runtime_mod,
    )
    result = runtime_helpers._execute_indicator_plot_runtime(
        generated_class="Generated",
        bars=["bar"],
        config=SimpleNamespace(),
        symbol="BTCUSDT",
        timeframe="15m",
        provider=SimpleNamespace(_provider="inner-provider"),
        compare_from_ms=1,
        compare_to_ms=2,
        progress_callback=lambda done, total: None,
    )
    assert result == {"executed": True}
    assert imported_libraries == ["backtest_engine"]


def test_storage_config_and_report_cli_leftover_branches(monkeypatch, tmp_path: Path):
    class FakeCursor:
        def __init__(self, rows=()):
            self._rows = list(rows)

        def fetchall(self):
            return self._rows

    class FakeStorage:
        def __init__(self, path):
            self.path = Path(path)

        def execute(self, _sql):
            return FakeCursor([])

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    class FakeMigrationRunner:
        applied: list[str] = []

        def run_migrations(self, _storage_db):
            return list(self.applied)

    monkeypatch.setattr("openpine.storage.SQLiteStorage", FakeStorage)
    monkeypatch.setattr("openpine.storage.MigrationRunner", FakeMigrationRunner)

    click_runner = CliRunner()
    FakeMigrationRunner.applied = []
    init_result = click_runner.invoke(
        storage_group, ["storage-init", "--path", str(tmp_path / "custom.sqlite")]
    )
    assert init_result.exit_code == 0, init_result.output
    assert "No pending migrations" in init_result.output

    schema_result = click_runner.invoke(
        storage_group, ["storage-schema", "--path", str(tmp_path / "missing.sqlite")]
    )
    assert schema_result.exit_code == 0, schema_result.output
    assert "Database not found" in schema_result.output

    FakeMigrationRunner.applied = ["001"]
    migrate_result = click_runner.invoke(
        storage_group, ["migrate", "--path", str(tmp_path / "migrate.sqlite")]
    )
    assert migrate_result.exit_code == 0, migrate_result.output
    assert "No migrations applied yet" in migrate_result.output
    assert "Newly applied: ['001']" in migrate_result.output

    health_report = SimpleNamespace(
        schema_contract="test-contract",
        table_count=1,
        index_count=0,
        applied_versions=[],
        pending_versions=[],
        missing_indexes=["idx_missing"],
        event_schema_compatible=False,
        ok=False,
    )
    monkeypatch.setattr("openpine.storage.db_health.schema_health", lambda _storage: health_report)
    health_result = click_runner.invoke(
        storage_group, ["health", "--path", str(tmp_path / "health.sqlite")]
    )
    assert health_result.exit_code == 1
    assert "required indexes missing" in health_result.output
    assert "events schema: incompatible" in health_result.output

    monkeypatch.setattr("openpine.storage.backup.verify_openpine", lambda _config: {"sqlite_exists": True, "sqlite_integrity": True, "optional": True})
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: SimpleNamespace())
    verify_result = click_runner.invoke(storage_group, ["verify"])
    assert verify_result.exit_code == 0, verify_result.output
    assert "All checks passed" in verify_result.output

    bad_cfg = SimpleNamespace(
        workspace_root=None,
        data_cache_root=tmp_path / "cache",
        output_root=tmp_path / "out",
        db_path=tmp_path / "db",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        sqlite_path=tmp_path / "openpine.sqlite",
        live_enabled="yes",
        kill_switch=0,
        log_level=123,
        timezone="UTC",
    )
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: bad_cfg)
    config_result = click_runner.invoke(config_group, ["validate"])
    assert config_result.exit_code == 1
    assert "workspace_root is required" in config_result.output
    assert "live_enabled must be a boolean" in config_result.output
    assert "kill_switch must be a boolean" in config_result.output
    assert "log_level must be a string" in config_result.output

    reports_cfg = SimpleNamespace(data_dir=tmp_path / "reports_data")
    reports_dir = reports_cfg.data_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "note.txt").write_text("plain text report", encoding="utf-8")
    (reports_dir / "data.json").write_text('{"answer": 42}', encoding="utf-8")
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: reports_cfg)
    show_text = click_runner.invoke(reports_group, ["show", "note"])
    assert show_text.exit_code == 0, show_text.output
    assert "plain text report" in show_text.output
    export_json = click_runner.invoke(reports_group, ["export", "data", "--format", "json"])
    assert export_json.exit_code == 0, export_json.output
    assert '"answer": 42' in export_json.output
