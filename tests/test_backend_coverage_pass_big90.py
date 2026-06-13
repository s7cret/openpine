from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.batch import runner as br
from openpine.batch.tv_corpus import ChartExport, ExportEntry
from openpine.cli import runtime_helpers as rh
from openpine.gateway import live_runner as lr


def _strategy(**kw):
    base = dict(
        strategy_id="s1",
        artifact_id="a1",
        params_hash="h1",
        exchange="BINANCE",
        market_type="SPOT",
        symbol="btcusdt",
        timeframe="1m",
        pine_id="p1",
        name="Strategy",
        enabled=True,
        status="running",
        params_json="{}",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _entry(tmp_path: Path, *, kind: str = "strategy") -> ExportEntry:
    root = tmp_path / f"entry_{kind}"
    root.mkdir(parents=True, exist_ok=True)
    pine = root / "script.pine"
    pine.write_text("strategy('x')\n" if kind == "strategy" else "indicator('x')\n", encoding="utf-8")
    chart = ChartExport("1m", root / "chart.csv", 3, 0, 180_000)
    return ExportEntry(42, "folder", kind, "grp", root, pine, (chart,))


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close, close, close, 1.0, True)


def _series(times=(0, 60_000)) -> BarSeries:
    bars = tuple(_bar(t, float(i + 1)) for i, t in enumerate(times))
    q = BarQuery(bars[0].instrument, bars[0].timeframe, min(times), max(times) + 60_000, gap_policy="allow_with_metadata")
    c = CoverageReport(q.start_ms, q.end_ms, bars[0].time, bars[-1].time_close, source_mix=("test",))
    return BarSeries(q, bars, c)


class _Console:
    def __init__(self):
        self.lines: list[str] = []
    def print(self, *parts, **kwargs):
        self.lines.append(" ".join(str(p) for p in parts))


class _FakeRow:
    def __init__(self, one=None, all_=None):
        self._one = one
        self._all = all_ if all_ is not None else []
    def fetchone(self):
        return self._one
    def fetchall(self):
        return self._all


class _OrderStorage:
    def __init__(self, *, changes=1, fail=False):
        self.calls = []
        self.changes = changes
        self.fail = fail
        self.commits = 0
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.fail and "INSERT" in sql:
            raise RuntimeError("insert boom")
        if "source_text" in sql:
            return _FakeRow(("tpPct = input.float(1)\nslPct = input.float(2)",))
        if "SELECT changes" in sql:
            return _FakeRow((self.changes,))
        return _FakeRow(None)
    def commit(self):
        self.commits += 1
        if self.fail:
            raise RuntimeError("commit boom")


def test_live_runner_lifecycle_loop_and_strategy_edges(monkeypatch):
    class _Task:
        def __init__(self):
            self.cancelled = False
        def done(self):
            return False
        def cancel(self):
            self.cancelled = True
    task = _Task()
    monkeypatch.setattr(lr.asyncio, "get_event_loop", lambda: SimpleNamespace(create_task=lambda coro: (coro.close(), task)[1]))
    runner = lr.LiveStrategyRunner(registry=None)
    runner.start()
    runner.start()  # already running branch
    runner.stop()
    assert task.cancelled

    class Registry:
        def __init__(self, strategies):
            self._strategies = strategies
        def list_strategies(self):
            return self._strategies

    processed = []
    runner = lr.LiveStrategyRunner(registry=Registry([_strategy(), _strategy(enabled=False), _strategy(status="paused")]))
    async def fake_process(strategy, now_ms):
        processed.append((strategy.strategy_id, now_ms))
    monkeypatch.setattr(runner, "_process_strategy", fake_process)
    asyncio.run(runner._check_all_strategies())
    assert len(processed) == 1

    async def bad_process(strategy, now_ms):
        raise RuntimeError("boom")
    monkeypatch.setattr(runner, "_process_strategy", bad_process)
    asyncio.run(runner._check_all_strategies())

    calls = {"n": 0}
    async def one_bad_check():
        calls["n"] += 1
        runner._running = False
        raise RuntimeError("loop boom")
    monkeypatch.setattr(runner, "_check_all_strategies", one_bad_check)
    runner._running = True
    asyncio.run(runner._run_loop())
    assert calls["n"] == 1


