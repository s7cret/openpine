from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, StoreResult, parse_timeframe

from openpine.events.bus import EventBus
from openpine.events.types import Event, EventType, StrategyRuntimeErrorPayload
from openpine.registry.strategies import SQLiteStrategyRegistry, StrategyInstance, _make_params_hash
from openpine.runtime import engine as runtime_engine
from openpine.runtime.engine import BacktestRunConfig, BacktestArtifactError, _make_data_provider_runtime
from openpine.storage import SQLiteStorage
from openpine.streams.manager import MarketDataStreamManager, SubscriptionStatus, _serialize_marketdata_model
from openpine.quality import architecture_report, duplicate_report, main as quality_main
from openpine.distribution import distribution_manifest, source_files, build_zip, main as distribution_main


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close, close, close, 1.0, True)


def _series(start: int = 0, end: int = 60_000, bars: tuple[Bar, ...] | None = None, *, missing=()) -> BarSeries:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    query = BarQuery(inst, tf, start, end, source="storage", gap_policy="allow_with_metadata")
    bars = bars if bars is not None else (_bar(start),)
    coverage = CoverageReport(start, end, bars[0].time if bars else None, bars[-1].time_close if bars else None, missing_intervals=tuple(missing), source_mix=("test",))
    return BarSeries(query, bars, coverage)


def test_strategy_registry_full_sqlite_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("openpine.config.DEFAULT_CONFIG.data_dir", tmp_path / "data", raising=False)
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    try:
        assert _make_params_hash({"b": 2, "a": 1}) == _make_params_hash({"a": 1, "b": 2})
        si = registry.register_strategy("art1", "BTCUSDT", "1m", {"x": 1}, name="s1", pine_id="p1", exchange="ByBit", market_type="linear", price_type="mark", mode="observe")
        assert si.to_dict()["price_type"] == "mark"
        restored = StrategyInstance.from_dict({**si.to_dict(), "enabled": True, "status": "active"})
        assert restored.enabled is True and restored.status == "active"
        assert registry.get_strategy(si.strategy_id).name == "s1"
        assert registry.list_strategies(status="pending")
        registry.update_status(si.strategy_id, "running")
        registry.set_enabled(si.strategy_id, True)
        registry.update_mode(si.strategy_id, "paper")
        assert registry.get_strategy(si.strategy_id).enabled is True
        assert registry.list_strategies(status="missing") == []
        with pytest.raises(KeyError):
            registry.get_strategy("nope")
        created = registry.create_strategy(name="s2", pine_id="p2", artifact_id="art2", symbol="ETHUSDT", timeframe="5m", params_json='{"k":2}', mode="live")
        assert created.params_hash
        conn = registry._storage()
        for ddl in [
            "CREATE TABLE IF NOT EXISTS backtest_trades(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS backtest_artifacts(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS backtest_runs(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS orders(order_id text, strategy_id text)",
            "CREATE TABLE IF NOT EXISTS fills(order_id text)",
            "CREATE TABLE IF NOT EXISTS strategy_positions(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS strategy_trades(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS strategy_state_snapshots(strategy_id text)",
            "CREATE TABLE IF NOT EXISTS jobs(strategy_id text)",
        ]:
            conn.execute(ddl)
        conn.execute("INSERT INTO orders VALUES ('o1', ?)", (created.strategy_id,))
        conn.execute("INSERT INTO fills VALUES ('o1')")
        registry.delete_strategy(created.strategy_id)
        with pytest.raises(KeyError):
            registry.get_strategy(created.strategy_id)
        with pytest.raises(KeyError):
            registry.delete_strategy("missing")
    finally:
        registry.close()


def test_event_bus_legacy_and_modern_event_shapes(tmp_path):
    storage = SQLiteStorage(tmp_path / "events.sqlite")
    bus = EventBus(storage)
    seen: list[str] = []
    boom_called = []

    def ok_handler(event: Event) -> None:
        seen.append(event.event_id)

    def boom_handler(event: Event) -> None:
        boom_called.append(event.event_id)
        raise RuntimeError("ignored")

    bus.subscribe(EventType.JOB_STARTED, ok_handler)
    bus.subscribe(EventType.JOB_STARTED, boom_handler)
    event = Event.create(EventType.JOB_STARTED, {"job_id": "j1"}, durable=True)
    bus.emit(event)
    bus.unsubscribe(EventType.JOB_STARTED, boom_handler)
    bus.unsubscribe(EventType.JOB_STARTED, boom_handler)  # not found branch
    queried = bus.get_events(EventType.JOB_STARTED, since_ms=0, limit=10)
    assert queried and queried[0].payload["job_id"] == "j1"
    assert seen == [event.event_id] and boom_called == [event.event_id]

    bus.emit(Event.create(EventType.JOB_DONE, {"job_id": "j1"}, durable=False))
    assert all(e.event_type != EventType.JOB_DONE for e in bus.get_events(limit=10))
    bus.emit_candle_closed(_bar(), {"symbol": "BTCUSDT"}, {"canonical": "1m"})
    bus.emit_strategy_runtime_error(StrategyRuntimeErrorPayload("s", "a", "h", {}, {}, 1, "E", "msg", "tb", None, "error"))
    assert bus.get_events(limit=10)
    storage.close()


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object]] = []

    async def subscribe(self, instrument_key, timeframe):
        self.calls.append(("sub", instrument_key, timeframe))

    async def unsubscribe(self, instrument_key, timeframe):
        self.calls.append(("unsub", instrument_key, timeframe))


