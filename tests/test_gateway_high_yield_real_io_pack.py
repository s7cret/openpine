from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway import live_runner as lr
from openpine.gateway import server
from openpine.gateway.deps import get_state
from openpine.gateway.routes import accounts_data as ad
from openpine.gateway.routes import tv_parity


class _Storage:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql: str, params: tuple[object, ...] = ()):
        return self.connection.execute(sql, params)

    @contextmanager
    def transaction(self):
        try:
            yield self
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class _Orchestrator:
    def __init__(self, bars: tuple[Bar, ...]) -> None:
        self.bars = bars
        self.queries: list[BarQuery] = []

    def load_bars(self, query: BarQuery):
        self.queries.append(query)
        coverage = CoverageReport(
            query.start_ms,
            query.end_ms,
            self.bars[0].time if self.bars else None,
            self.bars[-1].time_close if self.bars else None,
            source_mix=("test",),
        )
        return BarSeries(query, self.bars, coverage)


def _bars() -> tuple[Bar, ...]:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    return tuple(
        Bar(
            instrument,
            timeframe,
            open_time,
            open_time + 59_999,
            price,
            price + 2,
            price - 1,
            price + 1,
            10.0,
            True,
        )
        for open_time, price in ((0, 100.0), (60_000, 101.0))
    )


