from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.batch import runner as br
from openpine.batch.tv_corpus import ChartExport, ExportEntry
from openpine.cli import data as cli_data
from openpine.export import plots as export_plots
from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig, StrategyBarState
from openpine.gateway.routes import accounts_data
from openpine.gateway.routes import backtest as backtest_routes
from openpine.gateway.routes import dashboard, strategies
from openpine.notifications import telegram


def _entry(tmp_path: Path, *, kind: str = "indicator") -> ExportEntry:
    root = tmp_path / "entry"
    root.mkdir(parents=True, exist_ok=True)
    pine = root / "script.pine"
    pine.write_text("indicator('x')\n" if kind == "indicator" else "strategy('x')\n", encoding="utf-8")
    chart = ChartExport("1m", root / "chart.csv", 3, 0, 120_000)
    return ExportEntry(7, "folder", kind, "grp", root, pine, (chart,))


def _bar(t: int = 0, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close, close, close, 1.0, True)


def _series(start: int = 0, end: int = 60_000, bars: tuple[Bar, ...] | None = None) -> BarSeries:
    bars = bars if bars is not None else (_bar(start),)
    q = BarQuery(bars[0].instrument, bars[0].timeframe, start, end, gap_policy="allow_with_metadata")
    c = CoverageReport(start, end, bars[0].time if bars else None, bars[-1].time_close if bars else None, source_mix=("test",))
    return BarSeries(q, bars, c)