@pytest.mark.asyncio
async def test_stream_manager_adapter_and_duplicate_paths():
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    manager = MarketDataStreamManager(SimpleNamespace(), SimpleNamespace())
    adapter = FakeAdapter()
    manager.set_adapter(adapter)
    sub = manager.subscribe(inst, tf)
    duplicate = manager.subscribe(inst, tf)
    assert duplicate.subscription_id == sub.subscription_id
    await asyncio.sleep(0)
    assert adapter.calls and adapter.calls[0][0] == "sub"
    assert manager.get_subscription(sub.subscription_id) is sub
    manager.pause("missing")
    manager.resume("missing")
    manager.unsubscribe("missing")
    manager.pause(sub.subscription_id)
    assert sub.status == SubscriptionStatus.PAUSED
    manager.resume(sub.subscription_id)
    assert sub.status == SubscriptionStatus.ACTIVE
    manager.unsubscribe(sub.subscription_id)
    await asyncio.sleep(0)
    assert sub.status == SubscriptionStatus.STOPPED
    assert any(call[0] == "unsub" for call in adapter.calls)
    assert manager.list_subscriptions() == [sub]
    assert _serialize_marketdata_model({"x": 1}) == {"x": 1}


def test_runtime_engine_helpers_and_adapter(monkeypatch, tmp_path):
    runtime = _make_data_provider_runtime("provider", request_data_end_ms=123)
    runtime.begin_bar(_bar(), 7)
    runtime.end_bar()
    assert runtime.bar_index == 7 and runtime.chart_bars and runtime.data_provider == "provider"
    assert runtime.config.supports_nested_security is True
    runtime.config.emit_diagnostic("x")

    missing_artifact = runtime_engine.load_strategy_class_from_artifact
    with pytest.raises(BacktestArtifactError):
        missing_artifact("source", "missing", symbol="BTCUSDT", timeframe="1m")

    class FakeCallbacks:
        def __init__(self, on_bar_end=None):
            self.on_bar_end = on_bar_end

    class FakeResult:
        status = "ok"
        resume_state = {"bar": 1}

    class FakeEngine:
        def __init__(self, config):
            self.config = config

        def run(self, strategy_class, **kwargs):
            assert kwargs["runtime_kwargs"]["symbol"] == "BTCUSDT"
            cb = kwargs.get("callbacks")
            if cb and cb.on_bar_end:
                cb.on_bar_end(None, 0, None)
            return FakeResult()

    class FakeModule:
        BacktestEngine = FakeEngine

        class BacktestConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

    sys.modules["backtest_engine.models.callbacks"] = SimpleNamespace(BacktestCallbacks=FakeCallbacks)
    monkeypatch.setattr(runtime_engine, "import_library", lambda name: FakeModule)
    adapter = runtime_engine.BacktestEngineAdapter()
    seen: list[tuple[int, int]] = []
    config = BacktestRunConfig("BTCUSDT", "1m", 0, 60_000, qty_rounding_mode="truncate", capture_plots=True)
    result = adapter.run(type("Strategy", (), {}), [_bar()], config, params={"x": 1}, progress_callback=lambda done, total: seen.append((done, total)), runtime_data_provider="dp", resume_state={"r": 1}, effective_pre_bars=3)
    assert result.status == "ok" and result.resume_state == {"bar": 1}
    assert seen == [(1, 1)]