def test_live_runner_process_strategy_and_order_paths(monkeypatch):
    strategy = _strategy()
    runner = lr.LiveStrategyRunner(storage=_OrderStorage(), state_store=None)
    monkeypatch.setattr(runner, "_run_mini_backtest", lambda s, t: [{"side": "buy", "entry_price": 10.0, "qty": 2, "entry_time": t, "net_pnl": 3.5}])
    seen = []
    async def fake_process_orders(strategy_arg, orders):
        seen.append((strategy_arg.strategy_id, orders))
    monkeypatch.setattr(runner, "_process_orders", fake_process_orders)
    # two new bars, bounded catch-up path
    runner._strategy_states[strategy.strategy_id] = lr.StrategyBarState(strategy.strategy_id, 60_000)
    asyncio.run(runner._process_strategy(strategy, 240_000 + 1))
    assert seen and seen[0][1][0]["qty"] == 2

    # no new bar branch
    seen.clear()
    runner._strategy_states[strategy.strategy_id] = lr.StrategyBarState(strategy.strategy_id, 180_000)
    asyncio.run(runner._process_strategy(strategy, 240_000 + 1))
    assert seen == []

    # invalid timeframe bubbles from provider contract, covered as a fail-fast edge
    with pytest.raises(Exception):
        asyncio.run(runner._process_strategy(_strategy(timeframe="bad-tf"), 240_000 + 1))

    # order processing without storage and with DB duplicate/error branches
    runner = lr.LiveStrategyRunner(storage=None)
    asyncio.run(runner._process_orders(strategy, [{"side": "sell", "price": 9.5, "qty": 1}]))
    runner.storage = _OrderStorage(changes=0)
    asyncio.run(runner._process_orders(strategy, [{"side": "buy", "price": 10.0, "qty": 1}]))
    runner.storage = _OrderStorage(fail=True)
    asyncio.run(runner._process_orders(strategy, [{"side": "buy", "price": 10.0, "qty": 1}]))


def test_live_runner_resume_snapshot_and_direct_fetch_edges(monkeypatch):
    strategy = _strategy()
    class Snapshot:
        def __init__(self, bar_time, state_data):
            self.bar_time = bar_time
            self.state_data = state_data
    class StateStore:
        def __init__(self, snapshot=None):
            self.snapshot = snapshot
            self.saved = []
            self.invalidated = []
        def latest_snapshot_metadata(self, *args, **kwargs):
            return self.snapshot
        def load_latest_compatible(self, *args, **kwargs):
            return self.snapshot
        def save_runtime_snapshot(self, **kwargs):
            self.saved.append(kwargs)
        def mark_invalid(self, strategy_id, since_bar_time=None):
            self.invalidated.append((strategy_id, since_bar_time))

    runner = lr.LiveStrategyRunner(state_store=StateStore(Snapshot(120_000, {"runtime_state": {}, "bar_index": 1})))
    assert runner._latest_processed_bar_time(strategy, 180_000) == 120_000
    assert runner._load_resume_snapshot(strategy, instrument_key={}, timeframe={}, at_or_before_bar_time=180_000).bar_time == 120_000
    runner._save_resume_snapshot(strategy, result=SimpleNamespace(resume_state={"runtime_state": {}}), instrument_key={}, timeframe={}, bar_time=180_000, data_fingerprint="abc")
    assert runner.state_store.saved
    runner._mark_resume_snapshot_invalid(strategy, 120_000)
    assert runner.state_store.invalidated

    class Direct:
        def __init__(self, *args, **kwargs):
            self.args = args
        def fetch_bars(self, query):
            return _series((query.start_ms,))
    monkeypatch.setattr("openpine.data.provider_adapter.create_local_marketdata_provider_adapter", lambda: Direct())
    q = BarQuery(InstrumentKey("binance", "spot", "BTCUSDT"), parse_timeframe("1m"), 0, 60_000, gap_policy="allow_with_metadata")
    assert list(runner._fetch_direct(q).bars)

    # no runtime state, future snapshot, invalid resume index, large resume index
    for snap in [
        Snapshot(0, {"bar_index": 0}),
        Snapshot(999_999, {"runtime_state": {}, "bar_index": 0}),
        Snapshot(60_000, {"runtime_state": {}, "bar_index": "bad"}),
        Snapshot(60_000, {"runtime_state": {}, "bar_index": 999_999}),
    ]:
        runner = lr.LiveStrategyRunner(state_store=StateStore(snap), orchestrator=SimpleNamespace(load_bars=lambda q: _series((0, 60_000))))
        # monkeypatch heavy runtime modules by making load_strategy fail late into except for coverage
        assert runner._run_mini_backtest(strategy, 180_000) in (None, [])


