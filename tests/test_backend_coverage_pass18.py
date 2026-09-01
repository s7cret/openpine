from __future__ import annotations

import argparse
import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from openpine.batch import runner as batch_runner
from openpine.batch.tv_corpus import ChartExport, ExportEntry

cli_main = importlib.import_module("openpine.cli.main")
cli_ops = importlib.import_module("openpine.cli.ops")


def _entry(tmp_path: Path, *, kind: str = "strategy") -> tuple[ExportEntry, ChartExport]:
    root = tmp_path / "entry"
    root.mkdir(parents=True)
    pine = root / "source.pine"
    pine.write_text("strategy('s')" if kind == "strategy" else "indicator('i')", encoding="utf-8")
    chart = ChartExport("15m", root / "chart.csv", 3, 1_000, 4_000)
    chart.path.write_text("time,open,high,low,close\n1,1,2,0,1\n", encoding="utf-8")
    return ExportEntry(42, "demo", kind, "group", root, pine, (chart,)), chart






def test_ops_cli_service_queue_workers_branches(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    jobs_list = [
        SimpleNamespace(id="job123456", job_type=SimpleNamespace(value="compile"), status=SimpleNamespace(value="pending"), strategy_id="s", created_at=1_700_000_000_000),
        SimpleNamespace(id="jobabcdef", job_type=SimpleNamespace(value="raw"), status=SimpleNamespace(value="failed"), strategy_id=None, created_at=1_700_000_000_000),
    ]
    class Scheduler:
        def __init__(self): self.enqueued = []
        def list_jobs(self, status=None): return jobs_list if status is None else [jobs_list[1]]
        def get_job(self, job_id): return None if job_id == "missing" else SimpleNamespace(id=job_id, job_type="type", status=__import__("openpine.jobs.models", fromlist=["JobStatus"]).JobStatus.FAILED, strategy_id="s", priority=3, idempotency_key="k", created_at=1_700_000_000_000, started_at=1_700_000_001_000, finished_at=1_700_000_002_000, error="err", result={"ok": True}, attempt=2)
        def cancel(self, job_id): self.cancelled = job_id
        def enqueue(self, job): self.enqueued.append(job); return SimpleNamespace(id="newjobid")
        def recover_stale_locks(self): return 0
    sched = Scheduler()
    monkeypatch.setattr(cli_ops, "_cli_scheduler", sched)
    for args in (["jobs", "list"], ["jobs", "show", "job1"], ["jobs", "cancel", "job1"], ["jobs", "retry", "job1"], ["jobs", "enqueue-live-bar", "--strategy", "s", "--bar-time", "123"], ["jobs", "enqueue-live-bar", "--strategy", "s", "--bar-time", "123", "--dry-run"], ["queue", "status"]):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)
    assert runner.invoke(cli_main.cli, ["jobs", "show", "missing"]).exit_code != 0
    monkeypatch.setattr(cli_ops, "_systemd_available", lambda: False)
    for cmd in ("start", "stop", "restart", "status", "logs", "enable", "disable", "install"):
        assert runner.invoke(cli_main.cli, ["service", cmd]).exit_code != 0
    monkeypatch.setattr(cli_ops, "_systemd_available", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli_ops.shutil if hasattr(cli_ops, "shutil") else __import__("shutil"), "which", lambda name: "/bin/openpine")
    assert runner.invoke(cli_main.cli, ["service", "install"]).exit_code == 0
    import subprocess as sp
    class R:
        returncode = 0; stdout = "active"; stderr = ""
    monkeypatch.setattr(sp, "run", lambda *a, **k: R())
    for cmd in ("start", "stop", "restart", "status", "logs", "enable", "disable"):
        assert runner.invoke(cli_main.cli, ["service", cmd]).exit_code == 0

    class Pool:
        def __init__(self, scheduler): self.scheduler = scheduler
        def get_status(self): return {"running": True, "max_workers": 2, "active_workers": 1, "heartbeats": {"w": 1}}
        def stop(self): self.stopped = True
        def start(self): self.started = True
    import openpine.workers as workers_mod
    monkeypatch.setattr(workers_mod, "AggregationWorkerPool", Pool)
    monkeypatch.setattr(workers_mod, "FeatureWorkerPool", Pool)
    import openpine.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "JobScheduler", lambda: sched)
    for args in (["workers", "status"], ["workers", "pause"], ["workers", "resume"]):
        result = runner.invoke(cli_main.cli, args)
        assert result.exit_code == 0, (args, result.output)