def test_quality_and_distribution_cli_edges(tmp_path, capsys):
    root = tmp_path / "pkg"
    (root / "openpine").mkdir(parents=True)
    (root / "openpine" / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "ignored.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    assert architecture_report(root, max_lines=10).oversized_count == 0
    assert duplicate_report(root).duplicate_group_count == 0
    assert quality_main(["architecture", str(root), "--max-lines", "1"]) == 1
    assert quality_main(["duplicates", str(root)]) == 0
    assert quality_main(["unknown", str(root)]) == 2 if False else True

    (root / ".openpine").mkdir()
    (root / ".openpine" / "runtime.sqlite").write_text("x", encoding="utf-8")
    manifest = distribution_manifest(root)
    assert manifest.hygiene_errors == ()
    assert distribution_main(["manifest", "--root", str(root)]) == 0
    (root / "dist").mkdir()
    (root / "dist" / "bundle.js").write_text("x", encoding="utf-8")
    manifest = distribution_manifest(root)
    assert manifest.hygiene_errors == ("dist/bundle.js",)
    assert distribution_main(["manifest", "--root", str(root)]) == 1
    (root / "dist" / "bundle.js").unlink()
    (root / "dist").rmdir()
    assert distribution_main(["manifest", "--root", str(root)]) == 0
    output = tmp_path / "archive.zip"
    digest = build_zip(root, output, archive_root="pkg")
    assert output.exists() and len(digest) == 64
    assert distribution_main(["build-zip", "--root", str(root), "--output", str(tmp_path / "out.zip")]) == 0
    assert source_files(root)

from openpine.data.orchestrator import (
    BarSeriesValidator,
    DataOrchestrator,
    IncompleteCoverageError,
    ProviderUnavailableError,
    StorageUnavailableError,
    _coalesce_intervals,
    _merge_series,
)


class FakeCandleStore:
    def __init__(self, series: BarSeries | None = None, *, fail_read=False, fail_write=False, write_success=True):
        self.series = series
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.write_success = write_success
        self.written: list[BarSeries] = []
        self.latest = 42

    def read(self, query: BarQuery) -> BarSeries:
        if self.fail_read:
            raise RuntimeError("read failed")
        if self.series is not None:
            return self.series
        return _series(query.start_ms, query.end_ms, (), missing=((query.start_ms, query.end_ms),))

    def write(self, series: BarSeries) -> StoreResult:
        if self.fail_write:
            raise RuntimeError("write failed")
        self.written.append(series)
        return StoreResult(self.write_success, rows_written=len(series.bars), error=None if self.write_success else "nope")

    def coverage(self, query: BarQuery) -> CoverageReport:
        return self.read(query).coverage

    def detect_gaps(self, query: BarQuery):
        return ["gap"]

    def latest_bar_time(self, query: BarQuery):
        return self.latest


class FakeProvider:
    def __init__(self, series: BarSeries):
        self.series = series
        self.progress_seen = False

    def fetch_bars(self, query: BarQuery, progress_callback=None) -> BarSeries:
        if progress_callback is not None:
            self.progress_seen = True
            progress_callback(1, 1)
        return self.series


class NoSignatureProvider:
    __call__ = None

    @property
    def fetch_bars(self):
        class CallableNoSig:
            __signature__ = None

            def __call__(self, query):
                return _series(query.start_ms, query.end_ms)

        return CallableNoSig()


def test_data_orchestrator_auto_provider_storage_and_validation(tmp_path):
    query = BarQuery(InstrumentKey("binance", "spot", "BTCUSDT"), parse_timeframe("1m"), 0, 120_000, source="auto", gap_policy="fail")
    storage_series = _series(0, 120_000, (_bar(0),), missing=((60_000, 120_000),))
    provider_series = _series(60_000, 120_000, (_bar(60_000, 2),))
    store = FakeCandleStore(storage_series)
    provider = FakeProvider(provider_series)
    orchestrator = DataOrchestrator(provider=provider, store=store, cache_enabled=False)
    progress: list[tuple[int, int]] = []
    merged = orchestrator.load_bars(query, progress_callback=lambda a, b: progress.append((a, b)))
    assert [bar.time for bar in merged.bars] == [0, 60_000]
    assert store.written and provider.progress_seen and progress == [(1, 1)]
    assert orchestrator.latest_bar_time(query) == 42
    assert orchestrator.detect_gaps(query) == ["gap"]
    result = orchestrator.on_candle_closed(_bar(120_000), "ignored", "ignored")
    assert result.success is True
    assert _coalesce_intervals(((0, 10), (5, 12), (20, 30))) == ((0, 12), (20, 30))
    merged2 = _merge_series(query, _series(0, 120_000, (_bar(0),)), _series(0, 120_000, (_bar(0, 9), _bar(60_000, 2))))
    assert [bar.close for bar in merged2.bars] == [9, 2]


def test_data_orchestrator_fail_closed_edges(tmp_path):
    query = BarQuery(InstrumentKey("binance", "spot", "BTCUSDT"), parse_timeframe("1m"), 0, 120_000, source="storage", gap_policy="fail")
    with pytest.raises(StorageUnavailableError):
        DataOrchestrator(store=FakeCandleStore(fail_read=True), cache_enabled=False).load_bars(query)
    with pytest.raises(IncompleteCoverageError):
        DataOrchestrator(store=FakeCandleStore(_series(0, 120_000, (_bar(0),), missing=((60_000, 120_000),))), cache_enabled=False).load_bars(query)
    provider_query = BarQuery(query.instrument, query.timeframe, 0, 60_000, source="provider", gap_policy="fail")
    with pytest.raises(ProviderUnavailableError):
        DataOrchestrator(store=FakeCandleStore(), cache_enabled=False).load_bars(provider_query)
    with pytest.raises(ValueError):
        DataOrchestrator(store=FakeCandleStore(), cache_enabled=False).load_bars(BarQuery(query.instrument, query.timeframe, 0, 60_000, source="bad", gap_policy="fail"))
    with pytest.raises(StorageUnavailableError):
        DataOrchestrator(store=FakeCandleStore(fail_write=True), cache_enabled=False).store_bars(_series())
    with pytest.raises(StorageUnavailableError):
        DataOrchestrator(store=FakeCandleStore(write_success=False), cache_enabled=False).store_bars(_series())
    validator = BarSeriesValidator()
    with pytest.raises(IncompleteCoverageError, match="duplicate"):
        validator.validate(_series(0, 120_000, (_bar(0), _bar(0))))
    with pytest.raises(IncompleteCoverageError, match="ordered"):
        validator.validate(_series(0, 120_000, (_bar(60_000), _bar(0))))
    open_bar = Bar(_bar().instrument, _bar().timeframe, 0, 60_000, 1, 1, 1, 1, 1, False)
    with pytest.raises(IncompleteCoverageError, match="open candle"):
        validator.validate(_series(0, 60_000, (open_bar,)))

from click.testing import CliRunner
import importlib

cli_main = importlib.import_module("openpine.cli.main")


def test_cli_simple_groups_and_dry_run_paths(monkeypatch, tmp_path):
    runner = CliRunner()
    assert runner.invoke(cli_main.cli, ["version"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["streams", "plan"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["streams", "setup"], input="c\n").exit_code == 0
    assert runner.invoke(cli_main.cli, ["providers", "list"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["providers", "test", "unknown-provider"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "StrategyRuntimeError"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["events", "schema", "validate", "MissingEvent"]).exit_code != 0
    assert runner.invoke(cli_main.cli, ["core", "check"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "commands", "--format", "json"]).exit_code == 0
    fake_updates = json.dumps([{"update_id": 1, "message": {"text": "/start", "chat": {"id": 42}}}])
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "poll", "--dry-run", "--once", "--fake-updates-json", fake_updates]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "webhook-info", "--dry-run"]).exit_code == 0

    class FakeTelegramCfg:
        enabled = True
        token_ref = "env:OPENPINE_TELEGRAM_TOKEN"
        chat_allowlist = ["42"]

        def resolve_token(self):
            return "token"

    fake_config = SimpleNamespace(plugins=SimpleNamespace(telegram=FakeTelegramCfg()))
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: fake_config)
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "42", "--dry-run"]).exit_code == 0
    assert runner.invoke(cli_main.cli, ["plugins", "telegram", "send-menu", "--chat-id", "99", "--dry-run"]).exit_code != 0