def test_batch_runner_registry_revision_and_registry_branches(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    fake_mod = ModuleType("openpine_fake_mod")
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    (repo / ".git").mkdir(parents=True)
    pkg.mkdir()
    fake_mod.__file__ = str(pkg / "__init__.py")
    monkeypatch.setattr(br, "LIBRARY_NAMES", ("openpine_fake_mod", "missing_mod_for_test"))
    sys.modules["openpine_fake_mod"] = fake_mod
    monkeypatch.setattr(br.subprocess, "check_output", lambda *a, **k: b"abcdef12\n")
    revisions = br._get_library_revisions()
    assert revisions["openpine_fake_mod"] == "abcdef12"
    assert revisions["missing_mod_for_test"] == "unknown"

    class SourceRegistry:
        def __init__(self):
            self._conn = SimpleNamespace(execute=lambda *a, **k: None, commit=lambda: None)
            self.closed = False
        def get_source(self, name):
            raise KeyError(name)
        def add_source(self, source_text, name):
            return SimpleNamespace(id="src", source_type=None, source_path=None)
        def close(self):
            self.closed = True
    monkeypatch.setattr(br, "load_source_registry", SourceRegistry)
    source, created = br.get_or_add_source(entry, write=True)
    assert created and source.id == "src"

    class SourceRegistryExisting(SourceRegistry):
        def get_source(self, name):
            return SimpleNamespace(id="existing")
    monkeypatch.setattr(br, "load_source_registry", SourceRegistryExisting)
    source, created = br.get_or_add_source(entry, write=True)
    assert not created and source.id == "existing"

    class StrategyRegistry:
        def __init__(self, existing=False):
            self.existing = existing
        def list_strategies(self):
            return [SimpleNamespace(name=br.strategy_name(entry, "1m"), strategy_id="old")] if self.existing else []
        def register_strategy(self, **kwargs):
            return SimpleNamespace(strategy_id="new")
        def update_status(self, sid, status):
            self.updated = (sid, status)
        def close(self):
            pass
    monkeypatch.setattr(br, "load_strategy_registry", lambda: StrategyRegistry(existing=False))
    assert br.ensure_strategy_instance(entry, SimpleNamespace(id="src"), "artifact", "1m") == ("new", True)
    monkeypatch.setattr(br, "load_strategy_registry", lambda: StrategyRegistry(existing=True))
    assert br.ensure_strategy_instance(entry, SimpleNamespace(id="src"), "artifact", "1m") == ("old", False)


def test_runtime_helper_error_and_indicator_output_paths(tmp_path, monkeypatch):
    console = _Console()
    with pytest.raises(SystemExit):
        rh._get_strategy_or_exit(registry=SimpleNamespace(get_strategy=lambda sid: (_ for _ in ()).throw(KeyError(sid))), strategy_id="missing", console=console)
    assert "Strategy not found" in "\n".join(console.lines)

    class Config:
        def __init__(self, symbol=None, timeframe=None, start_time=None, end_time=None, exchange=None, market_type=None, commission_type=None, score_start_time=None, score_end_time=None, plot_from_ms=None, plot_to_ms=None, exit_matching=None, qty_step=None, qty_rounding_mode=None, **kwargs):
            self.__dict__.update(locals())
            self.__dict__.pop("self", None)
    strategy = _strategy(exchange="BINANCE", market_type="SPOT")
    cfg = rh._build_strategy_backtest_config(strategy=strategy, decl_args={"commission_type": "cash_per_order"}, start_ms=0, end_ms=60_000, warmup_bars=5, effective_pre_bars=2, capture_plots=True, capture_from_ms=0, capture_to_ms=60_000, config_cls=Config)
    assert cfg.commission_type == "fixed_per_order"
    replay_cfg = rh._build_strategy_replay_config(strategy=strategy, decl_args={"close_entries_rule": "any"}, start_ms=0, end_ms=1, config_cls=Config)
    assert replay_cfg.exit_matching == "ANY"

    assert rh._plot_record_count(None) == 0
    assert rh._plot_record_count([1, 2]) == 2
    assert rh._plot_record_count(SimpleNamespace(get_records=lambda: [1, 2, 3])) == 3

    timings = {}
    class Deps:
        def export_plot_records(self, records, path, from_ms=None, to_ms=None):
            Path(path).write_text("plot\n", encoding="utf-8")
            return len(records)
        def write_json(self, path, payload):
            Path(path).write_text(json.dumps(payload), encoding="utf-8")
    prepared = SimpleNamespace(
        generated_class=object,
        bars=[SimpleNamespace(time=0, time_close=60000, open=1, high=1, low=1, close=1, volume=1)],
        provider=object(),
        compare_from_ms=0,
        compare_to_ms=60_000,
        source=SimpleNamespace(id="pine", active_artifact_id="art"),
        start_ms=0,
        end_ms=60_000,
        data_fetch_info={"source": "test"},
    )
    monkeypatch.setattr(rh, "_run_indicator_plot_runtime", lambda **kw: (SimpleNamespace(plots=[1, 2]), 0.1))
    rh._write_indicator_plot_run_outputs(deps=Deps(), prepared=prepared, name="pine", symbol="BTCUSDT", timeframe="1m", exchange="binance", market_type="spot", output_dir=str(tmp_path / "out"), progress_every=0, timings=timings, start_total=0.0, perf_counter=lambda: 1.0, console=console)
    assert (tmp_path / "out" / "plots.csv").exists()
    assert (tmp_path / "out" / "run_meta.json").exists()


def test_live_runner_mini_backtest_success_resume_and_fallback(monkeypatch):
    import openpine.runtime.engine as runtime_engine
    import openpine.data.direct_data_provider as direct_data_provider

    strategy = _strategy()

    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeAdapter:
        calls = 0
        def run(self, strategy_class, bars, config, params, resume_state=None, runtime_data_provider=None):
            type(self).calls += 1
            if resume_state and type(self).calls == 1:
                raw = SimpleNamespace(trades=[], order_lifecycle=[])
            else:
                raw = SimpleNamespace(
                    trades=[SimpleNamespace(entry_time=180_000, direction="long", entry_price=12.5, qty=3, net_pnl=1.2)],
                    order_lifecycle=[SimpleNamespace(created_at=180_000, side="buy", price=12.5, quantity=3, order_type="market")],
                )
            return SimpleNamespace(raw_result=raw, resume_state={"runtime_state": {"x": 1}, "bar_index": 2})

    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Generated", (), {}))
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", FakeAdapter)
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", lambda *a, **k: object())

    class ArtifactStore:
        def get_artifact(self, artifact_id, pine_id):
            return {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {"commission_type": "cash_per_order", "initial_capital": 1234, "process_orders_on_close": True}}}}}

    class Store:
        def __init__(self, snapshot):
            self.snapshot = snapshot
            self.saved = []
            self.invalidated = []
        def latest_snapshot_metadata(self, *args, **kwargs):
            return self.snapshot
        def load_latest_compatible(self, *args, **kwargs):
            return self.snapshot
        def save_runtime_snapshot(self, **kwargs):
            self.saved.append(kwargs)
        def mark_invalid(self, *args, **kwargs):
            self.invalidated.append((args, kwargs))

    snapshot = SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {"old": 1}, "bar_index": 1})
    store = Store(snapshot)
    runner = lr.LiveStrategyRunner(orchestrator=SimpleNamespace(load_bars=lambda query: _series((0, 60_000, 120_000, 180_000))), artifact_store=ArtifactStore(), state_store=store, storage=_OrderStorage())
    orders = runner._run_mini_backtest(strategy, 180_000)
    assert orders and len(orders) == 2
    assert store.saved

    # resume replay error triggers full-window retry branch
    class RetryAdapter(FakeAdapter):
        calls = 0
        def run(self, strategy_class, bars, config, params, resume_state=None, runtime_data_provider=None):
            type(self).calls += 1
            if resume_state is not None and type(self).calls == 1:
                raise RuntimeError("resume config hash mismatch")
            return super().run(strategy_class, bars, config, params, resume_state=None, runtime_data_provider=runtime_data_provider)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", RetryAdapter)
    store2 = Store(SimpleNamespace(bar_time=60_000, state_data={"runtime_state": {"old": 1}, "bar_index": 1}))
    runner2 = lr.LiveStrategyRunner(orchestrator=runner.orchestrator, artifact_store=ArtifactStore(), state_store=store2, storage=_OrderStorage())
    assert runner2._run_mini_backtest(strategy, 180_000)
    assert store2.invalidated

    # empty bar series branch
    runner3 = lr.LiveStrategyRunner(orchestrator=SimpleNamespace(load_bars=lambda query: _series(tuple())), artifact_store=ArtifactStore(), state_store=None)
    assert runner3._run_mini_backtest(strategy, 180_000) is None

