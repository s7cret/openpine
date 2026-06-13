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
from openpine.compile import adapter as compile_adapter

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


def test_compile_adapter_pure_edges(monkeypatch, tmp_path: Path):
    assert compile_adapter._is_visual_contract_diagnostic(
        "P2A1507 Not lowerable under runtime_contract: Builtin plot has no runtime-equivalent"
    )
    assert not compile_adapter._is_visual_contract_diagnostic("P2A1507 Builtin label.new")
    assert compile_adapter._unsupported_request_in_source_error("request.financial(s, f)") == (
        "unsupported request call is not production lowerable: request.financial"
    )
    assert compile_adapter._unsupported_request_in_source_error("request.security(s, t, close)") is None

    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(compile_adapter.shutil, "which", lambda name: str(tool) if name == "pine2ast" else None)
    assert compile_adapter._find_tool("pine2ast") == tool
    monkeypatch.setattr(compile_adapter, "TOOL_SEARCH_PATHS", [tmp_path])
    assert compile_adapter._find_tool("tool") == tool
    assert compile_adapter._find_tool("missing") is None

    assert compile_adapter._version_from_module(SimpleNamespace(__version__="1"), "__version__") == "1"
    assert compile_adapter._version_from_module(SimpleNamespace(), "missing") == "unknown"
    diag = SimpleNamespace(code="P2A", severity=SimpleNamespace(value="error"), message="bad")
    assert compile_adapter._diagnostic_message(diag) == "error: P2A: bad"
    normalized, changed = compile_adapter._normalize_pine_v5_directive("//@version=5\nplot(close)\n")
    assert changed and "version=6" in normalized
    assert compile_adapter._normalize_pine_v5_directive("plot(close)") == ("plot(close)", False)
    assert compile_adapter._is_pine_v5_version_rejection(["P2A0103 Pine version 5"])
    assert not compile_adapter._is_pine_v5_version_rejection(["P2A0103", "P2A9999"])
    blockers = compile_adapter._production_metadata_blockers(
        {
            "codegen_safe": False,
            "runtime_contract_safe": False,
            "parity_safe": False,
            "unsafe": True,
            "unsupported_features": ["x"],
            "unsupported_nodes": ["y"],
            "unsupported_declaration_args": ["z"],
            "import_aliases": {"lib": "x"},
            "compile_profile": "debug",
        }
    )
    assert len(blockers) >= 8
    assert compile_adapter._unsupported_request_error(Exception("'request.earnings'")) == (
        "unsupported request call is not production lowerable: request.earnings"
    )
    cp = subprocess.CompletedProcess(["pine2ast"], 2, stdout="out", stderr="err")
    assert compile_adapter._pine2ast_subprocess_errors(cp)[0].startswith("pine2ast failed")
    meta = compile_adapter._subprocess_compile_meta(
        profile=compile_adapter.CompileProfile.production(),
        module_name="m",
        strict=True,
        pine2ast_path=Path("p2a"),
        ast2python_path=Path("a2p"),
        adapter_status="selected",
    )
    compile_adapter._mark_compile_meta_unsafe(meta, "reason")
    compile_adapter._mark_compile_meta_unsafe(meta, "reason")
    assert meta["unsafe_reasons"] == ["reason"]
    with pytest.raises(ValueError):
        compile_adapter._import_local_module("bad")


def test_compile_adapter_subprocess_and_library_edges(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(compile_adapter, "_find_tool", lambda name: tmp_path / name if name in {"pine2ast", "ast2python"} else None)
    tools, errors = compile_adapter._resolve_subprocess_tools()
    assert tools is not None and not errors
    src = compile_adapter._write_temp_pine_source("//@version=6")
    assert src.exists()
    src.unlink()

    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):
        calls.append(list(map(str, cmd)))
        if cmd[1] == "parse" and len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="P2A0103 Pine version 5")
        if cmd[1] == "parse":
            return subprocess.CompletedProcess(cmd, 0, stdout='{"kind":"Program"}', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="# code\n", stderr="")
    monkeypatch.setattr(compile_adapter.subprocess, "run", fake_run)
    path = tmp_path / "src.pine"; path.write_text("//@version=5\n", encoding="utf-8")
    meta: dict[str, object] = {}
    ast_json, err = compile_adapter._subprocess_ast_json_or_error(
        pine2ast_path=tmp_path / "pine2ast",
        src_path=path,
        source_text=path.read_text(),
        profile=compile_adapter.CompileProfile("diagnostic", False, False, False, allow_implicit_version_rewrite=True, allow_subprocess_fallback=True),
        timeout=1,
        compile_meta=meta,
    )
    assert err is None and ast_json == '{"kind":"Program"}' and meta["unsafe"] is True
    code, translate_err, ast_path = compile_adapter._translate_ast_with_subprocess(
        ast2python_path=tmp_path / "ast2python", ast_json=ast_json, module_name="m", strict=True, timeout=1
    )
    assert code == "# code\n" and translate_err is None
    ast_path.unlink(missing_ok=True)

    class APIs:
        def __init__(self, parse_result):
            self.parse_result = parse_result
            self.parse_calls = []
        def parse_code(self, text, options):
            self.parse_calls.append(text)
            return self.parse_result(text)
        def parse_options(self, **kwargs):
            return object()
        def ast_to_json(self, ast):
            return json.dumps({"kind": "Program"})
        def translate_ast(self, payload, **kwargs):
            return SimpleNamespace(code="# generated", metadata={"compile_profile": "production"}, source_map=[1, 2])
    ok_parse = lambda text: SimpleNamespace(ast={"ok": True}, ok=True, diagnostics=[])
    result = compile_adapter._translate_ast_with_library_api(
        apis=APIs(ok_parse), ast={"ok": True}, module_name="m", strict=False,
        profile=compile_adapter.CompileProfile.production(), compile_meta={}, kwargs={},
    )
    assert result.success and result.python_code == "# generated"
    apis = APIs(lambda text: SimpleNamespace(ast=None, ok=False, diagnostics=[SimpleNamespace(code="P2A0103", severity=SimpleNamespace(value="error"), message="Pine version 5")]))
    ast, parse_error = compile_adapter._parse_with_library_api(
        apis=apis, source_text="//@version=5\n", options=object(), profile=compile_adapter.CompileProfile.production(), compile_meta={},
    )
    assert ast is None and parse_error is not None
    visual = APIs(lambda text: SimpleNamespace(ast={"ok": True}, ok=False, diagnostics=[SimpleNamespace(code="P2A1507", severity=SimpleNamespace(value="error"), message="Builtin plot not lowerable under runtime_contract")]))
    ast, parse_error = compile_adapter._parse_with_library_api(
        apis=visual, source_text="//@version=6\n", options=object(), profile=compile_adapter.CompileProfile.production(), compile_meta={},
    )
    assert ast is not None and parse_error is None


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