def test_batch_runner_entry_paths_and_main(monkeypatch, tmp_path: Path):
    entry, chart = _entry(tmp_path, kind="indicator")
    args = argparse.Namespace(phase="plan", timeframe=None, skip_completed=False, force_compile=False, stop_on_error=False)
    assert batch_runner.run_entry(entry, args, batch_id="b", library_revisions={"openpine": "rev"})["status"] == "planned"

    source = SimpleNamespace(id="src", active_artifact_id=None)
    monkeypatch.setattr(batch_runner, "get_or_add_source", lambda entry, write: (source, True))
    monkeypatch.setattr(batch_runner, "compile_source", lambda source, force: (None, {"status": "compile_error", "errors": ["bad"]}))
    args.phase = "compile"
    assert batch_runner.run_entry(entry, args)["status"] == "compile_error"

    monkeypatch.setattr(batch_runner, "compile_source", lambda source, force: ("art", {"status": "compiled"}))
    monkeypatch.setattr(batch_runner, "run_indicator", lambda *a, **k: {"status": "ok", "kind": "indicator", "bars": 1, "plots_rows": 1})
    args.phase = "run"
    result = batch_runner.run_entry(entry, args, batch_id="b", library_revisions={name: "rev" for name in batch_runner.LIBRARY_NAMES})
    assert result["status"] == "ok" and result["runs"][0]["plots_rows"] == 1

    bad_entry, _ = _entry(tmp_path / "bad", kind="library")
    bad_result = batch_runner._run_entry_charts(bad_entry, source, "art", args, {}, "b", {})[0]
    assert bad_result["status"] == "skipped"

    assert batch_runner.parse_ids("1,3-5,,7") == {1, 3, 4, 5, 7}
    summary = batch_runner.summarize([result])
    assert summary["stats"]["ok"] == 1
    tf_summary = batch_runner.summary_by_timeframe([result, {"charts": [{"timeframe": "1D"}], "status": "planned"}])
    assert tf_summary["15m"]["plots_rows"] == 1 and tf_summary["1D"]["selected"] == 1
    assert batch_runner._write_timeframe_summary_csv(root=tmp_path, phase="run", batch_id="b", results=[result]).exists()
    payload = batch_runner._build_batch_summary_payload(args=SimpleNamespace(phase="run", root=tmp_path, manifest=tmp_path / "m.csv", symbol="BTCUSDT", exchange="binance", market_type="spot", calculation_from="2020", calculation_to=None, _calculation_to_by_timeframe={"15m": 2}), batch_id="b", errors_path=tmp_path / "err.jsonl", library_revisions={}, selected=[entry], entries=[entry], results=[result], timeframe_summary=tf_summary)
    assert payload["selected"] == 1

    called: list[int] = []
    monkeypatch.setattr(batch_runner, "completed_for_selection", lambda entry, args: entry.export_id == 1)
    def fake_run_entry(entry, args, batch_id="", library_revisions=None):
        called.append(entry.export_id)
        if entry.export_id == 3:
            raise RuntimeError("boom")
        return {**batch_runner.entry_summary(entry), "phase": args.phase, "selected_timeframes": ["15m"], "status": "ok", "runs": []}
    monkeypatch.setattr(batch_runner, "run_entry", fake_run_entry)
    entries = []
    for idx in (1, 2, 3):
        e, _ = _entry(tmp_path / f"e{idx}")
        entries.append(ExportEntry(idx, e.folder, e.kind, e.source_group, e.root, e.pine_path, e.charts))
    args2 = argparse.Namespace(root=tmp_path, phase="run", timeframe=None, skip_completed=True, stop_on_error=True)
    results = batch_runner._run_selected_entries(args=args2, selected=entries, batch_id="b", library_revisions={}, errors_path=tmp_path / "errors.jsonl")
    assert [r["status"] for r in results] == ["skipped_completed", "ok", "fatal_error"]