def test_batch_runner_metadata_and_completion_branches(tmp_path, monkeypatch, capsys):
    entry = _entry(tmp_path, kind="strategy")
    chart = entry.charts[0]
    args = argparse.Namespace(timeframe=None, skip_completed=True, phase="run")
    assert br.ms_to_utc_iso(None) is None
    assert br.ms_to_utc_iso(0).startswith("1970")
    assert isinstance(br.utc_now(), str)
    progress_path_root = tmp_path / "progress"
    br._write_progress(progress_path_root, "b1", 7, "run", "entry_start", selected_count=2, processed_count=1, summary_by_timeframe={"1m": {"selected": 1}})
    assert json.loads((progress_path_root / "current_progress.json").read_text())["current_entry_id"] == 7
    callback = br.build_progress_callback("x", 2)
    assert callback is not None
    callback(1, 10)
    callback(3, 10)
    callback(10, 10)
    assert "runtime x" in capsys.readouterr().out
    assert br.build_progress_callback("x", 0) is None
    assert br.get_or_add_source(entry, write=False) == (None, False)
    assert br._expected_output_files(entry, chart)[1].name == "trades.csv"
    assert br._wanted_charts(entry, argparse.Namespace(timeframe="1m")) == [chart]
    assert br._wanted_charts(entry, argparse.Namespace(timeframe="15m")) == []
    assert br._output_file_valid(tmp_path / "missing") is False
    assert br._valid_window({"from_ms": 1, "to_ms": 2}) is True
    assert br._valid_window({"from_ms": 2, "to_ms": 1}) is False
    meta = br._build_run_meta(entry=entry, chart=chart, status={"source_id": "s", "artifact_id": "a"}, run_info={"status": "ok", "data": {"calculation_from": 0, "calculation_to": 60_000, "compare_from": 0, "compare_to": 60_000}}, batch_id="b", library_revisions={name: "rev" for name in br.LIBRARY_NAMES})
    out_dir = entry.root / "openpine_outputs" / chart.timeframe
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots.csv").write_text("x\n", encoding="utf-8")
    (out_dir / "trades.csv").write_text("x\n", encoding="utf-8")
    (out_dir / "equity_curve.csv").write_text("x\n", encoding="utf-8")
    br.write_json(out_dir / "run_meta.json", meta)
    summary = br._build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info={"status": "ok", "bars": 3, "plots_rows": 2, "trades_rows": 1, "equity_rows": 1})
    br.write_json(out_dir / "summary.json", summary)
    br.write_json(entry.root / "openpine_outputs" / "openpine_batch_status.json", {"phase": "run", "status": "ok", "runs": [{"timeframe": "1m", "status": "ok"}]})
    assert br._run_meta_valid(out_dir / "run_meta.json") is True
    assert br._run_summary_valid(out_dir / "summary.json") is True
    assert br.completed_for_selection(entry, args) is True
    (out_dir / "fatal_error.json").write_text("{}", encoding="utf-8")
    assert br.completed_for_selection(entry, args) is False
    (out_dir / "fatal_error.json").unlink()
    assert br.result_has_error({"status": "ok", "runs": [{"status": "fail"}]}) is True
    assert br.result_has_error({"status": "ok", "runs": [{"status": "ok"}]}) is False
    assert br.parse_ids("1,3-4") == {1, 3, 4}
    assert br.parse_ids(None) is None
    results = [{"status": "ok", "kind": "strategy", "charts": [{"timeframe": "1m"}], "runs": [{"timeframe": "1m", "status": "ok", "bars": 10, "plots_rows": 2, "trades_rows": 1, "equity_rows": 1}]}]
    assert br.summarize(results)["stats"]["ok"] == 1
    assert br.summary_by_timeframe(results)["1m"]["bars"] == 10
    assert br.summary_by_timeframe([{"status": "planned", "selected_timeframes": ["5m"]}])["5m"]["statuses"]["planned"] == 1
    assert br.resolve_calculation_to_by_timeframe([entry], argparse.Namespace(phase="run", calculation_to=None, timeframe=None))["1m"] == 180000
    assert br.resolve_calculation_to_by_timeframe([entry], argparse.Namespace(phase="plan", calculation_to=None, timeframe=None)) == {}
    path = br._write_timeframe_summary_csv(root=tmp_path, phase="run", batch_id="b", results=results)
    assert path is not None and path.exists()
    payload = br._build_batch_summary_payload(args=argparse.Namespace(phase="run", root=tmp_path, manifest=tmp_path / "m.json", symbol="BTCUSDT", exchange="binance", market_type="spot", calculation_from="0", calculation_to=None, _calculation_to_by_timeframe={"1m": 60000}), batch_id="b", errors_path=tmp_path / "err.jsonl", library_revisions={"openpine": "rev"}, selected=[entry], entries=[entry], results=results, timeframe_summary={"1m": {"selected": 1}})
    assert payload["selected"] == 1
    br.append_jsonl(tmp_path / "x" / "events.jsonl", {"a": 1})
    assert "a" in (tmp_path / "x" / "events.jsonl").read_text()


