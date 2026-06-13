from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pandas as pd
import pytest
from marketdata_provider.contracts import InstrumentKey, parse_timeframe


def test_gateway_state_tolerates_nonfatal_startup_failures(monkeypatch, tmp_path: Path) -> None:
    from openpine.gateway import deps as gateway_deps
    from openpine.storage import migrations as migrations_mod
    from openpine.data import direct_provider as direct_provider_mod
    from openpine.data import orchestrator as orchestrator_mod

    class DummyStorage:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyRegistry:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class DummyManager:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class FailingEventBus:
        def __init__(self, _storage) -> None:
            raise RuntimeError("old events schema")

    class FailingOrchestrator:
        def __init__(self, *, cache_enabled: bool) -> None:
            self.cache_enabled = cache_enabled

        def set_provider(self, _provider) -> None:
            raise RuntimeError("provider unavailable")

    class DummyProvider:
        pass

    def fail_migrations(self, storage) -> None:
        del self, storage
        raise RuntimeError("duplicate column")

    cfg = SimpleNamespace(
        sqlite_path=tmp_path / "openpine.sqlite",
        data_dir=tmp_path / "data",
        kill_switch=False,
    )
    monkeypatch.setattr(gateway_deps.OpenPineConfig, "load", lambda: cfg)
    monkeypatch.setattr(gateway_deps, "SQLiteStorage", DummyStorage)
    monkeypatch.setattr(migrations_mod.MigrationRunner, "run_migrations", fail_migrations)
    monkeypatch.setattr(gateway_deps, "SQLitePineSourceRegistry", DummyRegistry)
    monkeypatch.setattr(gateway_deps, "SQLiteStrategyRegistry", DummyRegistry)
    monkeypatch.setattr(gateway_deps, "BacktestResultStore", DummyManager)
    monkeypatch.setattr(gateway_deps, "AccountManager", DummyManager)
    monkeypatch.setattr(gateway_deps, "OrderManager", DummyManager)
    monkeypatch.setattr(gateway_deps, "EventBus", FailingEventBus)
    monkeypatch.setattr(gateway_deps, "JobScheduler", DummyManager)
    monkeypatch.setattr(gateway_deps, "ArtifactStore", DummyManager)
    monkeypatch.setattr(gateway_deps, "StateStore", DummyManager)
    monkeypatch.setattr(gateway_deps, "RiskManager", DummyManager)
    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", FailingOrchestrator)
    monkeypatch.setattr(direct_provider_mod, "DirectBinanceProvider", DummyProvider)

    state = gateway_deps.GatewayState()

    assert state.storage.path == cfg.sqlite_path
    assert state.event_bus is None
    assert state.orchestrator.cache_enabled is False
    state.close()
    assert state.storage.closed is True
    assert state.pine_registry.closed is True
    assert state.strategy_registry.closed is True


def test_artifact_store_writes_optional_files_and_reports_paths(tmp_path: Path) -> None:
    from openpine.artifacts.store import ArtifactStore

    store = ArtifactStore(root=tmp_path)
    assert store.list_artifacts("missing-source") == []
    assert store.artifact_exists("art_a", "pine_a") is False

    artifact_dir = store.save_artifact(
        artifact_id="art_a",
        source_id="pine_a",
        params_hash="hash_a",
        python_code="# generated\n",
        compile_meta={"compile_status": "OK"},
        source_text="//@version=6\nstrategy('x')\n",
        ast_json='{"kind":"Program"}',
        requirements={"packages": ["numpy"]},
        diagnostics="clean",
    )

    loaded = store.get_artifact("art_a", "pine_a")
    assert loaded["ast_json"] == '{"kind":"Program"}'
    assert loaded["source_text"].startswith("//@version=6")
    assert loaded["compile_meta"]["params_hash"] == "hash_a"
    assert json.loads((artifact_dir / "requirements.json").read_text())["packages"] == [
        "numpy"
    ]
    assert store.get_artifact_path("art_a", "pine_a") == artifact_dir
    assert store.artifact_exists("art_a", "pine_a") is True