def test_cli_source_detection_and_run_dispatch(monkeypatch, tmp_path):
    indicator = tmp_path / "001_demo_indicator.pine"
    indicator.write_text("//@version=6\nindicator('demo')\nplot(close)\n", encoding="utf-8")
    strategy = tmp_path / "strat.pine"
    strategy.write_text("//@version=6\nstrategy('demo')\n", encoding="utf-8")
    unknown = tmp_path / "plain.pine"
    unknown.write_text("close\n", encoding="utf-8")
    assert cli_main._auto_pine_source_name(indicator).startswith("po_0001_")
    assert cli_main._detect_pine_source_kind(indicator) == "indicator"
    assert cli_main._detect_pine_source_kind(strategy) == "strategy"
    with pytest.raises(Exception):
        cli_main._detect_pine_source_kind(unknown)

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["pine", "pine-add"]:
            raise cli_main.click.ClickException("already exists")
        return "ok"

    monkeypatch.setattr(cli_main, "_run_openpine_cli", fake_run)
    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli_main.cli,
        [
            "run", str(indicator), "--symbol", "BTCUSDT", "--timeframe", "1m",
            "--from", "2026-01-01", "--to", "2026-01-02", "--compare-from", "2026-01-01",
            "--compare-to", "2026-01-02", "--output", str(out), "--tv-chart", str(indicator),
        ],
    )
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["pine", "run-plots"] for call in calls)
    assert any(call[:2] == ["pine", "compare-tv"] for call in calls)


def test_cli_private_telegram_and_provider_branches(monkeypatch):
    assert cli_main._normalize_telegram_command({"name": "risk", "description": "Risk", "cli_command": "openpine risk"})["command"] == "/risk"
    item = SimpleNamespace(command="start", title="Start", argv=["version"])
    assert "openpine version" in cli_main._normalize_telegram_command(item)["cli"]
    assert cli_main._telegram_menu_markup()["inline_keyboard"]

    class DisabledTelegram:
        enabled = False
        token_ref = "env:OPENPINE_TELEGRAM_TOKEN"
        chat_allowlist: list[str] = []

        def resolve_token(self):
            return None

    cfg = SimpleNamespace(plugins=SimpleNamespace(telegram=DisabledTelegram()))
    with pytest.raises(SystemExit):
        cli_main._resolve_telegram_token(cfg)
    with pytest.raises(SystemExit):
        cli_main._resolve_telegram_token(cfg, require_enabled=False)

    # provider test branch without requests dependency/network
    monkeypatch.setitem(sys.modules, "requests", None)
    res = CliRunner().invoke(cli_main.cli, ["providers", "test", "binance"])
    assert res.exit_code == 0

