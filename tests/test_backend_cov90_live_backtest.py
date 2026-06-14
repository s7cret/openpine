from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace

import pytest

from openpine.gateway import live_runner as lr
from openpine.gateway.routes import backtest as bt


def _strategy(**kw):
    base = dict(
        strategy_id="s1", pine_id="p1", artifact_id="a1", params_hash="ph",
        exchange="BINANCE", market_type="SPOT", symbol="BTCUSDT", timeframe="1m",
        name="Strat", params_json='{"x": 1}', enabled=True, status="running",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_live_runner_core_state_and_order_paths(monkeypatch):
    # default state store failure branch
    import openpine.config as cfg
    monkeypatch.setattr(cfg.OpenPineConfig, "load", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no cfg"))))
    assert lr.LiveStrategyRunner._default_state_store() is None

    r = lr.LiveStrategyRunner(config=lr.RunnerConfig(recheck_bars=2, max_catchup_bars=3), state_store=None)
    assert r._bars_to_process(lr.StrategyBarState("s", 0), 60000, 60000) == [60000]
    assert r._bars_to_process(lr.StrategyBarState("s", 60000), 300000, 60000) == [0, 60000, 120000, 180000, 240000]
    assert r._latest_processed_bar_time(_strategy(), 1000) == 0
    assert lr.LiveStrategyRunner._is_resume_replay_error(RuntimeError("config hash mismatch"))
    assert not lr.LiveStrategyRunner._is_resume_replay_error(RuntimeError("other"))
    assert lr.LiveStrategyRunner._resume_bar_index({"bar_index": "7"}) == 7
    assert lr.LiveStrategyRunner._resume_bar_index({"bar_index": "bad"}) is None
    assert lr.LiveStrategyRunner._resume_has_runtime_state({"runtime_state": {}})
    assert not lr.LiveStrategyRunner._resume_has_runtime_state({})

    raw = SimpleNamespace(
        trades=[SimpleNamespace(entry_time=100, exit_time=200, direction="short", entry_price=10, exit_price=9, qty=2, net_pnl=1.5), SimpleNamespace(entry_time=1)],
        order_lifecycle=[SimpleNamespace(created_at=120, side="buy", price=11, quantity=3, order_type="limit"), SimpleNamespace(time=1)],
    )
    orders = lr.LiveStrategyRunner._extract_new_orders(raw, 50)
    assert len(orders) == 2 and orders[0]["side"] == "short"
    assert lr.LiveStrategyRunner._extract_percent_input("tpPct=input.float(2.5)\nslPct = input.float(-1)", "tpPct") == 2.5
    assert lr.LiveStrategyRunner._extract_percent_input("x=1", "tpPct") is None
    strat = _strategy()
    assert lr.LiveStrategyRunner._instrument_key(strat) == {"exchange":"binance","market":"spot","symbol":"BTCUSDT","price_type":"trade"}
    assert lr.LiveStrategyRunner._timeframe_key(strat) == {"canonical":"1m"}

    class Store:
        def __init__(self): self.rows=[]; self.invalid=[]; self.saved=[]; self.source="tpPct=input.float(2)\nslPct=input.float(1)"
        def execute(self, sql, params=()):
            if "SELECT source_text" in sql: return SimpleNamespace(fetchone=lambda: (self.source,))
            if "SELECT changes" in sql: return SimpleNamespace(fetchone=lambda: (1,))
            self.rows.append((sql, params)); return SimpleNamespace(fetchone=lambda: None)
        def commit(self): self.committed=True
        def latest_snapshot_metadata(self, *a, **k): return SimpleNamespace(bar_time=123)
        def load_latest_compatible(self, *a, **k): return SimpleNamespace(bar_index=1, runtime_state={"ok": True})
        def save_runtime_snapshot(self, **kw): self.saved.append(kw)
        def mark_invalid(self, *a, **k): self.invalid.append((a,k))
    store = Store()
    r = lr.LiveStrategyRunner(storage=store, state_store=store)
    assert r._strategy_risk_percents(strat) == (2.0, 1.0)
    risk_orders = [{"side":"buy","entry_price":100},{"side":"sell","price":100},{"side":"bad","price":"x"}]
    r._attach_risk_prices(strat, risk_orders)
    assert risk_orders[0]["take_profit_price"] == 102
    assert risk_orders[1]["stop_price"] == 101
    assert r._load_resume_snapshot(strat, instrument_key={}, timeframe={}, at_or_before_bar_time=1) is not None
    r._save_resume_snapshot(strat, result=SimpleNamespace(resume_state={"state": 1}), instrument_key={}, timeframe={}, bar_time=2, data_fingerprint="fp")
    assert store.saved
    r._mark_resume_snapshot_invalid(strat, 3)
    assert store.invalid

    sent=[]
    monkeypatch.setattr(lr.ws_manager, "update_progress", lambda *a, **k: sent.append((a,k)))
    async def bcast(job): sent.append((("broadcast",job),{}))
    monkeypatch.setattr(lr.ws_manager, "broadcast_progress", bcast)
    asyncio.run(r._process_orders(strat, [{"side":"buy","qty":1,"entry_price":10,"entry_time":1,"net_pnl":1.2}]))
    assert sent and store.rows


def test_live_runner_loop_and_strategy_processing(monkeypatch):
    strat = _strategy()
    class Registry:
        def list_strategies(self): return [strat, _strategy(strategy_id="s2", status="paused")]
    r = lr.LiveStrategyRunner(registry=Registry(), state_store=None)
    called=[]
    async def proc(strategy, now_ms): called.append(strategy.strategy_id)
    r._process_strategy = proc
    asyncio.run(r._check_all_strategies())
    assert called == ["s1"]

    r2 = lr.LiveStrategyRunner(registry=None)
    asyncio.run(r2._check_all_strategies())
    # start/stop branches, including no loop runtime
    r3 = lr.LiveStrategyRunner()
    monkeypatch.setattr(lr.asyncio, "get_event_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))
    r3.start(); r3.stop()


def test_backtest_helpers_and_process_paths(monkeypatch):
    strat = _strategy()
    q = bt._market_data_query_for_strategy(strat, 0, 60_000)
    assert q.instrument.symbol == "BTCUSDT"
    series = SimpleNamespace(query=q, bars=[SimpleNamespace(time=0,time_close=60000,open=1,high=2,low=0.5,close=1.5,volume=10)])
    assert len(bt._bar_series_fingerprint(series)) == 64
    assert bt._normalize_metrics_payload({"metrics":{"total_trades": 2}})["trades_total"] == 2
    assert bt._normalize_metrics_payload(None) is None

    class Out:
        def __init__(self): self.items=[]
        def put_nowait(self, item): self.items.append(item)
        def put(self, item): self.items.append(item)
    class Adapter:
        def __init__(self, fail=False): self.fail=fail
        def run(self,*a,progress_callback=None,**k):
            progress_callback(1,2)
            if self.fail: raise RuntimeError("bad")
            return "result"
    out=Out(); bt._backtest_process_entry(out, Adapter(), object, [], object(), {}, None)
    assert out.items[0][0] == "progress" and out.items[-1] == ("ok", "result")
    out=Out(); bt._backtest_process_entry(out, Adapter(True), object, [], object(), {}, None)
    assert out.items[-1][0] == "err"

    class FakeQueue:
        def __init__(self, seq): self.seq=list(seq)
        def get(self, timeout=0):
            if self.seq: return self.seq.pop(0)
            raise queue.Empty
        def get_nowait(self):
            if self.seq: return self.seq.pop(0)
            raise queue.Empty
        def close(self): pass
        def cancel_join_thread(self): pass
    class FakeProc:
        def __init__(self, *a, **k): self.exitcode=0; self.alive=True
        def start(self): pass
        def is_alive(self):
            if self.alive: self.alive=False; return True
            return False
        def join(self): pass
    class Ctx:
        def __init__(self, q): self.q=q
        def Queue(self): return self.q
        def Process(self, **kw): return FakeProc()
    monkeypatch.setattr(bt.mp, "get_context", lambda name: Ctx(FakeQueue([("progress", 1, 3), ("ok", "done")])) )
    progress=[]
    assert bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None, lambda d,t: progress.append((d,t))) == "done"
    assert progress == [(1,3)]
    monkeypatch.setattr(bt.mp, "get_context", lambda name: Ctx(FakeQueue([("err", "ValueError", "no", "tb")])) )
    with pytest.raises(RuntimeError): bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None)
    monkeypatch.setattr(bt.mp, "get_context", lambda name: Ctx(FakeQueue([])) )
    with pytest.raises(RuntimeError): bt._run_backtest_in_process(Adapter(), object, [], object(), {}, None)