def test_exchange_metadata_fallbacks_and_cache_error_paths(monkeypatch, tmp_path: Path) -> None:
    from openpine import exchange_metadata as metadata

    real_loader = metadata._load_binance_spot_exchange_info
    monkeypatch.setattr(
        metadata,
        "_load_binance_spot_exchange_info",
        lambda **_kwargs: {"symbols": []},
    )
    assert metadata.default_qty_step("binance", "spot", "btcusdt") == 0.00001

    monkeypatch.setattr(
        metadata,
        "_load_binance_spot_exchange_info",
        lambda **_kwargs: {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}],
                }
            ]
        },
    )
    assert metadata.default_qty_step("binance", "spot", "BTCUSDT") == 0.00001
    assert metadata._symbol_info({"symbols": [{"symbol": "ETHUSDT"}]}, "BTCUSDT") is None

    monkeypatch.setattr(metadata, "_load_binance_spot_exchange_info", real_loader)
    from openpine import exchange_metadata as fresh_metadata

    monkeypatch.setattr(fresh_metadata, "_BINANCE_SPOT_INFO", None)
    monkeypatch.setenv(
        "OPENPINE_BINANCE_EXCHANGE_INFO_CACHE", str(tmp_path / "exchange-info.json")
    )
    monkeypatch.setenv("OPENPINE_BINANCE_EXCHANGE_INFO_REFRESH", "1")
    monkeypatch.setattr(
        fresh_metadata,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )

    assert fresh_metadata._load_binance_spot_exchange_info(fetch_network=True) is None
    fresh_metadata._write_cache(Path("/dev/null/openpine-cache.json"), {"symbols": []})


def test_order_manager_loads_existing_ids_and_account_filter_branch() -> None:
    from openpine.orders.manager import OrderManager

    class Cursor:
        def __init__(self, *, rows=(), one=None) -> None:
            self._rows = list(rows)
            self._one = one

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._one

    order_row = (
        "ord_1",
        "strategy_1",
        "acct_1",
        None,
        "client_1",
        "BTCUSDT",
        "buy",
        "limit",
        2.0,
        100.0,
        None,
        "pending",
        0,
        0.0,
        None,
        "{}",
        None,
        None,
        10,
        20,
    )

    class Storage:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def execute(self, sql: str, params: tuple = ()) -> Cursor:
            self.calls.append((" ".join(sql.split()).lower(), params))
            lowered = self.calls[-1][0]
            if "select client_order_id from orders" in lowered:
                return Cursor(rows=[("already-seen",)])
            if "where order_id" in lowered:
                return Cursor(one=None)
            if "select * from orders" in lowered:
                return Cursor(rows=[order_row])
            raise AssertionError(sql)

        def commit(self) -> None:  # pragma: no cover - not used here
            raise AssertionError("commit should not be called")

    storage = Storage()
    manager = OrderManager(storage)  # type: ignore[arg-type]

    assert "already-seen" in manager._seen_client_ids
    assert manager.get_order("missing") is None
    assert [order.order_id for order in manager.list_orders(account_id="acct_1")] == [
        "ord_1"
    ]
    assert storage.calls[-1][1] == ("acct_1",)


def test_scheduler_dequeue_skips_blocked_and_nonpending_jobs() -> None:
    from openpine.jobs.models import Job, JobStatus, JobType
    from openpine.jobs.scheduler import JobScheduler

    scheduler = JobScheduler()
    blocked = scheduler.enqueue(
        Job(job_type=JobType.BACKTEST, priority=10, serialization_key="strategy-a")
    )
    nonpending = scheduler.enqueue(Job(job_type=JobType.REPORT, priority=5))
    nonpending.status = JobStatus.RUNNING
    scheduler._running["strategy-a"] = "external-job"

    assert scheduler.dequeue() is None
    assert blocked in scheduler._queue
    assert nonpending in scheduler._queue


def test_stream_manager_no_loop_adapter_edges_and_model_dump_serialization() -> None:
    from openpine.streams.manager import (
        MarketDataStreamManager,
        SubscriptionStatus,
        _serialize_marketdata_model,
    )

    class Dumpable:
        def model_dump(self):
            return {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"}

    class Adapter:
        async def subscribe(self, instrument_key, timeframe) -> None:
            del instrument_key, timeframe
            raise AssertionError("no running loop should avoid scheduling")

        async def unsubscribe(self, instrument_key, timeframe) -> None:
            del instrument_key, timeframe
            raise AssertionError("no running loop should avoid scheduling")

    assert _serialize_marketdata_model(Dumpable())["symbol"] == "BTCUSDT"

    manager = MarketDataStreamManager(event_bus=object(), data_orchestrator=object())
    manager.set_adapter(Adapter())
    sub = manager.subscribe(
        InstrumentKey("binance", "spot", "BTCUSDT"), parse_timeframe("1m")
    )
    manager.unsubscribe(sub.subscription_id)

    assert manager.get_subscription(sub.subscription_id).status == SubscriptionStatus.STOPPED


async def test_live_provider_adapter_unsubscribe_cancels_task() -> None:
    from openpine.streams.provider_adapter import LocalProviderLiveDataFeedAdapter

    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")
    adapter = LocalProviderLiveDataFeedAdapter()
    key = (str(instrument), timeframe.canonical)
    cancelled = False

    async def wait_forever() -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    task = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)
    adapter._clients[key] = object()
    adapter._tasks[key] = task

    await adapter.unsubscribe(instrument, timeframe)

    assert key not in adapter._clients
    assert key not in adapter._tasks
    assert task.cancelled() is True
    assert cancelled is True


