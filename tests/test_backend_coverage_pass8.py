from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway.routes import accounts_data
from openpine.gateway.routes import backtest as backtest_routes
from openpine.gateway.routes import pine_ops
from openpine.gateway.schemas import DataBackfillRequest, KillSwitchRequest


class SqliteWrapper:
    def __init__(self, path: Path):
        self.db_path = path
        self.conn = sqlite3.connect(path)

    def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ANN201
        return self.conn.execute(sql, params)

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.conn.commit()
        return False

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


def _init_storage(tmp_path: Path) -> SqliteWrapper:
    storage = SqliteWrapper(tmp_path / "openpine.sqlite")
    storage.execute(
        "CREATE TABLE accounts (account_id text, name text, exchange text, market_type text, mode text, live_enabled int, created_at int)"
    )
    storage.execute("INSERT INTO accounts VALUES ('a1','Paper','binance','spot','paper',0,1)")
    storage.execute(
        "CREATE TABLE orders (order_id text, strategy_id text, account_id text, client_order_id text, symbol text, side text, order_type text, quantity real, price real, stop_price real, avg_fill_price real, status text, filled_quantity real, remaining_quantity real, error_message text, created_at int, updated_at int)"
    )
    storage.execute(
        "INSERT INTO orders VALUES ('o1','s1','a1','c1','BTCUSDT','buy','limit',1,10,NULL,10,'filled',1,0,NULL,100,200)"
    )
    storage.execute("CREATE TABLE strategy_instances (strategy_id text, name text)")
    storage.execute("INSERT INTO strategy_instances VALUES ('s1','Strategy')")
    storage.execute("CREATE TABLE fills (fill_id text, order_id text, strategy_id text, symbol text, side text, qty real, price real, time_ms int)")
    storage.execute("INSERT INTO fills VALUES ('f1','o1','s1','BTCUSDT','buy',1,10,150)")
    storage.execute(
        "CREATE TABLE candle_manifests (manifest_id text, exchange text, market_type text, symbol text, price_type text, timeframe text, min_open_time int, max_open_time int, row_count int, file_size_bytes int, partition_path text, is_active int)"
    )
    part = tmp_path / "part.parquet"
    part.write_text("fake", encoding="utf-8")
    storage.execute(
        "INSERT INTO candle_manifests VALUES ('m1','binance','spot','BTCUSDT','trade','1m',0,120000,3,4,?,1)",
        (str(part),),
    )
    storage.commit()
    return storage


def _bar(t: int, close: float = 1.0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, close, close, close, close, 1.0, True)


def _series(start: int = 0, bars: tuple[Bar, ...] | None = None) -> BarSeries:
    bars = bars or (_bar(start), _bar(start + 60_000))
    query = BarQuery(bars[0].instrument, bars[0].timeframe, start, bars[-1].time_close, gap_policy="allow_with_metadata")
    coverage = CoverageReport(start, bars[-1].time_close, bars[0].time, bars[-1].time_close, source_mix=("test",))
    return BarSeries(query, bars, coverage)


class FakeOrchestrator:
    def __init__(self, series: BarSeries | None = None):
        self.series = series or _series()
        self.loaded: list[BarQuery] = []
        self.stored: list[BarSeries] = []

    def load_bars(self, query: BarQuery, progress_callback=None):
        self.loaded.append(query)
        if progress_callback:
            progress_callback(len(self.series.bars), 1, len(self.series.bars), 1, None, "fetch")
        return self.series

    def store_bars(self, series: BarSeries):
        self.stored.append(series)
        return SimpleNamespace(rows_written=len(series.bars))

    def latest_bar_time(self, query: BarQuery):
        return self.series.bars[-1].time if self.series.bars else None

    def detect_gaps(self, query: BarQuery):
        return []


class FakeScheduler:
    def __init__(self):
        self.jobs: dict[str, object] = {}
        self.done: list[tuple[str, object]] = []
        self.failed: list[tuple[str, str]] = []

    def enqueue(self, job):
        self.jobs[job.id] = job
        return job

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def mark_running(self, job_id: str):
        self.jobs[job_id].status = SimpleNamespace(value="running")

    def mark_done(self, job_id: str, result):
        self.done.append((job_id, result))
        self.jobs[job_id].status = SimpleNamespace(value="done")

    def mark_failed(self, job_id: str, error: str):
        self.failed.append((job_id, error))
        self.jobs[job_id].status = SimpleNamespace(value="failed")

    def list_jobs(self):
        return list(self.jobs.values())


