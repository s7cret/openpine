from __future__ import annotations

import asyncio
from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.routes import dashboard as dashboard_routes
from openpine.jobs.models import Job, JobStatus, JobType
from openpine.jobs.scheduler import JobScheduler


class Storage:
    def __init__(self, *, fail: bool = False, with_events: bool = True):
        self.fail = fail
        self.with_events = with_events
        self.queries: list[str] = []

    def execute(self, sql: str, params=()):
        self.queries.append(sql)
        if self.fail:
            raise RuntimeError("storage unavailable")
        compact = " ".join(sql.lower().split())
        if "pragma table_info(events)" in compact:
            rows = [(0, "event_id"), (1, "timestamp_ms")] if self.with_events else []
            return SimpleNamespace(fetchall=lambda: rows)
        if "select max(" in compact:
            return SimpleNamespace(fetchone=lambda: (777,))
        if "from backtest_runs" in compact:
            return SimpleNamespace(
                fetchall=lambda: [
                    ("r1", "s1", "completed", 10, 20, 5, None),
                    ("r2", "s2", "cancelled", 30, 40, 25, "stop"),
                    ("r3", "s3", "queued", None, None, 35, None),
                ]
            )
        if "from orders" in compact:
            return SimpleNamespace(fetchone=lambda: ("o1", "filled", "buy", "BTCUSDT", 1, 2))
        raise AssertionError(sql)


class Registry:
    def __init__(self, strategies):
        self._strategies = strategies

    def list_strategies(self):
        return list(self._strategies)


class Orchestrator:
    def __init__(self, *, fail: bool = False, latest: int | None = 1000):
        self.fail = fail
        self.latest = latest

    def load_bars(self, query):
        if self.fail:
            raise RuntimeError("bars unavailable")
        inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
        tf = parse_timeframe("1m")
        bar = Bar(inst, tf, self.latest or 0, (self.latest or 0) + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)
        coverage = CoverageReport(query.start_ms, query.end_ms, bar.time, bar.time_close, source_mix=("test",))
        return BarSeries(query, (bar,), coverage)

    def latest_bar_time(self, query: BarQuery):
        if self.fail:
            raise RuntimeError("latest unavailable")
        return self.latest


class Worker:
    def __init__(self, alive: bool):
        self._alive = alive

    def is_alive(self):
        return self._alive