def test_quality_helpers_skip_dirs_syntax_errors_and_script_guard(
    monkeypatch, tmp_path: Path
) -> None:
    from openpine import quality

    root = tmp_path / "pkg"
    (root / "tests").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    (root / "tests" / "ignored.py").write_text("def ignored():\n    return 1\n", encoding="utf-8")
    (root / "__pycache__" / "ignored.py").write_text("def ignored():\n    return 1\n", encoding="utf-8")

    assert [path.name for path in quality._python_files(root)] == ["bad.py", "ok.py"]
    assert list(quality._function_fingerprints(root / "bad.py")) == []

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: SimpleNamespace(command="unknown", root=str(root)),
    )
    assert quality.main([]) == 2
    with pytest.raises(SystemExit) as exc:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("openpine.quality", run_name="__main__")
    assert exc.value.code == 2


def test_release_report_aggregates_version_quality_and_script_guard(
    monkeypatch, tmp_path: Path
) -> None:
    from openpine import release as release_mod
    from openpine.distribution import DistributionManifest
    from openpine.quality import ArchitectureReport, DuplicateReport

    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "openpine"\nversion = "4.0.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    migration = root / "011_perf_indexes.sql"
    migration.write_text("-- indexes present for this unit test\n", encoding="utf-8")

    monkeypatch.setattr(release_mod, "__version__", "0.0.0")
    monkeypatch.setattr(release_mod, "CANONICAL_DOCS", set())
    monkeypatch.setattr(release_mod, "REQUIRED_INDEXES", ())
    monkeypatch.setattr(
        release_mod,
        "_get_migration_files",
        lambda _path: [(11, "perf_indexes", migration)],
    )
    monkeypatch.setattr(
        release_mod,
        "architecture_report",
        lambda _root, max_lines: ArchitectureReport(
            max_lines=max_lines,
            oversized_count=1,
            oversized=[{"path": "big.py", "lines": max_lines + 1}],
        ),
    )
    monkeypatch.setattr(
        release_mod,
        "duplicate_report",
        lambda _root: DuplicateReport(
            duplicate_group_count=1,
            duplicate_groups=[{"locations": ["a.py:f", "b.py:f"]}],
        ),
    )
    monkeypatch.setattr(
        release_mod,
        "distribution_manifest",
        lambda root_path: DistributionManifest(
            root=root_path.name, file_count=1, byte_count=1, hygiene_errors=()
        ),
    )

    report = release_mod.release_report(root)

    assert report.ok is False
    assert any("package version 0.0.0" in error for error in report.errors)
    assert any("architecture oversized files" in error for error in report.errors)
    assert any("duplicate function groups" in error for error in report.errors)

    monkeypatch.setattr(sys, "argv", ["python -m openpine.release", "--root", str(root)])
    with pytest.raises(SystemExit) as exc:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("openpine.release", run_name="__main__")
    assert exc.value.code == 1


def test_parquet_fallback_schema_dataframe_io_and_count(monkeypatch, tmp_path: Path) -> None:
    from openpine._compat import parquet

    monkeypatch.setattr(parquet, "_pa", None)
    monkeypatch.setattr(parquet, "_pq", None)

    fallback_schema = parquet.schema(
        [("symbol", "string"), ("quantity", "float64", True)]
    )
    assert str(fallback_schema) == "symbol: string\nquantity: float64 nullable"

    output = tmp_path / "bars.parquet"
    df = pd.DataFrame({"symbol": ["BTCUSDT"], "quantity": [1.25]})
    parquet.write_dataframe(df, output, schema=fallback_schema)

    loaded = parquet.read_dataframe(output)
    assert loaded.to_dict("records") == [{"symbol": "BTCUSDT", "quantity": 1.25}]
    assert parquet.row_count(output) == 1


def test_event_bus_model_dump_payload_and_persist_failure() -> None:
    from openpine.events import bus as bus_mod
    from openpine.events.types import Event, EventType

    class Dumpable:
        def model_dump(self):
            return {"payload": "via-model-dump"}

    event_bus = bus_mod.EventBus.__new__(bus_mod.EventBus)
    event_bus._subscribers = {}

    def fail_persist(_event) -> None:
        raise OSError("disk full")

    event_bus._persist = fail_persist

    assert bus_mod._event_payload_dict(Dumpable()) == {"payload": "via-model-dump"}
    with pytest.raises(RuntimeError, match="failed to persist durable event"):
        event_bus.emit(Event.create(EventType.JOB_STARTED, {"job_id": "j1"}))