def test_cli_state_accounts_and_providers_deeper(monkeypatch, tmp_path):
    import importlib
    cm = importlib.import_module("openpine.cli.main")
    from click.testing import CliRunner

    runner = CliRunner()

    class Cfg:
        data_dir = tmp_path / "data"
        config_dir = tmp_path
        sqlite_path = tmp_path / "db.sqlite"
        duckdb_path = tmp_path / "duck.duckdb"
        kill_switch = False
        live_enabled = False
        timezone = "UTC"
        plugins = SimpleNamespace(telegram=SimpleNamespace(enabled=False, chat_allowlist=[]))
    (Cfg.data_dir / "state" / "strategy_id=s1").mkdir(parents=True)
    snap_file = Cfg.data_dir / "state" / "strategy_id=s1" / "snap_1.state.msgpack"
    snap_file.write_text("x", encoding="utf-8")
    snap_file.with_suffix(".debug.json").write_text(json.dumps({"last_processed_bar_time": 123}), encoding="utf-8")

    import openpine.config as config_mod
    monkeypatch.setattr(config_mod.OpenPineConfig, "load", classmethod(lambda cls: Cfg))

    class Snap:
        def __init__(self, status="active", bar_time=1000):
            self.snapshot_id = "snapabcdef123456"
            self.strategy_id = "s1"
            self.bar_time = bar_time
            self.size_bytes = 4096
            self.saved_at = 2000
            self.status = status
            self.artifact_id = "a1"
    class Store:
        snapshots = [Snap("active"), Snap("invalid", 500)]
        def __init__(self, path):
            self.path = path
        def list_snapshots(self, strategy_id):
            return list(type(self).snapshots)
    import openpine.state.store as state_store_mod
    monkeypatch.setattr(state_store_mod, "StateStore", Store)

    assert runner.invoke(cm.cli, ["state", "list", "--strategy", "s1"]).exit_code == 0
    assert runner.invoke(cm.cli, ["state", "list"]).exit_code == 0
    assert runner.invoke(cm.cli, ["state", "invalid"]).exit_code == 0
    Store.snapshots = []
    assert runner.invoke(cm.cli, ["state", "list", "--strategy", "s1"]).exit_code == 0
    assert runner.invoke(cm.cli, ["state", "rebuild", "s1"]).exit_code == 0

    class Rebuilder:
        def __init__(self, **kwargs): pass
        def rebuild(self, strategy_id, from_bar_time):
            return SimpleNamespace(strategy_id=strategy_id, artifact_id="a1", bar_time=from_bar_time)
    import openpine.recovery as recovery_mod
    monkeypatch.setattr(recovery_mod, "StateRebuilder", Rebuilder)
    Store.snapshots = [Snap("active", 777)]
    assert runner.invoke(cm.cli, ["state", "rebuild", "s1"]).exit_code == 0
    assert runner.invoke(cm.cli, ["state", "rebuild", "s1", "--from-bar", "42"]).exit_code == 0

    class Storage:
        def __init__(self, *a, **k): self.committed=False; self.rolled=False
        def commit(self): self.committed=True
        def rollback(self): self.rolled=True
        def close(self): pass
        def execute(self, *a, **k): return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)
    import openpine.storage as storage_mod
    monkeypatch.setattr(storage_mod, "SQLiteStorage", Storage)

    import openpine.accounts as accounts_mod
    import openpine.accounts.models as models
    class Manager:
        mode = "normal"
        def __init__(self, storage): pass
        def list_accounts(self):
            if self.mode == "empty": return []
            if self.mode == "bad":
                return [SimpleNamespace(name="bad", id="bad123456789", account_id="bad123456789", exchange="weird", provider="weird", market_type="spot", mode=models.AccountType.LIVE, account_type=models.AccountType.LIVE, live_enabled=False, config={}, api_key_ref=None, api_secret_ref=None)]
            return [SimpleNamespace(name="good", id="good123456789", account_id="good123456789", exchange="binance", provider="binance", market_type="spot", mode=models.AccountType.PAPER, account_type=models.AccountType.PAPER, live_enabled=False, config={"strategy_id":"s1"}, api_key_ref="k", api_secret_ref="s")]
        def create_account(self, **kwargs):
            if kwargs["name"] == "boom": raise RuntimeError("boom")
            return SimpleNamespace(account_id="created123456", **kwargs)
    monkeypatch.setattr(accounts_mod, "AccountManager", Manager)
    Manager.mode = "empty"
    assert runner.invoke(cm.cli, ["accounts", "list"]).exit_code == 0
    Manager.mode = "normal"
    assert runner.invoke(cm.cli, ["accounts", "list", "--strategy", "s1"]).exit_code == 0
    assert runner.invoke(cm.cli, ["accounts", "test", "good"]).exit_code == 0
    Manager.mode = "bad"
    assert runner.invoke(cm.cli, ["accounts", "test", "bad"]).exit_code != 0
    assert runner.invoke(cm.cli, ["accounts", "add", "--name", "boom", "--exchange", "binance", "--api-key", "abc", "--secret", "secret"]).exit_code != 0

    # provider list/test branches including ImportError and request exception edges
    assert runner.invoke(cm.cli, ["providers", "list"]).exit_code == 0
    assert runner.invoke(cm.cli, ["providers", "test", "unknown_provider"]).exit_code != 0
    req = SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network")))
    monkeypatch.setitem(sys.modules, "requests", req)
    assert runner.invoke(cm.cli, ["providers", "test", "binance"]).exit_code != 0