def test_batch_runner_chart_inference_and_merge(tmp_path, monkeypatch):
    entry = _entry(tmp_path)
    chart_path = entry.root / "chart.csv"
    pd.DataFrame({"time": [0, 60, 120], "open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3], "Volume": [10, 20, 30], "BAR_INDEX": [10, 11, 12], "PERIODIC": [float("nan"), 1.0, 2.0]}).to_csv(chart_path, index=False)
    chart = ChartExport("1m", chart_path, 3, 0, 120_000)
    provider_bars = [_bar(0, 9), _bar(180_000, 4)]
    merged = br._merge_tv_visible_bars(provider_bars=provider_bars, chart=chart, symbol="BTCUSDT", exchange="binance", market_type="spot")
    assert [b.time for b in merged] == [0, 60_000, 120_000, 180_000]
    assert merged[0].close == 1.0
    offset, meta = br._infer_tv_bar_index_offset(chart, merged)
    assert offset == 10 and meta["status"] == "inferred"
    df = pd.DataFrame({"time": [0, 1, 2, 3], "open": [1]*4, "high": [1]*4, "low": [1]*4, "close": [1]*4, "MOD7": [float("nan"), 1, 1, 1]})
    offset2, meta2 = br._infer_tv_bar_index_offset_from_periodic_na(df, 0)
    assert isinstance(offset2, int)
    assert meta2 is None or "status" in meta2


class _FakeStateStore:
    def __init__(self, snapshot=None, fail=False):
        self.snapshot = snapshot
        self.fail = fail
        self.saved = []
        self.invalidated = []
    def latest_snapshot_metadata(self, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return self.snapshot
    def load_latest_compatible(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return self.snapshot
    def save_runtime_snapshot(self, **kwargs):
        if self.fail:
            raise RuntimeError("boom")
        self.saved.append(kwargs)
    def mark_invalid(self, strategy_id, since_bar_time=None):
        if self.fail:
            raise RuntimeError("boom")
        self.invalidated.append((strategy_id, since_bar_time))


class _FakeStrategy:
    strategy_id = "s1"
    artifact_id = "a1"
    params_hash = "h"
    exchange = "BINANCE"
    market_type = "SPOT"
    symbol = "btcusdt"
    timeframe = "1m"
    pine_id = "p1"
    name = "Strategy"
    enabled = True
    status = "running"


def test_live_runner_helpers_and_order_processing(monkeypatch):
    strategy = _FakeStrategy()
    runner = LiveStrategyRunner(RunnerConfig(recheck_bars=1, max_catchup_bars=2), state_store=None)
    assert runner._bars_to_process(StrategyBarState("s", 0), 120000, 60000) == [120000]
    assert runner._bars_to_process(StrategyBarState("s", 60000), 240000, 60000) == [0, 60000, 120000, 180000]
    assert runner._instrument_key(strategy)["symbol"] == "BTCUSDT"
    assert runner._timeframe_key(strategy) == {"canonical": "1m"}
    assert runner._resume_bar_index({"bar_index": "7"}) == 7
    assert runner._resume_bar_index({"bar_index": "bad"}) is None
    assert runner._resume_has_runtime_state({"runtime_state": {}}) is True
    assert runner._resume_has_runtime_state({}) is False
    assert runner._is_resume_replay_error(RuntimeError("content hash mismatch")) is True
    assert runner._is_resume_replay_error(RuntimeError("ordinary")) is False
    assert runner._extract_percent_input("tpPct = input.float(2.5)\n", "tpPct") == 2.5
    assert runner._extract_percent_input("tpPct = input.float(x)\n", "tpPct") is None
    assert runner._client_order_id(strategy, {"side": "buy", "qty": 1}) == runner._client_order_id(strategy, {"side": "buy", "qty": 1})
    raw = SimpleNamespace(trades=[SimpleNamespace(entry_time=120000, direction="long", entry_price=10, qty=1, net_pnl=2)], order_lifecycle=[SimpleNamespace(time=120000, side="sell", price=12, quantity=1, order_type="limit")])
    orders = runner._extract_new_orders(raw, 60000)
    assert len(orders) == 2
    class Store:
        def execute(self, sql, params=()):
            if "source_text" in sql:
                return SimpleNamespace(fetchone=lambda: ("tpPct = input.float(10)\nslPct = input.float(5)",))
            if "SELECT changes" in sql:
                return SimpleNamespace(fetchone=lambda: (1,))
            return SimpleNamespace(fetchone=lambda: None)
        def commit(self):
            pass
    runner.storage = Store()
    runner._attach_risk_prices(strategy, orders)
    assert orders[0]["take_profit_price"] == 11
    assert orders[0]["stop_price"] == 9.5
    asyncio.run(runner._process_orders(strategy, orders))
    fail_store = _FakeStateStore(fail=True)
    runner2 = LiveStrategyRunner(state_store=fail_store)
    assert runner2._latest_processed_bar_time(strategy, 1) == 0
    assert runner2._load_resume_snapshot(strategy, instrument_key={}, timeframe={}, at_or_before_bar_time=1) is None
    runner2._save_resume_snapshot(strategy, result=SimpleNamespace(resume_state={"runtime_state": {}}), instrument_key={}, timeframe={}, bar_time=1, data_fingerprint=None)
    runner2._mark_resume_snapshot_invalid(strategy, 1)


def test_backtest_and_accounts_helper_edges(tmp_path, monkeypatch):
    strategy = SimpleNamespace(strategy_id="s1", exchange="BINANCE", market_type="SPOT", symbol="btcusdt", timeframe="1m")
    q = backtest_routes._market_data_query_for_strategy(strategy, 0, 60000)
    assert q.instrument.symbol == "BTCUSDT"
    assert backtest_routes._normalize_metrics_payload({"net_profit": 1, "nested": {"x": object()}})["net_profit"] == 1
    assert backtest_routes._normalize_metrics_payload(None) is None
    assert len(backtest_routes._bar_series_fingerprint(_series()).encode()) == 64
    class _BacktestStorage:
        def execute(self, sql, params=()):
            if "PRAGMA table_info" in sql:
                return SimpleNamespace(fetchall=lambda: [(0, "run_id"), (1, "data_fingerprint")])
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])
        def commit(self):
            pass
    state = SimpleNamespace(storage=_BacktestStorage())
    backtest_routes._ensure_backtest_data_fingerprint_column(state)
    backtest_routes._save_backtest_data_fingerprint(state, "r1", "fp")
    assert accounts_data._ranges_cover_request([{"from_ms": 0, "to_ms": 10}], "1m", 0, 10) is True
    assert accounts_data._ranges_cover_request([{"from_ms": 0, "to_ms": 5}], "1m", 0, 120000) is False
    monkeypatch.setattr(accounts_data, "default_cache_dir", lambda: tmp_path / "empty-cache")
    assert accounts_data._stored_ranges_cover_request({"exchange":"binance","market_type":"spot","symbol":"BTCUSDT","timeframe":"1m","from_time":0,"to_time":10}, SimpleNamespace(config=SimpleNamespace(data_cache_root=None, data_dir=tmp_path))) == (False, 0)
    assert len(accounts_data._compact_ranges([{"start_ms": 0, "end_ms": 1}, {"start_ms": 1, "end_ms": 2}])) == 2
    merged = accounts_data._coalesce_ranges([{"from_ms": 2, "to_ms": 3}, {"from_ms": 0, "to_ms": 2}], "1m")
    assert merged[0]["from_ms"] == 0 and merged[0]["to_ms"] == 3
    assert accounts_data._estimate_bars_for_window(0, 120000, "1m") == 2
    assert accounts_data._timeframe_duration_ms("bad") == 60_000
    assert accounts_data._freshness_status(None, "1m") == "empty"
    assert isinstance(accounts_data._series_id(("binance", "spot", "BTCUSDT", "trade", "1m")), str)
    missing_state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path, sqlite_path=tmp_path / "missing.sqlite"), storage=SimpleNamespace(db_path=tmp_path / "missing.sqlite"))
    assert accounts_data._database_size_bytes(missing_state) == 0
    d = tmp_path / "x"
    d.mkdir()
    (d / "a.bin").write_bytes(b"123")
    assert accounts_data._dir_size(d) == 3


