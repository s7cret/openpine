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
    # This entrypoint normally runs inside the dedicated supervisor. Calling it
    # in the pytest parent must not clean that parent's real descendants (most
    # notably multiprocessing.resource_tracker).
    monkeypatch.setattr(bt, "_terminate_current_process_descendants", lambda: None)
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
        def __init__(self, *a, **k):
            self.pid = 4242
            self.exitcode = 0
            self.alive = True
        def start(self): pass
        def is_alive(self):
            if self.alive: self.alive=False; return True
            return False
        def join(self, timeout=None): pass
    class Ctx:
        class Receiver:
            def recv(self): return (4242, 7)
            def close(self): pass
        class Sender:
            def close(self): pass
        def __init__(self, q): self.q=q
        def Queue(self): return self.q
        def Event(self): return SimpleNamespace(is_set=lambda: True)
        def Pipe(self, duplex=False):
            assert duplex is False
            return self.Receiver(), self.Sender()
        def Process(self, **kw): return FakeProc()
    monkeypatch.setattr(bt, "_proc_identity", lambda pid: ("S", pid, 7))
    monkeypatch.setattr(bt, "_terminate_backtest_worker", lambda worker, timeout=3.0: True)
    monkeypatch.setattr(
        bt,
        "mp",
        SimpleNamespace(
            get_context=lambda name: Ctx(
                FakeQueue([("progress", 1, 3), ("ok", "done")])
            )
        ),
    )
    progress=[]
    assert bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None, lambda d,t: progress.append((d,t))) == "done"
    assert progress == [(1,3)]
    monkeypatch.setattr(
        bt,
        "mp",
        SimpleNamespace(
            get_context=lambda name: Ctx(
                FakeQueue([("err", "ValueError", "no", "tb")])
            )
        ),
    )
    with pytest.raises(RuntimeError): bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None)
    monkeypatch.setattr(
        bt,
        "mp",
        SimpleNamespace(get_context=lambda name: Ctx(FakeQueue([]))),
    )
    with pytest.raises(RuntimeError): bt._execute_backtest_run_in_thread("run", set(), Adapter(), object, [], object(), {}, None)