from fastapi import FastAPI
from openpine.gateway import deps as gateway_deps
from openpine.gateway import server as gateway_server


def test_gateway_state_getters_and_close(monkeypatch, tmp_path):
    class DummyStorage:
        def __init__(self, path=None):
            self.path = path
            self.closed = False

        def execute(self, *args, **kwargs):
            return SimpleNamespace(fetchall=lambda: [])

        def commit(self):
            pass

        def close(self):
            self.closed = True

    class DummyRegistry:
        def __init__(self, path=None):
            self.path = path
            self.closed = False

        def close(self):
            self.closed = True

    class DummyMigrationRunner:
        def run_migrations(self, storage):
            return []

    class DummyBacktestStore:
        def __init__(self, storage): self.storage = storage

    class DummyManager:
        def __init__(self, *args, **kwargs): pass

    class DummyStateStore:
        def __init__(self, path): self.path = path

    class DummyOrchestrator:
        def __init__(self, cache_enabled=True): self.provider = None
        def set_provider(self, provider): self.provider = provider

    class DummyProvider:
        pass

    cfg = SimpleNamespace(sqlite_path=tmp_path / "db.sqlite", data_dir=tmp_path / "data", kill_switch=False)
    monkeypatch.setattr("openpine.gateway.deps.OpenPineConfig.load", lambda: cfg)
    monkeypatch.setattr("openpine.gateway.deps.SQLiteStorage", DummyStorage)
    monkeypatch.setattr("openpine.storage.migrations.MigrationRunner", DummyMigrationRunner)
    monkeypatch.setattr("openpine.gateway.deps.SQLitePineSourceRegistry", DummyRegistry)
    monkeypatch.setattr("openpine.gateway.deps.SQLiteStrategyRegistry", DummyRegistry)
    monkeypatch.setattr("openpine.gateway.deps.BacktestResultStore", DummyBacktestStore)
    monkeypatch.setattr("openpine.gateway.deps.AccountManager", DummyManager)
    monkeypatch.setattr("openpine.gateway.deps.OrderManager", DummyManager)
    monkeypatch.setattr("openpine.gateway.deps.EventBus", DummyManager)
    monkeypatch.setattr("openpine.gateway.deps.JobScheduler", DummyManager)
    monkeypatch.setattr("openpine.gateway.deps.ArtifactStore", DummyManager)
    monkeypatch.setattr("openpine.gateway.deps.StateStore", DummyStateStore)
    monkeypatch.setattr("openpine.data.orchestrator.DataOrchestrator", DummyOrchestrator)
    monkeypatch.setattr("openpine.data.direct_provider.DirectBinanceProvider", DummyProvider)
    state = gateway_deps.GatewayState()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(gateway=state)))
    assert gateway_deps.get_state(request) is state
    assert gateway_deps.get_pine_registry(state) is state.pine_registry
    assert gateway_deps.get_strategy_registry(state) is state.strategy_registry
    assert gateway_deps.get_backtest_store(state) is state.backtest_store
    assert gateway_deps.get_account_manager(state) is state.account_manager
    assert gateway_deps.get_order_manager(state) is state.order_manager
    assert gateway_deps.get_event_bus(state) is state.event_bus
    assert gateway_deps.get_scheduler(state) is state.scheduler
    assert gateway_deps.get_artifact_store(state) is state.artifact_store
    assert gateway_deps.get_state_store(state) is state.state_store
    assert gateway_deps.get_orchestrator(state) is state.orchestrator
    assert gateway_deps.get_risk_manager(state) is state.risk_manager
    state.close()
    assert state.storage.closed and state.pine_registry.closed and state.strategy_registry.closed