def test_cli_data_helpers_and_export_plot_branches(tmp_path, monkeypatch):
    assert cli_data._fmt_utc_ms_as(0, "%Y") == "1970"
    start, end, error = cli_data._parse_data_backfill_window(from_date="1970-01-01", to_date="1970-01-02", now_ms=200000)
    assert error is None and start is not None and end is not None and start < end
    assert cli_data._parse_cli_ymd_ms("bad", option_name="--from")[0] is None
    import openpine.data.provider_adapter as provider_adapter
    import openpine.data.orchestrator as orchestrator_mod
    monkeypatch.setattr(provider_adapter, "create_local_marketdata_provider_adapter", lambda: object())
    class FakeOrchestrator:
        def __init__(self, provider):
            self.provider = provider
        def load_bars(self, query):
            return _series(0, 60000, (_bar(0), _bar(60000)))
    monkeypatch.setattr(orchestrator_mod, "DataOrchestrator", FakeOrchestrator)
    console = SimpleNamespace(print=lambda *a, **k: None)
    assert cli_data._run_sync_marketdata_backfill(symbol="BTCUSDT", timeframe="1m", exchange="binance", market="spot", start_ms=0, end_ms=60000, timeout=0, console=console) is True
    rows = [(0, 0, 1.0, "plot"), (20, 1, 2.0, "plot")]
    assert export_plots.export_plot_records(rows, tmp_path / "plots.csv", from_ms=0, to_ms=10) == 1
    assert (tmp_path / "plots.csv").read_text(encoding="utf-8").startswith("bar_time")
    long_csv = tmp_path / "long.csv"
    pd.DataFrame({"bar_time": [0, 20], "bar_index": [0, 1], "value": [1, 2], "title": ["p", "p"]}).to_csv(long_csv, index=False)
    assert export_plots.export_plot_outputs(long_csv, tmp_path / "wide.csv", from_ms=100, to_ms=200) == 0