def test_account_manager_unfiltered_and_provider_only_queries(tmp_path: Path) -> None:
    from openpine.accounts.manager import AccountManager
    from openpine.accounts.models import AccountType
    from openpine.storage import MigrationRunner, SQLiteStorage

    storage = SQLiteStorage(tmp_path / "accounts.sqlite")
    try:
        MigrationRunner().run_migrations(storage)
        manager = AccountManager(storage)
        account = manager.create_account(
            name="paper-ccxt",
            provider="ccxt",
            exchange="binance",
            market_type="spot",
            mode=AccountType.PAPER,
        )

        assert [item.account_id for item in manager.list_accounts()] == [
            account.account_id
        ]
        assert [item.account_id for item in manager.list_accounts_by_provider("ccxt")] == [
            account.account_id
        ]
    finally:
        storage.close()


def test_config_cache_pine_risk_timezone_and_compile_success_leftovers(
    monkeypatch, tmp_path: Path
) -> None:
    from openpine.accounts.models import Account
    from openpine.compile.adapter import CompileResult
    from openpine.compile.pipeline import compile_pipeline
    from openpine.config import loader as config_loader
    from openpine.config.model import OpenPineConfig
    from openpine.data.cache_io import read_valid_meta
    from openpine.orders.models import OrderIntent, OrderSide, OrderType
    from openpine.pine.source import PineSource
    from openpine.risk.manager import MaxPositionSizeRule, RiskManager
    from openpine import timezones

    monkeypatch.delenv("OPENPINE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENPINE_TIMEZONE", raising=False)
    monkeypatch.setattr(config_loader, "load_env_file", lambda: None)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("config_dir: ~/openpine-pass48\n", encoding="utf-8")
    assert config_loader.load_config(config_path).config_dir == Path(
        "~/openpine-pass48"
    ).expanduser()
    assert config_loader.load_config(tmp_path / "missing.yaml").timezone == timezones.DEFAULT_TIMEZONE

    meta_path = tmp_path / "cache" / "meta.json"
    meta_path.parent.mkdir()
    meta_path.write_text(
        json.dumps({"schema_version": 1, "key": {"symbol": "BTCUSDT"}}),
        encoding="utf-8",
    )
    assert read_valid_meta(meta_path, {"symbol": "BTCUSDT"}, schema_version=2) is None
    assert read_valid_meta(meta_path, {"symbol": "ETHUSDT"}, schema_version=1) is None
    assert read_valid_meta(meta_path, {"symbol": "BTCUSDT"}, schema_version=1)[
        "schema_version"
    ] == 1

    pine = PineSource(id="pine_1", name="source", source_text="//@version=6\n")
    assert pine.to_dict()["id"] == "pine_1"
    assert PineSource.from_dict({"id": "pine_2", "name": "minimal", "source_text": "x"}).version == "1.0.0"

    account = Account(account_id="acct_1", name="acct", provider="ccxt", exchange="binance")
    market_order = OrderIntent(
        client_order_id="client_1",
        strategy_id="strategy_1",
        account_id="acct_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        price=None,
    )
    assert MaxPositionSizeRule(max_notional=1.0).check(market_order, account) == (
        True,
        None,
    )
    risk = RiskManager()
    assert risk.kill_switch is False
    risk.set_kill_switch(True)
    assert risk.kill_switch is True

    monkeypatch.setattr(
        config_loader,
        "load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
    )
    assert timezones.configured_timezone().name == timezones.DEFAULT_TIMEZONE
    assert timezones.format_utc_ms(0) == "1970-01-01 00:00:00"

    cfg = OpenPineConfig(workspace_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr("openpine.artifacts.store.OpenPineConfig.load", lambda: cfg)

    class Adapter:
        def compile(self, source_text: str, **kwargs) -> CompileResult:
            assert source_text.startswith("//@version=6")
            assert "profile" in kwargs
            return CompileResult(
                success=True,
                python_code="# generated\n",
                compile_meta={"pine2ast_version": "p", "ast2python_version": "a"},
                ast_json='{"kind":"Program"}',
            )

    compiled = compile_pipeline(pine, Adapter(), params_hash="params")

    artifact_path = Path(compiled["artifact_path"])
    assert compiled["success"] is True
    assert (artifact_path / "generated_strategy.py").read_text() == "# generated\n"
    assert (artifact_path / "ast.json").read_text() == '{"kind":"Program"}'