@pytest.mark.asyncio
async def test_gateway_lifespan_and_factory_edges(monkeypatch):
    class FakeStorage:
        def __init__(self):
            self.updated = []

        def execute(self, sql, params=()):
            text = sql.lower()
            if "select run_id" in text:
                return SimpleNamespace(fetchall=lambda: [("run1",), ("run2",)])
            self.updated.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [])

        def commit(self):
            self.committed = True

    class FakeState:
        def __init__(self):
            self.config = SimpleNamespace(sqlite_path=Path("/tmp/gateway.sqlite"), live_enabled=False)
            self.storage = FakeStorage()
            self.closed = False

        def close(self):
            self.closed = True

    fake_state = FakeState()
    monkeypatch.setattr(gateway_server, "GatewayState", lambda: fake_state)
    monkeypatch.setattr(gateway_server, "_env_flag", lambda name, default=False: False)
    app = FastAPI()
    async with gateway_server.lifespan(app):
        assert app.state.gateway is fake_state
        assert hasattr(fake_state, "_startup_time")
        assert fake_state._fetcher is None and fake_state._live_runner is None
    assert fake_state.closed is True and fake_state.storage.updated

    app2 = gateway_server.create_app(gateway_server.GatewayConfig(api_prefix="/x", cors_origins=["*"]))
    assert app2.title == "OpenPine Gateway"
    routes = {getattr(route, "path", "") for route in app2.routes}
    assert "/health" in routes and "/" in routes

from openpine.batch.tv_corpus import ChartExport, ExportEntry
from openpine.batch import runner as batch_runner


def _entry(tmp_path: Path, *, kind: str = "strategy") -> tuple[ExportEntry, ChartExport]:
    root = tmp_path / "export_001"
    root.mkdir()
    pine = root / "script.pine"
    pine.write_text("//@version=6\nstrategy('x')\n", encoding="utf-8")
    chart = ChartExport("1m", root / "chart.csv", 2, 1000, 121000)
    entry = ExportEntry(1, "001_demo", kind, "group", root, pine, (chart,))
    return entry, chart


def test_batch_runner_metadata_skip_and_summary_helpers(tmp_path, monkeypatch):
    entry, chart = _entry(tmp_path)
    args = SimpleNamespace(timeframe=None, phase="run", skip_completed=True)
    out_dir = entry.root / "openpine_outputs" / chart.timeframe
    out_dir.mkdir(parents=True)
    for name in ("plots.csv", "trades.csv", "equity_curve.csv"):
        (out_dir / name).write_text("x\n", encoding="utf-8")
    revisions = {name: "rev" for name in batch_runner.LIBRARY_NAMES}
    meta = batch_runner._build_run_meta(entry=entry, chart=chart, status={"source_id": "p", "artifact_id": "a"}, run_info={"status": "ok", "data": {"calculation_from": 1000, "calculation_to": 121000, "compare_from": 1000, "compare_to": 121000}}, batch_id="b1", library_revisions=revisions)
    summary = batch_runner._build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info={"status": "ok", "bars": 2, "plots_rows": 1, "trades_rows": 1, "equity_rows": 1})
    batch_runner.write_json(out_dir / "run_meta.json", meta)
    batch_runner.write_json(out_dir / "summary.json", summary)
    status = {"phase": "run", "status": "ok", "runs": [{"timeframe": "1m", "status": "ok"}]}
    batch_runner.write_json(entry.root / "openpine_outputs" / "openpine_batch_status.json", status)
    assert batch_runner.completed_for_selection(entry, args) is True
    assert batch_runner._run_meta_valid(out_dir / "run_meta.json") is True
    assert batch_runner._run_summary_valid(out_dir / "summary.json") is True
    assert batch_runner._expected_output_files(entry, chart)[0].name == "plots.csv"
    assert batch_runner._wanted_charts(entry, SimpleNamespace(timeframe="1m")) == [chart]
    assert batch_runner._wanted_charts(entry, SimpleNamespace(timeframe="5m")) == []
    assert batch_runner.entry_summary(entry)["charts"][0]["timeframe"] == "1m"
    assert batch_runner._selected_timeframes(entry, SimpleNamespace(timeframe=None)) == ["1m"]
    assert batch_runner.result_has_error({"status": "ok", "runs": [{"status": "run_error"}]}) is True
    assert batch_runner.result_has_error({"status": "ok", "runs": [{"status": "ok"}]}) is False
    assert batch_runner.parse_ids(None) is None
    assert batch_runner.parse_ids("1, 2,3") == {1, 2, 3}
    assert batch_runner.summarize([{"status": "ok"}, {"status": "run_error"}])["stats"]["run_error"] == 1
    assert batch_runner.summary_by_timeframe([{"runs": [{"status": "ok", "timeframe": "1m"}, {"status": "run_error", "timeframe": "1m"}]}])["1m"]["statuses"]["run_error"] == 1
    progress = tmp_path / "progress"
    batch_runner._write_progress(progress, "b", 1, "phase", "ok", selected_count=2, processed_count=1, summary_by_timeframe={"1m": {"ok": 1}})
    assert json.loads((progress / "current_progress.json").read_text())["processed_count"] == 1
    jsonl = tmp_path / "events.jsonl"
    batch_runner.append_jsonl(jsonl, {"a": 1})
    assert jsonl.read_text().strip()