def test_telegram_formatting_and_plugin_manager(monkeypatch):
    assert telegram._string_id(123) == "123"
    assert telegram._string_id(None) is None
    assert "&lt;" in telegram._format_cli_output_for_html("<x>")
    cfg = telegram.TelegramPluginConfig(enabled=True, chat_allowlist=["1"])
    class Plugin:
        def info(self):
            return telegram.PluginInfo("p", "telegram", True)
    manager = telegram.PluginManager([Plugin()])
    assert manager.load_plugins()[0].enabled is True
    notifier = telegram.TelegramNotifier(cfg)
    assert notifier.config.chat_allowlist == ["1"]
    message = telegram.TelegramMessage.from_api({"message_id": 1, "chat": {"id": 1}, "text": "/start"})
    assert message is not None and message.chat_id == "1"
    callback = telegram.TelegramCallbackQuery.from_api({"id": "c1", "from": {"id": 1}, "message": {"chat": {"id": 1}, "message_id": 2}, "data": "x"})
    assert callback is not None and callback.chat_id == "1"
    update = telegram.TelegramUpdate.from_api({"update_id": 7, "message": {"message_id": 1, "chat": {"id": 1}, "text": "hi"}})
    assert update is not None and update.message is not None


@pytest.mark.asyncio
async def test_dashboard_and_strategy_route_edges(tmp_path):
    class FakeCursor:
        def __init__(self, rows=None, one=None):
            self.rows = rows or []
            self.one = one
        def fetchall(self):
            return self.rows
        def fetchone(self):
            return self.one if self.one is not None else (self.rows[0] if self.rows else None)
    class Storage:
        db_path = tmp_path / "db.sqlite"
        def execute(self, sql, params=()):
            text = " ".join(sql.lower().split())
            if "count(*)" in text and "strategy_instances" in text:
                return FakeCursor(one=(1,))
            if "from orders" in text and "group by" not in text:
                return FakeCursor(one=(1, 100, 200))
            if "from orders" in text and "group by symbol" in text:
                return FakeCursor(rows=[("BTCUSDT", 1, 100)])
            return FakeCursor(rows=[])
    class Reg:
        def __init__(self):
            self.s = SimpleNamespace(strategy_id="s1", id="s1", name="S", pine_id="p", artifact_id="a", symbol="BTCUSDT", timeframe="1m", exchange="binance", market_type="spot", params_json="{}", params_hash="h", mode="paper", enabled=False, status="paused", created_at=1, updated_at=2)
        def list_strategies(self):
            return [self.s]
        def get_strategy(self, sid):
            if sid != "s1":
                raise KeyError(sid)
            return self.s
        def set_enabled(self, sid, enabled):
            self.s.enabled = enabled
        def update_status(self, sid, status):
            self.s.status = status
        def update_mode(self, sid, mode):
            self.s.mode = mode
    state = SimpleNamespace(storage=Storage(), strategy_registry=Reg(), risk_manager=SimpleNamespace(is_kill_switch_active=lambda: False), _risk_kill_switch=[False], config=SimpleNamespace(data_dir=tmp_path), scheduler=SimpleNamespace(list_jobs=lambda: []), _startup_time=0)
    summary = await dashboard.dashboard(state=state)
    assert summary.strategies
    listed = await strategies.list_strategies(registry=state.strategy_registry)
    assert listed[0].strategy_id == "s1"
    await strategies.strategy_enable("s1", registry=state.strategy_registry)
    assert state.strategy_registry.s.enabled is True
    await strategies.strategy_disable("s1", registry=state.strategy_registry)
    with pytest.raises(HTTPException):
        await strategies.strategy_enable("missing", registry=state.strategy_registry)