def _state(tmp_path: Path) -> SimpleNamespace:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    return SimpleNamespace(
        config=SimpleNamespace(
            sqlite_path=tmp_path / "openpine.sqlite",
            data_dir=data_root,
            data_cache_root=data_root / "cache",
        ),
        storage=_init_storage(tmp_path),
        orchestrator=FakeOrchestrator(),
        scheduler=FakeScheduler(),
        _risk_kill_switch=[False],
    )


def test_accounts_data_inventory_delete_backfill_and_risk(tmp_path, monkeypatch):
    state = _state(tmp_path)
    cache = tmp_path / "pcache"
    cache.mkdir()
    monkeypatch.setattr(accounts_data, "default_cache_dir", lambda: cache)
    meta = {
        "key": {"instrument": {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"}, "timeframe": "1m"},
        "rows": 2,
        "first_time": 0,
        "last_time": 60_000,
    }
    (cache / "good.json").write_text(json.dumps(meta), encoding="utf-8")
    (cache / "good.csv").write_text("x", encoding="utf-8")
    (cache / "bad.json").write_text("not-json", encoding="utf-8")

    root = state.config.data_cache_root / "marketdata"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "index.sqlite") as db:
        db.execute(
            "CREATE TABLE marketdata_segments (id text, exchange text, market text, symbol text, timeframe text, start_time int, end_time int, rows_count int, source_kind text)"
        )
        db.execute("INSERT INTO marketdata_segments VALUES ('seg1','binance','spot','BTCUSDT','1m',120000,180000,2,'trade_kline')")
    seg_dir = accounts_data._marketdata_segment_dir(root, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    seg_dir.mkdir(parents=True)
    (seg_dir / "chunk.parquet").write_text("x", encoding="utf-8")

    inventory = accounts_data._data_series_inventory(state)
    assert inventory and inventory[0]["symbol"] == "BTCUSDT"
    series = inventory[0]
    assert accounts_data._series_role({"timeframe": "5m", "sources": ["aggregate"]}) == "derived"
    assert accounts_data._ranges_cover_request(series["ranges"], "1m", 0, 60_000) is True
    assert accounts_data._ranges_cover_request([], "1m", 0, 60_000) is False
    assert accounts_data._estimate_unique_bars([{"from_ms": 0, "to_ms": 60_000, "rows": 2}], "1m") == 2
    assert accounts_data._estimate_unique_bars([{"rows": 7}], "1m") == 7
    assert accounts_data._estimate_bars_for_window(0, 0, "1m") == 0
    assert accounts_data._freshness_status(None, "1m") == "empty"
    assert accounts_data._database_size_bytes(state) > 0
    assert accounts_data._data_summary(state)["series_count"] >= 1
    sid = str(series["id"])
    assert sid in accounts_data._series_by_id(state)

    assert asyncio.run(accounts_data.list_accounts(state))[0].account_id == "a1"
    cache_status = asyncio.run(accounts_data.data_cache_status(state))
    assert "BTCUSDT" in cache_status.instruments
    assert asyncio.run(accounts_data.data_summary(state))["orders"]["total"] == 1
    orders = asyncio.run(accounts_data.delete_data_orders(symbol="btcusdt", state=state))
    assert orders["orders_deleted"] == 1
    assert asyncio.run(accounts_data.delete_data_orders(symbol="missing", state=state))["orders_deleted"] == 0
    assert asyncio.run(accounts_data.risk_status(state)).kill_switch is False
    assert asyncio.run(accounts_data.toggle_kill_switch(KillSwitchRequest(enabled=True), state))["kill_switch"] is True

    payload = {"exchange": "binance", "market_type": "spot", "symbol": "BTCUSDT", "timeframe": "1m", "from_time": 0, "to_time": 60_000}
    assert accounts_data._stored_ranges_cover_request(payload, state)[0] is True
    state2 = _state(tmp_path / "s2")
    state2.orchestrator = FakeOrchestrator(_series(0, (_bar(0), _bar(60_000), _bar(120_000))))
    loaded = accounts_data._run_data_backfill_sync({**payload, "to_time": 180_000}, state2, lambda *args: None)
    assert loaded["bars_loaded"] >= 0
    body = DataBackfillRequest(symbol="BTCUSDT", timeframe="1m", from_time="0", to_time="60000")
    bt = BackgroundTasks()
    queued = asyncio.run(accounts_data.data_backfill(body, bt, state2))
    assert queued["status"] in {"pending", "queued"}
    state2.storage.close()
    state.storage.close()


def test_accounts_data_refresh_and_delete_series_paths(tmp_path, monkeypatch):
    state = _state(tmp_path)
    monkeypatch.setattr(accounts_data, "default_cache_dir", lambda: tmp_path / "empty_cache")
    state.config.data_cache_root.mkdir(parents=True, exist_ok=True)
    series = accounts_data._data_series_inventory(state)[0]
    sid = str(series["id"])
    refreshed = asyncio.run(accounts_data.refresh_data_series(sid, state))
    assert refreshed["status"] in {"refreshed", "actual"}
    deleted = asyncio.run(accounts_data.delete_data_series(sid, state))
    assert deleted["status"] == "deleted"
    with pytest.raises(HTTPException):
        asyncio.run(accounts_data.refresh_data_series("missing", state))
    with pytest.raises(HTTPException):
        asyncio.run(accounts_data.delete_data_series("missing", state))
    state.storage.close()


class FakeBacktestStore:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.deleted: list[str] = []
        self.cancelled: list[str] = []
        self.failed: list[str] = []
        self.run = SimpleNamespace(
            run_id="r1",
            strategy_id="s1",
            status="running",
            started_at=10,
            finished_at=None,
            symbol="BTCUSDT",
            timeframe="1m",
            from_time=0,
            to_time=60_000,
            bars_processed=2,
        )
        self.trade = SimpleNamespace(trade_id="t1", entry_time=0, exit_time=60_000, direction="long", entry_price=1.0, exit_price=2.0, qty=1.0, net_pnl=1.0)
        report = tmp_path / "report.md"
        report.write_text("# report", encoding="utf-8")
        csv = tmp_path / "data.csv"
        csv.write_text("a,b\n1,2\n", encoding="utf-8")
        self.artifacts = [
            SimpleNamespace(artifact_type="report_md", path=str(report)),
            SimpleNamespace(artifact_type="equity_curve", path=str(csv)),
            SimpleNamespace(artifact_type="plot_outputs", path=str(csv)),
            SimpleNamespace(artifact_type="bar_outputs", path=str(csv)),
        ]

    def list_all_runs(self, limit=50):
        return [self.run]

    def list_runs(self, strategy_id: str, limit=50):
        return [self.run]

    def get_run(self, run_id: str):
        return self.run if run_id == "r1" else None

    def get_metrics(self, run_id: str):
        return {"metrics": {"total_trades": 1}}

    def delete_run(self, run_id: str):
        if run_id == "r1":
            self.deleted.append(run_id)
            return True
        return False

    def list_trades(self, run_id: str):
        return [self.trade]

    def list_artifacts(self, run_id: str):
        return self.artifacts

    def create_run(self, request):
        return "r-new"

    def mark_cancelled(self, run_id: str, message: str):
        self.cancelled.append(run_id)

    def mark_failed(self, run_id: str, message: str):
        self.failed.append(message)


class FakeBacktestState:
    def __init__(self, tmp_path: Path):
        self.backtest_store = FakeBacktestStore(tmp_path)
        self.strategy_registry = SimpleNamespace(get_strategy=lambda strategy_id: SimpleNamespace(strategy_id=strategy_id, name="Strategy", pine_id="p1", artifact_id="a1", params_hash="h", exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m", params_json="{}"))
        self.backtest_cancel_requests: set[str] = set()
        self.storage = _init_storage(tmp_path)
        self.artifact_store = SimpleNamespace(get_artifact=lambda artifact_id, pine_id=None: {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {}}}}})
        self.orchestrator = FakeOrchestrator()


def test_backtest_routes_listing_actions_progress_and_artifacts(tmp_path, monkeypatch):
    state = FakeBacktestState(tmp_path)
    monkeypatch.setattr(backtest_routes, "_estimate_backtest_market_data", lambda strategy, from_ms, to_ms: SimpleNamespace(strategy_id=strategy.strategy_id, symbol=strategy.symbol, timeframe=strategy.timeframe, requested_from=from_ms, requested_to=to_ms, effective_from=from_ms, effective_to=to_ms, earliest_available=from_ms, adjusted=False, estimated_bars=1, estimated_pages=1, model_dump=lambda: {}))
    runs = asyncio.run(backtest_routes.list_runs(state=state))
    assert runs[0].metrics["trades_total"] == 1
    assert asyncio.run(backtest_routes.get_run("r1", state)).strategy_name == "Strategy"
    with pytest.raises(HTTPException):
        asyncio.run(backtest_routes.get_run("missing", state))
    assert asyncio.run(backtest_routes.get_run_trades("r1", state))[0].trade_id == "t1"
    assert asyncio.run(backtest_routes.run_action("r1", "cancel", state))["accepted"] is True
    with pytest.raises(HTTPException):
        asyncio.run(backtest_routes.run_action("r1", "bad", state))
    state.backtest_store.run.status = "completed"
    assert asyncio.run(backtest_routes.run_action("r1", "cancel", state))["accepted"] is False
    state.backtest_store.run.status = "running"
    with pytest.raises(HTTPException):
        asyncio.run(backtest_routes.run_action("missing", "cancel", state))
    asyncio.run(backtest_routes.delete_run("r1", state))
    with pytest.raises(HTTPException):
        asyncio.run(backtest_routes.delete_run("missing", state))
    backtest_routes.ws_manager.update_progress("rid", "backtest", "running", 0.5, "Bars: 1,000/2,000")
    progress = asyncio.run(backtest_routes.get_progress("rid"))
    assert progress and progress.bars_processed == 1000
    assert asyncio.run(backtest_routes.get_progress_detail("rid"))["status"] == "running"
    assert asyncio.run(backtest_routes.get_run_report("r1", state))["format"] == "markdown"
    assert asyncio.run(backtest_routes.export_run("r1", state))["trades"][0]["trade_id"] == "t1"
    monkeypatch.setattr(backtest_routes, "_read_parquet_as_csv", lambda path: "a,b\n1,2\n")
    assert asyncio.run(backtest_routes.get_run_equity("r1", state))["format"] == "csv"
    assert asyncio.run(backtest_routes.get_run_plots("r1", state))["format"] == "csv"
    assert asyncio.run(backtest_routes.get_run_bar_outputs("r1", state))["format"] == "csv"
    state.backtest_store.artifacts = []
    with pytest.raises(HTTPException):
        asyncio.run(backtest_routes.get_run_equity("r1", state))
    state.storage.close()


class PineRegistryFake:
    def __init__(self):
        self.source = SimpleNamespace(id="src1", name="MyStrategy", source_text="strategy('x')", source_type="strategy", version="1", active_artifact_id=None, created_at=1, updated_at=2)
        self.active: list[tuple[str, str]] = []

    def get_source(self, source_id: str):
        if source_id != "src1":
            raise KeyError(source_id)
        return self.source

    def set_active_artifact(self, source_id: str, artifact_id: str) -> None:
        self.active.append((source_id, artifact_id))


class ArtifactStoreFake:
    def __init__(self, root: Path):
        self._root = root
        self.saved: list[dict[str, object]] = []

    def save_artifact(self, **kwargs) -> None:
        self.saved.append(kwargs)
        artifact_dir = self._root / kwargs["source_id"] / kwargs["artifact_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "generated_strategy.py").write_text(str(kwargs["python_code"]), encoding="utf-8")
        (artifact_dir / "compile_meta.json").write_text(json.dumps(kwargs["compile_meta"]), encoding="utf-8")

    def get_artifact(self, artifact_id: str, source_id: str | None = None):
        source_id = source_id or "src1"
        artifact_dir = self._root / source_id / artifact_id
        if not artifact_dir.exists():
            raise FileNotFoundError(artifact_id)
        return {"artifact_dir": str(artifact_dir), "compile_meta": json.loads((artifact_dir / "compile_meta.json").read_text())}


def _pine_state(tmp_path: Path):
    return SimpleNamespace(pine_registry=PineRegistryFake(), artifact_store=ArtifactStoreFake(tmp_path / "artifacts"))


def test_pine_ops_compile_validate_and_artifact_routes(tmp_path, monkeypatch):
    import pine2ast
    import ast2python

    class Severity:
        value = "error"

    diag = SimpleNamespace(message="bad", code="P", severity=Severity())
    visual_diag = SimpleNamespace(
        message="Builtin plot has no runtime-equivalent visual output under runtime_contract v1.4",
        code="P2A1507",
        severity=Severity(),
    )
    result_ok = SimpleNamespace(ok=True, diagnostics=[], ast=SimpleNamespace(kind="Program"))
    result_visual = SimpleNamespace(
        ok=False,
        diagnostics=[visual_diag],
        ast=SimpleNamespace(kind="Program"),
    )
    result_bad = SimpleNamespace(ok=False, diagnostics=[diag], ast=None)
    parse_options = []
    translate_kwargs = []

    def fake_parse_code(*args, **kwargs):
        parse_options.append(kwargs.get("options"))
        return result_ok

    def fake_translate_ast(ast, **kwargs):
        translate_kwargs.append(kwargs)
        return SimpleNamespace(
            code="# generated",
            diagnostics=[],
            metadata={"declaration": {}, "visual_policy": kwargs.get("visual_policy")},
        )

    monkeypatch.setattr(pine2ast, "parse_code", fake_parse_code)
    monkeypatch.setattr(pine2ast, "ast_to_dict", lambda ast: {"type": "Program", "body": []})
    monkeypatch.setattr(pine2ast, "ast_to_json", lambda ast: "{}")
    monkeypatch.setattr(ast2python, "translate_ast", fake_translate_ast)
    state = _pine_state(tmp_path)
    tasks = BackgroundTasks()
    queued = asyncio.run(pine_ops.compile_pine("src1", tasks, state))
    assert queued["status"] == "queued"
    task = tasks.tasks[0]
    asyncio.run(task.func(*task.args, **task.kwargs))
    assert state.artifact_store.saved and state.pine_registry.active
    artifacts = asyncio.run(pine_ops.list_artifacts("src1", state))
    assert artifacts and artifacts[0]["has_generated_strategy"] is True
    artifact_id = artifacts[0]["artifact_id"]
    inspected = asyncio.run(pine_ops.inspect_artifact("src1", artifact_id, state))
    assert inspected["generated_python_lines"] == 1
    assert asyncio.run(pine_ops.compile_progress(queued["operation_id"])) is not None
    assert asyncio.run(pine_ops.validate_pine("src1", state))["valid"] is True
    assert translate_kwargs[0]["visual_policy"] == "record"
    assert [getattr(opts, "runtime_contract_profile", None) for opts in parse_options] == [
        "v1.4",
        "v1.4",
    ]
    assert all(
        getattr(opts, "strict_builtin_namespaces", False) is True
        for opts in parse_options
    )

    monkeypatch.setattr(pine2ast, "parse_code", lambda *a, **k: result_visual)
    visual_tasks = BackgroundTasks()
    asyncio.run(
        pine_ops.compile_pine("src1", visual_tasks, state)  # type: ignore[arg-type]
    )
    asyncio.run(
        visual_tasks.tasks[0].func(
            *visual_tasks.tasks[0].args,
            **visual_tasks.tasks[0].kwargs,
        )
    )
    compile_meta = state.artifact_store.saved[-1]["compile_meta"]
    assert compile_meta["filtered_visual_diagnostics"] == [
        "P2A1507: Builtin plot has no runtime-equivalent visual output under runtime_contract v1.4"
    ]
    assert asyncio.run(
        pine_ops.validate_pine("src1", state)  # type: ignore[arg-type]
    )["valid"] is True

    monkeypatch.setattr(pine2ast, "parse_code", lambda *a, **k: result_bad)
    assert asyncio.run(pine_ops.validate_pine("src1", state))["valid"] is False
    with pytest.raises(HTTPException):
        asyncio.run(pine_ops.compile_pine("missing", BackgroundTasks(), state))
    with pytest.raises(HTTPException):
        asyncio.run(pine_ops.list_artifacts("missing", state))
    with pytest.raises(HTTPException):
        asyncio.run(pine_ops.inspect_artifact("src1", "missing", state))


def test_pine_ops_compile_failure_paths(tmp_path, monkeypatch):
    import pine2ast
    import ast2python

    class Severity:
        value = "error"

    diag = SimpleNamespace(message="boom", code="E", severity=Severity())
    state = _pine_state(tmp_path)
    monkeypatch.setattr(pine2ast, "parse_code", lambda *a, **k: SimpleNamespace(ok=False, diagnostics=[diag], ast=None))
    tasks = BackgroundTasks()
    asyncio.run(pine_ops.compile_pine("src1", tasks, state))
    asyncio.run(tasks.tasks[0].func(*tasks.tasks[0].args, **tasks.tasks[0].kwargs))
    assert state.artifact_store.saved == []

    monkeypatch.setattr(pine2ast, "parse_code", lambda *a, **k: SimpleNamespace(ok=True, diagnostics=[], ast=object()))
    monkeypatch.setattr(pine2ast, "ast_to_dict", lambda ast: {"type": "Program", "body": []})
    monkeypatch.setattr(ast2python, "translate_ast", lambda *a, **k: SimpleNamespace(code="", diagnostics=[diag], metadata={}))
    tasks2 = BackgroundTasks()
    asyncio.run(pine_ops.compile_pine("src1", tasks2, state))
    asyncio.run(tasks2.tasks[0].func(*tasks2.tasks[0].args, **tasks2.tasks[0].kwargs))
    assert state.artifact_store.saved == []