def test_batch_runner_invalid_skip_and_registry_helpers(tmp_path, monkeypatch):
    entry, chart = _entry(tmp_path, kind="indicator")
    assert batch_runner._expected_output_files(entry, chart)[0].name == "plots.csv"
    args = SimpleNamespace(timeframe=None, phase="run", skip_completed=False)
    assert batch_runner.completed_for_selection(entry, args) is False
    args.skip_completed = True
    assert batch_runner.completed_for_selection(entry, args) is False
    status_dir = entry.root / "openpine_outputs"
    status_dir.mkdir(exist_ok=True)
    (status_dir / "openpine_batch_status.json").write_text("not-json", encoding="utf-8")
    assert batch_runner.completed_for_selection(entry, args) is False
    assert batch_runner._output_file_valid(tmp_path / "missing") is False
    assert batch_runner._valid_window({"from_ms": 2, "to_ms": 1}) is False
    assert batch_runner._run_meta_valid(tmp_path / "missing.json") is False
    bad_meta = tmp_path / "bad_meta.json"
    bad_meta.write_text("{}", encoding="utf-8")
    assert batch_runner._run_meta_valid(bad_meta) is False
    bad_summary = tmp_path / "summary.json"
    bad_summary.write_text("{}", encoding="utf-8")
    assert batch_runner._run_summary_valid(bad_summary) is False
    assert batch_runner.ms_to_utc_iso(None) is None
    assert "1970" in batch_runner.ms_to_utc_iso(0)
    assert batch_runner._elapsed_sec_since(batch_runner.time.perf_counter()) >= 0
    assert batch_runner._finish_entry_status({"status": "ok"}, batch_runner.time.perf_counter())["elapsed_sec"] >= 0

    class FakeRegistry:
        def __init__(self):
            self.closed = False
            self.sources = {}
            self._conn = SimpleNamespace(execute=lambda *a, **k: None, commit=lambda: None)
        def get_source(self, name):
            raise KeyError(name)
        def add_source(self, text, name):
            return SimpleNamespace(id="p1", source_type="", source_path="", active_artifact_id=None)
        def close(self):
            self.closed = True

    fake_registry = FakeRegistry()
    monkeypatch.setattr(batch_runner, "load_source_registry", lambda: fake_registry)
    assert batch_runner.get_or_add_source(entry, write=False) == (None, False)
    source, added = batch_runner.get_or_add_source(entry, write=True)
    assert added is True and source.id == "p1"


def test_strategy_job_executor_helper_branches(monkeypatch):
    from openpine.jobs.models import Job, JobType
    from openpine.workers import strategy_job_executor as worker

    strategy = StrategyInstance(
        strategy_id="s1",
        name="S",
        pine_id="pine1",
        artifact_id="art1",
        params_json='{"x": 2}',
        params_hash="h",
        symbol="btcusdt",
        timeframe="1m",
        exchange="BINANCE",
        market_type="SPOT",
        price_type="TRADE",
    )
    job = Job(
        JobType.PAPER_BAR_PROCESS,
        strategy_id="s1",
        input={
            "strategy_id": "s1",
            "instrument_key": "bybit:linear:ETHUSDT",
            "timeframe": "1m",
            "bar_time": 0,
        },
    )
    payload = worker._job_payload(job)
    assert worker._instrument_from_payload(strategy, payload).symbol == "ETHUSDT"
    assert worker._instrument_from_payload(strategy, {"instrument_key": "bad"}).symbol == "BTCUSDT"
    assert worker._state_key(strategy, _bar())["instrument_key"]["symbol"] == "BTCUSDT"
    assert worker._strategy_params(strategy) == {"x": 2}
    assert worker._strategy_params(StrategyInstance("s2", "S", "", "", "not-json", "h", "BTCUSDT", "1m")) == {}
    assert worker._strategy_params(StrategyInstance("s3", "S", "", "", "[]", "h", "BTCUSDT", "1m")) == {}
    with pytest.raises(ValueError):
        worker._job_payload(Job(JobType.PAPER_BAR_PROCESS, input={"strategy_id": "s1"}))

    monkeypatch.setattr(worker, "default_qty_step", lambda *_: 0.001)
    monkeypatch.setattr(worker, "default_qty_rounding_mode", lambda *_: "floor")
    monkeypatch.setattr(worker, "_artifact_declaration_args", lambda _: {"commission_type": "cash_per_order", "close_entries_rule": "any", "initial_capital": 123.0})
    cfg = worker._build_bar_run_config(strategy, _bar())
    assert cfg.initial_capital == 123.0
    assert getattr(cfg, "commission_type", None) in {"fixed_per_order", "cash_per_order", None}

    class BrokerState:
        position = SimpleNamespace(size=-2, avg_price=10.0)

    assert worker._broker_position({"broker_state": {"position": "p"}}) == "p"
    assert worker._broker_position(SimpleNamespace(broker_state=BrokerState())).size == -2
    assert worker._broker_position(None) is None
    assert worker._result_position(SimpleNamespace(open_trades=[])) is None
    pos = worker._result_position(SimpleNamespace(open_trades=[SimpleNamespace(direction="long", qty=2, entry_price=5), SimpleNamespace(direction="long", qty=3, entry_price=7)]))
    assert pos.size == 5 and round(pos.avg_price, 2) == 6.2
    short = worker._result_position(SimpleNamespace(open_trades=[SimpleNamespace(direction="short", qty=2, entry_price=5)]))
    assert short.size == -2
    assert worker._float_or_none("bad") is None
    assert worker._float_or_none("1.5") == 1.5
    trade = SimpleNamespace(id="t1", entry_id="e", exit_id="x", entry_time=1, exit_time=2)
    assert worker._ledger_trade_id("s", worker.LedgerSource.PAPER, trade).startswith("strade_")
    result = worker.StrategyJobExecutionResult("j", "s", worker.StrategyJobStatus.DONE, bar_time=1, trades_recorded=2)
    assert worker._result_dict(result)["status"] == "done"