def test_live_runner_mini_backtest_success_and_resume_fallback(monkeypatch):
    strategy = _FakeStrategy()
    snapshot = SimpleNamespace(bar_time=60000, state_data={"runtime_state": {}, "bar_index": 1})
    state_store = _FakeStateStore(snapshot=snapshot)
    class Orchestrator:
        def __init__(self):
            self.calls = 0
        def load_bars(self, query):
            self.calls += 1
            return _series(query.start_ms, query.end_ms, (_bar(query.start_ms), _bar(query.end_ms - 60000)))
    class ArtifactStore:
        def get_artifact(self, artifact_id, pine_id):
            return {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {"initial_capital": 2000, "commission_type": "cash_per_order", "commission_value": 1, "close_entries_rule": "any"}}}}}
    import openpine.runtime.engine as runtime_engine
    import openpine.data.direct_data_provider as direct_data_provider
    monkeypatch.setattr(runtime_engine, "load_strategy_class_from_artifact", lambda *a, **k: type("Strategy", (), {}))
    class FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class Raw:
        trades = [SimpleNamespace(entry_time=120000, direction="long", entry_price=10, qty=1, net_pnl=5)]
        order_lifecycle = []
    class FakeResult:
        raw_result = Raw()
        resume_state = {"runtime_state": {}, "bar_index": 2}
    class Adapter:
        def __init__(self):
            self.calls = 0
        def run(self, *args, **kwargs):
            self.calls += 1
            if kwargs.get("resume_state") is not None:
                raise RuntimeError("resume content hash mismatch")
            return FakeResult()
    monkeypatch.setattr(runtime_engine, "BacktestRunConfig", FakeConfig)
    monkeypatch.setattr(runtime_engine, "BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(direct_data_provider, "DirectBinanceDataProvider", lambda market: object())
    runner = LiveStrategyRunner(RunnerConfig(lookback_bars=5), orchestrator=Orchestrator(), artifact_store=ArtifactStore(), state_store=state_store)
    orders = runner._run_mini_backtest(strategy, 120000)
    assert orders and orders[0]["entry_price"] == 10
    assert state_store.invalidated == [("s1", 60000)]
    assert state_store.saved


def test_live_runner_process_strategy_and_empty_paths(monkeypatch):
    strategy = _FakeStrategy()
    registry = SimpleNamespace(list_strategies=lambda: [strategy, SimpleNamespace(enabled=False, status="running")])
    runner = LiveStrategyRunner(RunnerConfig(recheck_bars=0, max_catchup_bars=1), registry=registry, state_store=None)
    assert asyncio.run(runner._check_all_strategies()) is None
    async def fake_process(strategy, now_ms):
        raise RuntimeError("ignored")
    runner._process_strategy = fake_process
    assert asyncio.run(runner._check_all_strategies()) is None
    runner_no_registry = LiveStrategyRunner(registry=None)
    assert asyncio.run(runner_no_registry._check_all_strategies()) is None
    runner2 = LiveStrategyRunner(state_store=None)
    monkeypatch.setattr(runner2, "_run_mini_backtest", lambda s, t: [])
    assert asyncio.run(runner2._process_strategy(strategy, 180000)) is None
