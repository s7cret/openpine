from __future__ import annotations

import asyncio
from types import SimpleNamespace


from openpine.gateway.routes import backtest as bt


class FakeWS:
    def __init__(self):
        self.events = []
    def update_progress(self, run_id, domain, status, progress, message, detail=None):
        self.events.append((run_id, status, progress, message, detail))
    async def broadcast_progress(self, run_id):
        self.events.append((run_id, "broadcast", None, None, None))


class FakeStore:
    def __init__(self):
        self.failed = []
        self.cancelled = []
        self.saved = []
    def mark_failed(self, run_id, message):
        self.failed.append((run_id, message))
    def mark_cancelled(self, run_id, message):
        self.cancelled.append((run_id, message))
    def save_result(self, **kwargs):
        self.saved.append(kwargs)


class FakeRegistry:
    def __init__(self, strategy=None, fail=False):
        self.strategy = strategy or SimpleNamespace(
            strategy_id="s1",
            pine_id="p1",
            artifact_id="a1",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            params_json='{"x": 1}',
        )
        self.fail = fail
    def get_strategy(self, strategy_id):
        if self.fail:
            raise KeyError(strategy_id)
        return self.strategy


class FakeStorage:
    def __init__(self):
        self.sql = []
        self.has_col = False
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if sql.startswith("PRAGMA"):
            rows = [(0, "run_id")] + ([(1, "data_fingerprint")] if self.has_col else [])
            return SimpleNamespace(fetchall=lambda: rows)
        return SimpleNamespace(fetchall=lambda: [])
    def commit(self):
        self.sql.append(("COMMIT", ()))


def _state(*, registry=None, orchestrator=None, storage=None, store=None):
    return SimpleNamespace(
        strategy_registry=registry or FakeRegistry(),
        backtest_store=store or FakeStore(),
        orchestrator=orchestrator or SimpleNamespace(load_bars=lambda query, progress_callback=None: SimpleNamespace(query=query, bars=[])),
        artifact_store=SimpleNamespace(get_artifact=lambda artifact_id, pine_id: {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {"commission_type": "cash_per_order"}}}}}),
        storage=storage or FakeStorage(),
        backtest_cancel_requests=set(),
    )


def test_backtest_background_strategy_and_artifact_failures(monkeypatch):
    ws = FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    store = FakeStore()
    asyncio.run(bt._run_backtest_background(_state(registry=FakeRegistry(fail=True), store=store), "missing", "run1", 1, 2, None, 0, False))
    assert ws.events[-1][1] == "broadcast"

    import openpine.runtime.engine as rt
    def bad_loader(*args, **kwargs):
        raise rt.BacktestArtifactError("bad artifact")
    monkeypatch.setattr(rt, "load_strategy_class_from_artifact", bad_loader)
    store = FakeStore()
    asyncio.run(bt._run_backtest_background(_state(store=store), "s1", "run2", 1, 2, None, 0, False))
    assert store.failed and "bad artifact" in store.failed[-1][1]


def test_backtest_background_data_error_empty_and_cancel(monkeypatch):
    ws = FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    monkeypatch.setattr(bt, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: SimpleNamespace(estimated_bars=10, estimated_pages=2, effective_from=from_ms, adjusted=False))
    import openpine.runtime.engine as rt
    monkeypatch.setattr(rt, "load_strategy_class_from_artifact", lambda *args, **kwargs: object)

    store = FakeStore()
    state = _state(store=store, orchestrator=SimpleNamespace(load_bars=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no data"))))
    asyncio.run(bt._run_backtest_background(state, "s1", "run3", 1, 2, None, 0, False))
    assert store.failed and "Data load failed" in store.failed[-1][1]

    store = FakeStore()
    state = _state(store=store, orchestrator=SimpleNamespace(load_bars=lambda *a, **k: SimpleNamespace(query=SimpleNamespace(instrument=SimpleNamespace(exchange="binance", market="spot", symbol="BTCUSDT"), timeframe=SimpleNamespace(canonical="1m"), start_ms=1, end_ms=2), bars=[])))
    asyncio.run(bt._run_backtest_background(state, "s1", "run4", 1, 2, None, 0, False))
    assert store.failed and "No bars" in store.failed[-1][1]

    store = FakeStore()
    state = _state(store=store)
    state.backtest_cancel_requests.add("run5")
    asyncio.run(bt._run_backtest_background(state, "s1", "run5", 1, 2, None, 0, False))
    assert store.cancelled and "strategy load" in store.cancelled[-1][1]


def test_backtest_background_success_and_helpers(monkeypatch):
    ws = FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    monkeypatch.setattr(bt, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: SimpleNamespace(estimated_bars=1, estimated_pages=1, effective_from=from_ms, adjusted=False, earliest_available=from_ms))
    import openpine.runtime.engine as rt
    monkeypatch.setattr(rt, "load_strategy_class_from_artifact", lambda *args, **kwargs: object)
    import openpine.data.direct_data_provider as ddp
    monkeypatch.setattr(ddp, "DirectBinanceDataProvider", lambda market="spot": SimpleNamespace(market=market))
    monkeypatch.setattr(bt, "_run_backtest_in_process", lambda *args, **kwargs: SimpleNamespace(raw_result=SimpleNamespace(trades=[1], equity_curve=[1]), bars_processed=1))
    bar = SimpleNamespace(time=1, time_close=2, open=1.0, high=2.0, low=0.5, close=1.5, volume=10)
    state = _state(orchestrator=SimpleNamespace(load_bars=lambda query, progress_callback=None: SimpleNamespace(query=query, bars=[bar])), storage=FakeStorage())
    asyncio.run(bt._run_backtest_background(state, "s1", "run6", 1, 2, {"override": 1}, 0, True))
    assert state.backtest_store.saved
    assert bt._normalize_metrics_payload({"metrics": {"total_trades": 3}})["trades_total"] == 3
    assert bt._normalize_metrics_payload({"trades_total": 2})["total_trades"] == 2
    assert bt._normalize_metrics_payload(None) is None
    assert isinstance(bt._parse_date_ms("123"), int)