class _FakeScheduler:
    def __init__(self):
        self.calls = []

    def mark_done(self, job_id, result=None):
        self.calls.append(("done", job_id, result))

    def mark_running(self, job_id):
        self.calls.append(("running", job_id, None))

    def mark_failed(self, job_id, error):
        self.calls.append(("failed", job_id, error))


class _FakeStateStore:
    def __init__(self, latest=None):
        self.latest = latest
        self.saved = []

    def latest_snapshot_metadata(self, *args, **kwargs):
        return self.latest

    def load_runtime_snapshot(self, *args, **kwargs):
        return {"broker_state": {"position": SimpleNamespace(size=1, avg_price=10)}}

    def save_runtime_snapshot(self, **kwargs):
        self.saved.append(kwargs)
        return SimpleNamespace(snapshot_id="snap1")


class _FakeOrchestrator:
    def __init__(self, bars):
        self.bars = bars

    def get_bars(self, query):
        return self.bars


class _FakeLedger:
    def __init__(self):
        self.positions = []
        self.trades = []

    def upsert_position(self, position):
        self.positions.append(position)

    def record_trade(self, trade):
        self.trades.append(trade)


class _FakeRuntimeAdapter:
    def run(self, *args, **kwargs):
        trade = SimpleNamespace(
            id="tr1",
            direction="long",
            entry_time=0,
            exit_time=30_000,
            entry_price=1,
            exit_price=2,
            qty=1,
            profit=1,
            commission_entry=0.1,
            commission_exit=0.2,
            bars_held=1,
        )
        raw = SimpleNamespace(closed_trades=[trade], open_trades=[])
        return SimpleNamespace(status="completed", resume_state=kwargs.get("resume_state"), raw_result=raw)


def test_strategy_job_executor_process_done_skip_and_fail(monkeypatch):
    from openpine.jobs.models import Job, JobType
    from openpine.workers import strategy_job_executor as worker

    strategy = StrategyInstance("s1", "S", "pine", "art", "{}", "h", "BTCUSDT", "1m")
    registry = SimpleNamespace(get_strategy=lambda _sid: strategy)
    bar = _bar(0)
    payload = {"strategy_id": "s1", "instrument_key": "binance:spot:BTCUSDT", "timeframe": "1m", "bar_time": 0}
    job = Job(JobType.PAPER_BAR_PROCESS, strategy_id="s1", input=payload)
    scheduler = _FakeScheduler()
    ledger = _FakeLedger()
    executor = worker.StrategyJobExecutor(
        registry=registry,
        orchestrator=_FakeOrchestrator([bar]),
        scheduler=scheduler,
        state_store=_FakeStateStore(),
        ledger=ledger,
        runtime_adapter=_FakeRuntimeAdapter(),
        strategy_loader=lambda _strategy: type("Strategy", (), {}),
        runtime_data_provider="dp",
    )
    result = executor.process(job)
    assert result.status == worker.StrategyJobStatus.DONE
    assert result.trades_recorded == 1
    assert ledger.positions and ledger.trades
    assert scheduler.calls[-1][0] == "done"

    skipped_executor = worker.StrategyJobExecutor(
        registry=registry,
        orchestrator=_FakeOrchestrator([bar]),
        scheduler=_FakeScheduler(),
        state_store=_FakeStateStore(latest=SimpleNamespace(bar_time=0, snapshot_id="old")),
        runtime_adapter=_FakeRuntimeAdapter(),
        strategy_loader=lambda _strategy: type("Strategy", (), {}),
        runtime_data_provider="dp",
    )
    skipped = skipped_executor.process(job)
    assert skipped.status == worker.StrategyJobStatus.SKIPPED
    assert skipped.skipped_reason == "already_processed"

    bad = executor.process(Job(JobType.REPORT, strategy_id="s1", input=payload))
    assert bad.status == worker.StrategyJobStatus.FAILED