def _strategy(**kwargs):
    defaults = dict(
        strategy_id="s1",
        name="Strategy",
        symbol="BTCUSDT",
        timeframe="1m",
        mode="paper",
        status="active",
        enabled=True,
        exchange="binance",
        market_type="spot",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_job_scheduler_lifecycle_dedupe_serialization_and_locks(monkeypatch):
    scheduler = JobScheduler()
    monkeypatch.setattr("openpine.jobs.scheduler.time.time", lambda: 1000.0)

    first = scheduler.enqueue(Job(job_type=JobType.BACKTEST, priority=1, idempotency_key="same", serialization_key="s1"))
    assert scheduler.enqueue(Job(job_type=JobType.BACKTEST, priority=99, idempotency_key="same")) is first
    second = scheduler.enqueue(Job(job_type=JobType.REPORT, priority=10, serialization_key="s1"))
    third = scheduler.enqueue(Job(job_type=JobType.BACKFILL, priority=5, serialization_key="s2"))

    job = scheduler.dequeue()
    assert job is second
    scheduler.mark_running(job.id)
    assert scheduler.is_running("s1") is True
    assert scheduler.dequeue() is third
    assert scheduler.get_job("missing") is None
    scheduler.mark_running("missing")
    scheduler.mark_done("missing")
    scheduler.mark_failed("missing", "bad")
    scheduler.cancel("missing")

    scheduler.mark_done(job.id, {"ok": True})
    assert scheduler.is_running("s1") is False
    scheduler.mark_running(third.id)
    scheduler.mark_failed(third.id, "boom")
    scheduler.cancel(third.id)
    assert third.status == JobStatus.FAILED
    assert scheduler.list_jobs(JobStatus.FAILED) == [third]

    scheduler.mark_running(first.id)
    scheduler.cancel(first.id)
    assert first.status == JobStatus.CANCELLED
    scheduler.cancel(first.id)

    assert scheduler.acquire_lock("resource", "owner", ttl_seconds=60) is True
    assert scheduler.acquire_lock("resource", "owner", ttl_seconds=60) is True
    assert scheduler.acquire_lock("resource", "other", ttl_seconds=60) is False
    scheduler.release_lock("resource", "not-owner")
    assert scheduler.acquire_lock("resource", "other", ttl_seconds=60) is False
    scheduler.release_lock("resource", "owner")
    assert scheduler.acquire_lock("resource", "other", ttl_seconds=-1) is True
    assert scheduler.recover_stale_locks() == 1


def test_dashboard_helpers_and_route_success_error_paths(monkeypatch):
    strat_ok = _strategy(strategy_id="s1", enabled=True, status="active")
    strat_error = _strategy(strategy_id="s2", enabled=False, status="error", symbol="ETHUSDT")
    scheduler = JobScheduler()
    pending = scheduler.enqueue(Job(job_type=JobType.BACKTEST, strategy_id="s1", priority=1))
    running = scheduler.enqueue(Job(job_type=JobType.REPORT, strategy_id="s2", priority=2))
    scheduler.dequeue()
    scheduler.mark_running(running.id)
    scheduler.mark_done(pending.id, {"ok": True})

    monkeypatch.setattr(dashboard_routes.time, "time", lambda: 1000.0)
    state = SimpleNamespace(
        strategy_registry=Registry([strat_ok, strat_error]),
        scheduler=scheduler,
        storage=Storage(),
        orchestrator=Orchestrator(latest=999_000),
        _risk_kill_switch=[True],
        _startup_time=900.0,
        _fetcher=SimpleNamespace(last_fetch_at=123456),
        _live_runner=SimpleNamespace(_running=True),
        _background_worker_process=Worker(False),
    )

    assert dashboard_routes._normalize_job_status("succeeded") == "done"
    assert dashboard_routes._normalize_job_status("error") == "failed"
    assert dashboard_routes._normalize_job_status("cancelled") == "failed"
    assert dashboard_routes._normalize_job_status("custom") == "custom"
    assert dashboard_routes._count_jobs([{"status": "done"}, {"status": "failed"}], "done") == 1
    persistent = dashboard_routes._persistent_jobs(state)
    assert [j["status"] for j in persistent] == ["done", "failed", "pending"]
    health_ok = dashboard_routes._strategy_health(state, strat_ok)
    assert health_ok["runner_alive"] is True and health_ok["last_order"]["order_id"] == "o1"

    response = asyncio.run(dashboard_routes.dashboard(state))
    assert response.kill_switch is True
    assert response.last_event_time == 777
    assert response.last_bar_update == 123456
    assert response.jobs.done >= 2

    error_state = SimpleNamespace(
        strategy_registry=Registry([_strategy(status="active", enabled=True)]),
        scheduler=JobScheduler(),
        storage=Storage(fail=True),
        orchestrator=Orchestrator(fail=True),
        _risk_kill_switch=[False],
        _startup_time=990.0,
        _fetcher=None,
        _live_runner=SimpleNamespace(_running=False),
        _background_worker_process=Worker(False),
    )
    assert dashboard_routes._persistent_jobs(error_state) == []
    bad_health = dashboard_routes._strategy_health(error_state, _strategy(enabled=True, status="active"))
    assert bad_health["status"] == "runner_off"
    response2 = asyncio.run(dashboard_routes.dashboard(error_state))
    assert response2.last_event_time is None
    assert response2.last_bar_update is None


def test_release_and_distribution_cli_edges(tmp_path, capsys):
    from openpine import __version__
    from openpine.distribution import build_zip, distribution_manifest, main as dist_main, source_files
    from openpine.release import release_report, main as release_main

    root = tmp_path / "repo"
    (root / "openpine" / "storage" / "migrations").mkdir(parents=True)
    (root / "openpine" / "storage" / "migrations" / "001_init.sql").write_text("CREATE TABLE x(id int);\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='openpine'\nversion='0.0.0'\ndependencies=[]\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "README.md").write_text("repo", encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / "artifact.whl").write_text("wheel", encoding="utf-8")
    (root / "pkg.pyc").write_text("bad", encoding="utf-8")
    (root / "keep.py").write_text("print('ok')\n", encoding="utf-8")

    manifest = distribution_manifest(root)
    assert any("dist/artifact.whl" in err for err in manifest.hygiene_errors)
    assert all(not str(path).endswith(".pyc") for path in source_files(root))
    assert dist_main(["manifest", "--root", str(root)]) == 1
    assert "hygiene_errors" in capsys.readouterr().out

    zip_path = tmp_path / "out.zip"
    digest = build_zip(root, zip_path, archive_root="openpine-test")
    assert len(digest) == 64 and zip_path.exists()
    assert dist_main(["build-zip", "--root", str(root), "--output", str(tmp_path / "cli.zip")]) == 0

    report = release_report(root)
    assert not report.ok
    assert any("pyproject version" in error for error in report.errors)
    assert any("missing canonical docs" in error for error in report.errors)
    assert release_main(["--root", str(root)]) == 1
    out_path = tmp_path / "release.json"
    assert release_main(["--root", str(root), "--json", str(out_path)]) == 1
    assert f'"version": "{__version__}"' in out_path.read_text(encoding="utf-8")


def test_export_helpers_window_and_plot_edges(tmp_path):
    from dataclasses import dataclass
    import pandas as pd

    from openpine.export._utils import first, int_or_none, object_dict
    from openpine.export.batch import export_strategy_result
    from openpine.export.plots import export_plot_outputs, export_plot_records
    from openpine.export.window import ExportWindow, parse_time_ms

    @dataclass
    class D:
        x: int

    class Obj:
        def __init__(self):
            self.y = 2

    assert object_dict(D(1)) == {"x": 1}
    assert object_dict(Obj()) == {"y": 2}
    assert object_dict({"z": 3}) == {"z": 3}
    assert object_dict(5) == {}
    assert first({"a": None, "b": 2}, "a", "b") == 2
    assert int_or_none("") is None
    assert int_or_none("7") == 7
    assert parse_time_ms(None) is None
    assert parse_time_ms("") is None
    assert parse_time_ms("1000") == 1_000_000
    assert parse_time_ms("1700000000000") == 1700000000000
    assert isinstance(parse_time_ms("2024-01-01T00:00:00Z"), int)
    window = ExportWindow(0, 2)
    assert window.contains(1) and not window.contains(None) and not window.contains(2)

    empty_source = tmp_path / "empty.csv"
    pd.DataFrame(columns=["bar_time", "bar_index", "value", "title"]).to_csv(empty_source, index=False)
    assert export_plot_outputs(empty_source, tmp_path / "wide_empty.csv") == 0

    records = [(0, 0, 1.0, "A"), (1, 1, SimpleNamespace(_current=2.0), "B"), (2, 2, 3.0, "A")]
    assert export_plot_records(records, tmp_path / "wide.csv", from_ms=0, to_ms=2) == 2
    assert (tmp_path / "wide.csv").read_text(encoding="utf-8").splitlines()[0].startswith("bar_time")

    class PlotRecords:
        def get_records(self):
            return records

    result = SimpleNamespace(plots=PlotRecords(), trades=[], equity_curve=[])
    summary = export_strategy_result(result=result, window=window, output_dir=tmp_path / "out")
    assert summary.plots_rows == 2
    assert set(summary.outputs) == {"plots", "trades", "all_trades", "equity_curve"}

    empty_result = SimpleNamespace(plots=object(), trades=None, equity_curve=None)
    empty_summary = export_strategy_result(result=empty_result, window=window, output_dir=tmp_path / "out2")
    assert empty_summary.plots_rows == 0