def _accounts_app(state: object) -> TestClient:
    app = FastAPI()
    app.include_router(ad.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    return TestClient(app)


def test_accounts_data_routes_use_real_sqlite_and_marketdata_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cache_dir = tmp_path / "persistent"
    cache_dir.mkdir()
    (cache_dir / "btc.json").write_text(
        json.dumps(
            {
                "key": {
                    "instrument": {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
                    "timeframe": "1m",
                },
                "rows": 2,
                "first_time": 0,
                "last_time": 60_000,
            }
        ),
        encoding="utf-8",
    )
    (cache_dir / "btc.csv").write_text("time,close\n0,100\n60000,101\n", encoding="utf-8")
    monkeypatch.setattr(ad, "default_cache_dir", lambda: cache_dir)

    data_dir = tmp_path / "data"
    store_root = data_dir / "cache" / "marketdata"
    store_root.mkdir(parents=True)
    index = sqlite3.connect(store_root / "index.sqlite")
    index.execute(
        "CREATE TABLE marketdata_segments (id INTEGER, exchange TEXT, market TEXT, symbol TEXT, timeframe TEXT, start_time INTEGER, end_time INTEGER, rows_count INTEGER, source_kind TEXT)"
    )
    index.execute(
        "INSERT INTO marketdata_segments VALUES (1, 'binance', 'spot', 'BTCUSDT', '1m', 0, 60000, 2, 'trade_kline')"
    )
    index.commit()
    index.close()
    segment_dir = ad._marketdata_segment_dir(store_root, "binance", "spot", "BTCUSDT", "1m", "trade_kline")
    segment_dir.mkdir(parents=True)
    (segment_dir / "part.parquet").write_bytes(b"real-segment")

    storage = _Storage(tmp_path / "gateway.sqlite")
    storage.execute("CREATE TABLE accounts (account_id TEXT, name TEXT, exchange TEXT, market_type TEXT, mode TEXT, live_enabled INTEGER, created_at INTEGER)")
    storage.execute("INSERT INTO accounts VALUES ('a1', 'Paper', 'binance', 'spot', 'paper', 0, 10)")
    storage.execute("CREATE TABLE strategy_instances (strategy_id TEXT, name TEXT)")
    storage.execute("INSERT INTO strategy_instances VALUES ('s1', 'Momentum')")
    storage.execute("CREATE TABLE orders (order_id TEXT, symbol TEXT, strategy_id TEXT, status TEXT, created_at INTEGER)")
    storage.execute("CREATE TABLE fills (fill_id TEXT, order_id TEXT)")
    storage.execute("INSERT INTO orders VALUES ('o1', 'BTCUSDT', 's1', 'filled', 100)")
    storage.execute("INSERT INTO orders VALUES ('o2', 'ETHUSDT', 's1', 'open', 101)")
    storage.execute("INSERT INTO fills VALUES ('f1', 'o1')")
    storage.execute("CREATE TABLE candle_manifests (exchange TEXT, market_type TEXT, symbol TEXT, price_type TEXT, timeframe TEXT, min_open_time INTEGER, max_open_time INTEGER, row_count INTEGER, file_size_bytes INTEGER, manifest_id TEXT, is_active INTEGER, partition_path TEXT)")
    storage.connection.commit()

    state = SimpleNamespace(
        storage=storage,
        orchestrator=_Orchestrator(_bars()),
        config=SimpleNamespace(
            sqlite_path=tmp_path / "gateway.sqlite",
            data_dir=data_dir,
            data_cache_root=None,
            marketdata_timeframes=("1m", "1h"),
            marketdata_default_timeframe="1m",
            marketdata_stable_quotes_only=True,
            marketdata_stable_quote_assets=("USDT",),
        ),
        _risk_kill_switch=[False],
    )
    ad._invalidate_data_summary_cache(state)
    client = _accounts_app(state)
    try:
        accounts = client.get("/api/accounts")
        assert accounts.status_code == 200
        assert accounts.json() == [{"account_id": "a1", "name": "Paper", "exchange": "binance", "market_type": "spot", "mode": "paper", "live_enabled": False, "created_at": 10}]

        summary = client.get("/api/data/summary")
        assert summary.status_code == 200
        assert summary.json()["orders"]["total"] == 2
        assert summary.json()["series_count"] == 1
        assert summary.json()["series"][0]["bar_count"] == 2

        klines = client.get("/api/data/klines", params={"symbol": "btcusdt", "start_time": 0, "end_time": 120_000, "interval": "1m"})
        assert klines.status_code == 200
        assert [item["close"] for item in klines.json()["bars"]] == [101.0, 102.0]
        assert state.orchestrator.queries[-1].instrument.symbol == "BTCUSDT"

        ticker = client.get("/api/data/ticker24h", params={"symbol": "BTCUSDT"})
        assert ticker.status_code == 200
        assert ticker.json()["lastPrice"] == 102.0
        assert ticker.json()["volume"] == 20.0

        disabled = client.get("/api/data/klines", params={"symbol": "BTCUSDT", "start_time": 0, "end_time": 60_000, "exchange": "unknown"})
        assert disabled.status_code == 404
        assert disabled.json()["detail"] == "unknown exchange: unknown"

        deleted = client.delete("/api/data/orders", params={"symbol": "btcusdt", "status": "filled"})
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "orders_deleted": 1, "fills_deleted": 1}
        assert storage.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert storage.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0

        kill = client.post("/api/risk/kill-switch", json={"enabled": True})
        assert kill.status_code == 200 and kill.json() == {"kill_switch": True}
        assert client.get("/api/risk").json()["kill_switch"] is True
    finally:
        storage.close()


def test_tv_parity_filesystem_api_filters_downloads_and_deletes(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    root = tmp_path / "tv-parity"
    newer = root / "run-new"
    demo = root / "tvpar_demo_seed"
    corrupt = root / "corrupt"
    for directory in (newer, demo, corrupt):
        directory.mkdir(parents=True)
    (newer / "tv_parity_result.json").write_text(
        json.dumps({"run_id": "run-new", "strategy_id": "s1", "source": "tradingview_csv", "status": "done", "queued_at": 20, "candle_summary": {"symbol": "BTCUSDT", "exchange": "binance", "market_type": "spot", "timeframe": "1m", "valid_bars": 2, "from_time": 0, "to_time": 120000}}),
        encoding="utf-8",
    )
    (demo / "tv_parity_result.json").write_text(json.dumps({"run_id": "tvpar_demo_seed", "strategy_id": "s1", "source": "exchange_data", "queued_at": 30}), encoding="utf-8")
    (corrupt / "tv_parity_result.json").write_text("{not json", encoding="utf-8")
    artifact = newer / "openpine_outputs" / "plots.csv"
    artifact.parent.mkdir()
    artifact.write_text("time,value\n0,1\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)

    history = client.get("/api/tv-parity/runs")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["run_id"] == "run-new"
    assert history.json()["items"][0]["result_path"].endswith(
        "tv-parity/run-new/tv_parity_result.json"
    )

    demos = client.get("/api/tv-parity/runs", params={"include_demo": "true", "source": "exchange"})
    assert demos.status_code == 200
    assert demos.json()["items"][0]["run_id"] == "tvpar_demo_seed"

    detail = client.get("/api/tv-parity/runs/run-new")
    assert detail.status_code == 200
    artifacts = {item["name"]: item for item in detail.json()["artifacts"]}
    assert artifacts["openpine_plots"] == {"name": "openpine_plots", "filename": "plots.csv", "size_bytes": 15, "download_url": "/api/tv-parity/runs/run-new/artifacts/openpine_plots"}
    assert artifacts["result_json"]["filename"] == "tv_parity_result.json"
    download = client.get("/api/tv-parity/runs/run-new/artifacts/openpine_plots")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert download.text == "time,value\n0,1\n"
    assert client.get("/api/tv-parity/runs/run-new/artifacts/nope").status_code == 404

    deleted = client.delete("/api/tv-parity/runs/run-new")
    assert deleted.status_code == 204
    assert not newer.exists()
    assert client.get("/api/tv-parity/runs/run-new").status_code == 404


def test_tv_replay_snapshot_gate_and_upload_limit_fail_closed(tmp_path: Path) -> None:
    candles = tmp_path / "candles.csv"
    candles.write_text("time,open,high,low,close\n0,1,2,0,1\n", encoding="utf-8")
    parsed = tv_parity.parse_tradingview_candles_csv(candles, exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m")
    config = SimpleNamespace(start_time=0, end_time=60_000, exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m")
    with pytest.raises(RuntimeError, match="snapshot hash mismatch"):
        tv_parity._run_isolated_tv_replay(SimpleNamespace(run_isolated=lambda *_args, **_kwargs: None), "x", list(parsed.bars), config, {}, None, expected_data_snapshot_hash="not-a-hash")

    from openpine.run_identity import execution_data_snapshot_hash

    expected = execution_data_snapshot_hash(bars=list(parsed.bars), supplemental_bars=None, exchange="binance", market="spot", symbol="BTCUSDT", timeframe="1m", start_ms=0, end_ms=60_000, finality_policy="CLOSED_BAR_ONLY")
    captured = {}
    result = tv_parity._run_isolated_tv_replay(SimpleNamespace(run_isolated=lambda source, bars, cfg, **kwargs: captured.update({"source": source, "bars": bars, "htf": kwargs.get("htf_bars")}) or "ran"), "source", list(parsed.bars), config, {}, None, expected_data_snapshot_hash=expected)
    assert result == "ran"
    assert captured["source"] == "source" and len(captured["bars"]) == 1

    from fastapi import HTTPException, UploadFile
    import io

    async def store_too_large() -> None:
        upload = UploadFile(filename="candles.csv", file=io.BytesIO(b"abcd"))
        with pytest.raises(HTTPException) as excinfo:
            await tv_parity._store_upload(upload, upload_root=tmp_path / "uploads", fallback_name="candles.csv", max_bytes=3)
        assert excinfo.value.status_code == 413
        assert not (tmp_path / "uploads" / "candles.csv").exists()

    asyncio.run(store_too_large())


class _SnapshotStore:
    def __init__(self, metadata=None, *, fail=False) -> None:
        self.metadata = metadata
        self.fail = fail
        self.saved: list[dict[str, object]] = []
        self.invalidated: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def latest_snapshot_metadata(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("metadata unavailable")
        return self.metadata

    def load_latest_compatible(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("snapshot unavailable")
        return self.metadata

    def save_runtime_snapshot(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("save unavailable")
        self.saved.append(kwargs)

    def mark_invalid(self, *args, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("invalidate unavailable")
        self.invalidated.append((args, kwargs))


def _strategy(**overrides):
    values = {"strategy_id": "s1", "artifact_id": "a1", "params_hash": "p1", "exchange": "BINANCE", "market_type": "SPOT", "symbol": "btcusdt", "timeframe": "1m", "enabled": True, "status": "running"}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_live_runner_state_snapshot_and_fail_closed_paths(monkeypatch) -> None:
    store = _SnapshotStore(SimpleNamespace(bar_time=120_000))
    runner = lr.LiveStrategyRunner(state_store=store)
    strategy = _strategy()
    assert runner._latest_processed_bar_time(strategy, 180_000) == 120_000
    assert runner._load_resume_snapshot(strategy, instrument_key=runner._instrument_key(strategy), timeframe=runner._timeframe_key(strategy), at_or_before_bar_time=180_000).bar_time == 120_000
    runner._save_resume_snapshot(strategy, result=SimpleNamespace(resume_state={"x": 1}), instrument_key=runner._instrument_key(strategy), timeframe=runner._timeframe_key(strategy), bar_time=180_000, data_fingerprint="abc")
    runner._mark_resume_snapshot_invalid(strategy, 180_000)
    assert store.saved[0]["reason"] == "live_bar"
    assert store.invalidated == [(('s1',), {"since_bar_time": 180_000})]
    assert runner._instrument_key(strategy) == {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT", "price_type": "trade"}

    failing = lr.LiveStrategyRunner(state_store=_SnapshotStore(fail=True))
    assert failing._latest_processed_bar_time(strategy, 1) == 0
    assert failing._load_resume_snapshot(strategy, instrument_key={}, timeframe={}, at_or_before_bar_time=1) is None
    failing._save_resume_snapshot(strategy, result=SimpleNamespace(resume_state={}), instrument_key={}, timeframe={}, bar_time=1, data_fingerprint=None)
    failing._mark_resume_snapshot_invalid(strategy, 1)

    runner.set_strategy_htf_timeframe("s1", "5m")
    assert runner._requested_htf_timeframe(strategy) == "5m"
    runner.set_strategy_htf_timeframe("s1", None)
    runner.set_strategy_mtf_series("s1", [])
    assert runner._requested_htf_timeframe(strategy) is None

    with pytest.raises(lr.CanonicalOrderRouterRequired, match="canonical Intent"):
        asyncio.run(runner._process_strategy(strategy, 240_001))
    assert runner._strategy_states["s1"].last_check_ms == 240_001
    assert asyncio.run(runner._process_strategy(_strategy(timeframe="1M"), 180_001)) is None

    monkeypatch.setattr(lr.LiveStrategyRunner, "_default_state_store", staticmethod(lambda: None))
    stopped = lr.LiveStrategyRunner()
    stopped.stop()
    assert asyncio.run(stopped.wait_stopped()) is True


def test_live_runner_htf_loader_and_series_fingerprint_use_real_contracts() -> None:
    chart_bars = list(_bars())
    orchestrator = _Orchestrator(tuple(chart_bars))
    runner = lr.LiveStrategyRunner(orchestrator=orchestrator, state_store=None)
    loaded = runner._load_mtf_provider_bars(_strategy(), chart_bars, "ETHUSDT", "5m")
    assert loaded == chart_bars
    query = orchestrator.queries[-1]
    assert query.instrument.symbol == "ETHUSDT"
    assert query.timeframe.canonical == "5m"
    series = orchestrator.load_bars(BarQuery(chart_bars[0].instrument, parse_timeframe("1m"), 0, 120_000, gap_policy="allow_with_metadata"))
    fingerprint = runner._series_fingerprint(series)
    assert len(fingerprint) == 64
    assert fingerprint != runner._series_fingerprint(SimpleNamespace(query=series.query, bars=tuple(chart_bars[:1])))


def test_server_shutdown_helpers_and_configuration_branches(monkeypatch) -> None:
    monkeypatch.delenv("GATEWAY_FLAG", raising=False)
    assert server._env_flag("GATEWAY_FLAG", True) is True
    monkeypatch.setenv("GATEWAY_FLAG", "off")
    assert server._env_flag("GATEWAY_FLAG", True) is False
    monkeypatch.setenv("GATEWAY_INT", "bad")
    monkeypatch.setenv("GATEWAY_FLOAT", "bad")
    assert server._env_int("GATEWAY_INT", 7) == 7
    assert server._env_float("GATEWAY_FLOAT", 1.5) == 1.5

    class Runner:
        def __init__(self, answer: bool) -> None:
            self.answer = answer
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

        async def wait_stopped(self) -> bool:
            return self.answer

    class Fetcher:
        def __init__(self, alive: bool) -> None:
            self._thread = SimpleNamespace(is_alive=lambda: alive)
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    class Supervisor:
        async def stop(self) -> bool:
            return True

    runner = Runner(True)
    fetcher = Fetcher(False)
    assert asyncio.run(server._stop_runtime_services(runner=runner, fetcher=fetcher, supervisor=Supervisor())) is True
    assert runner.stopped and fetcher.stopped
    assert server._thread_service_quiesced(SimpleNamespace(_thread=SimpleNamespace(is_alive=lambda: True))) is False
    assert asyncio.run(server._stop_live_runner(SimpleNamespace(stop=lambda: None))) is False


def test_accounts_backfill_chunk_math_and_status_helpers_cover_unaligned_windows(
    monkeypatch,
) -> None:
    minute = parse_timeframe("1m").duration_ms
    assert minute is not None
    source = parse_timeframe("1m")
    target = parse_timeframe("5m")
    monkeypatch.setattr(ad, "_DATA_BACKFILL_SUBPROCESS_SOURCE_BARS", 6)
    chunks = list(ad._iter_backfill_source_chunks(minute, 19 * minute, source, target))
    assert chunks[0][0] == minute
    assert chunks[-1][1] == 19 * minute
    assert all(start < end for start, end in chunks)
    assert list(ad._iter_backfill_source_chunks(0, 3 * minute, SimpleNamespace(duration_ms=None), target)) == [(0, 3 * minute)]
    assert ad._combined_coverage_status(["valid", "gap"], False) == "gap"
    assert ad._combined_coverage_status([], True) == "valid"
    assert ad._min_optional_ms(None, "10") == 10
    assert ad._min_optional_ms(5, 10) == 5
    assert ad._max_optional_ms(None, "10") == 10
    assert ad._max_optional_ms(5, 10) == 10
    sample: list[list[int]] = []
    ad._extend_missing_sample(sample, tuple((index, index + 1) for index in range(12)))
    assert sample == [[index, index + 1] for index in range(10)]
    assert "already cached" in ad._backfill_done_message(
        {"bars_loaded": 0, "skipped_existing": 2, "bars_available": 2, "coverage_complete": True}
    )
    assert "No candles returned" in ad._backfill_done_message({"bars_loaded": 0})


def test_tv_parity_background_writes_failure_receipt_for_missing_artifact(tmp_path: Path) -> None:
    candles = tmp_path / "candles.csv"
    candles.write_text("time,open,high,low,close\n0,1,2,0,1\n", encoding="utf-8")
    parsed = tv_parity.parse_tradingview_candles_csv(
        candles, exchange="binance", market_type="spot", symbol="BTCUSDT", timeframe="1m"
    )
    failures: list[tuple[str, str]] = []
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(
            get_strategy=lambda _strategy_id: SimpleNamespace(artifact_id="missing", pine_id="p1")
        ),
        artifact_store=SimpleNamespace(
            get_artifact=lambda *_args: (_ for _ in ()).throw(FileNotFoundError("sealed artifact absent"))
        ),
        backtest_store=SimpleNamespace(mark_failed=lambda run_id, error: failures.append((run_id, error))),
    )
    run_root = tmp_path / "run"
    asyncio.run(
        tv_parity._run_tv_parity_background(
            state=state,
            strategy_id="s1",
            run_id="run-1",
            parsed=parsed,
            run_root=run_root,
            uploads={"candles": None},
            params_override=None,
            warmup_bars=0,
            capture_plots=False,
            compare_from_ms=0,
            compare_to_ms=60_000,
            abs_tol=0.0,
            rel_tol=0.0,
            include_base_columns=False,
        )
    )
    receipt = json.loads((run_root / "tv_parity_result.json").read_text(encoding="utf-8"))
    assert failures == [("run-1", "sealed artifact absent")]
    assert receipt["status"] == "failed"
    assert receipt["error"] == "sealed artifact absent"


def test_server_rehydrates_persisted_strategy_job_and_rejects_poisoned_payloads(tmp_path: Path) -> None:
    from base64 import urlsafe_b64encode

    from openpine.jobs import JobType
    from openpine.jobs.persist import JobV1Error, JobV1Store

    prefix = "inline:openpine.strategy-job-input.v1:"
    payload = {"strategy_id": "strategy-1", "bar_time": 60_000}
    encoded = urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    record = {
        "job_id": "persisted-1",
        "kind": "paper",
        "idempotency_key": "paper:strategy-1:60000",
        "input_artifact_refs": [prefix + encoded],
    }
    job = server._strategy_job_from_persisted(record)
    assert job.job_type is JobType.PAPER_BAR_PROCESS
    assert job.strategy_id == "strategy-1"
    assert job.input == payload
    with pytest.raises(JobV1Error, match="missing"):
        server._strategy_job_from_persisted({**record, "input_artifact_refs": []})
    with pytest.raises(JobV1Error, match="invalid"):
        server._strategy_job_from_persisted({**record, "input_artifact_refs": [prefix + "not_base64"]})
    with pytest.raises(JobV1Error, match="incomplete"):
        server._strategy_job_from_persisted({**record, "kind": "unknown"})

    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        stored = store.create(
            job_id="sqlite-backed-job",
            kind="paper",
            actor="gateway-test",
            idempotency_key="sqlite-backed-key",
            input_artifact_refs=[prefix + encoded],
        )
        restored = server._strategy_job_from_persisted(stored)
        assert restored.id == "sqlite-backed-job"
        assert restored.input["bar_time"] == 60_000
    finally:
        store.close()
