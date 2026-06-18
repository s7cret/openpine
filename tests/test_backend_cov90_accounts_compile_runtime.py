from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace


from ast2python.profiles import CompileProfile
from openpine.compile import adapter as ca
from openpine.gateway.routes import accounts_data as ad
from openpine.jobs import JobStatus


def test_accounts_data_coverage_and_backfill_paths(monkeypatch, tmp_path):
    state = SimpleNamespace(config=SimpleNamespace(data_cache_root=None, data_dir=tmp_path), scheduler=None)

    class Store:
        def coverage(self, **kwargs):
            return [
                {"timeframe": "1m", "earliest_ms": 1, "latest_ms": 2, "bar_count": 3, "gaps": [{"a": 1}]},
                {"timeframe": "5m", "bar_count": 0},
            ]

    import marketdata_provider

    monkeypatch.setattr(marketdata_provider, "create_candle_store", lambda config: Store())
    rows = asyncio.run(ad.data_coverage("BTCUSDT", state=state))
    assert [row.timeframe for row in rows] == ["1m", "5m"]

    monkeypatch.setattr(marketdata_provider, "create_candle_store", lambda config: (_ for _ in ()).throw(RuntimeError("bad")))
    assert asyncio.run(ad.data_coverage("BTCUSDT", state=state)) == []

    events: list[tuple] = []
    monkeypatch.setattr(ad.ws_manager, "update_progress", lambda *a, **k: events.append((a, k)))
    async def _broadcast(*a, **k):
        return None
    monkeypatch.setattr(ad.ws_manager, "broadcast_progress", _broadcast)

    class Scheduler:
        def __init__(self, job):
            self.job = job
            self.done = None
            self.failed = None
            self.running = False

        def get_job(self, job_id):
            return self.job

        def mark_running(self, job_id):
            self.running = True
            self.job.status = JobStatus.RUNNING

        def mark_done(self, job_id, result):
            self.done = result

        def mark_failed(self, job_id, error):
            self.failed = error

    job = SimpleNamespace(status=JobStatus.PENDING)
    scheduler = Scheduler(job)
    state = SimpleNamespace(scheduler=scheduler)
    monkeypatch.setattr(ad, "_run_data_backfill_sync", lambda payload, state, cb: (cb(5, 1, 10, 2, None, "fetch") or cb(10, 2, 10, 2, None, "write") or {"bars_loaded": 10, "skipped_existing": 2}))
    asyncio.run(ad._run_data_backfill_job("job1", {"symbol": "BTCUSDT"}, state))
    assert scheduler.done["bars_loaded"] == 10
    assert events

    job_iso = SimpleNamespace(status=JobStatus.PENDING)
    scheduler_iso = Scheduler(job_iso)
    state_iso = SimpleNamespace(scheduler=scheduler_iso)
    subprocess_payloads = []
    monkeypatch.setattr(
        ad,
        "_run_data_backfill_subprocess",
        lambda payload: subprocess_payloads.append(payload)
        or {"bars_loaded": 7, "skipped_existing": 0, "execution_mode": "isolated_process"},
    )
    asyncio.run(
        ad._run_data_backfill_job(
            "job-iso",
            {"symbol": "SOLUSDT", "estimated_source_bars": 250_001},
            state_iso,
        )
    )
    assert scheduler_iso.done["bars_loaded"] == 7
    assert scheduler_iso.done["execution_mode"] == "isolated_process"
    assert subprocess_payloads

    job2 = SimpleNamespace(status=JobStatus.PENDING)
    scheduler2 = Scheduler(job2)
    state2 = SimpleNamespace(scheduler=scheduler2)
    monkeypatch.setattr(ad, "_run_data_backfill_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    asyncio.run(ad._run_data_backfill_job("job2", {"symbol": "ETHUSDT"}, state2))
    assert scheduler2.failed == "boom"

    assert asyncio.run(ad._run_data_backfill_job("missing", {}, SimpleNamespace(scheduler=SimpleNamespace(get_job=lambda job_id: None)))) is None


def test_compile_adapter_subprocess_and_library_edges(monkeypatch, tmp_path):
    # Missing API attributes and missing tools.
    class FakeModule:
        __version__ = "1"
        RUNTIME_CONTRACT_VERSION = "r"
        PACKAGE_VERSION = "p"

    modules = {name: FakeModule() for name in ca.COMPILER_PACKAGES}
    modules["pine2ast.api"] = SimpleNamespace(parse_code=lambda *a, **k: None)

    def fake_import(name):
        return modules[name]

    monkeypatch.setattr(ca, "_import_local_module", fake_import)
    monkeypatch.setattr(ca.importlib, "import_module", lambda name: modules[name])
    apis, status = ca._load_library_apis()
    assert apis is None
    assert status.errors

    monkeypatch.setattr(ca, "_find_tool", lambda name: None)
    tools, errors = ca._resolve_subprocess_tools()
    assert tools is None and len(errors) == 2

    # Subprocess parser: success, invalid JSON, non-v5 rejection, v5 rewrite success/fail.
    src = tmp_path / "s.pine"
    src.write_text("//@version=5\nplot(close)")
    profile = CompileProfile.diagnostic(allow_implicit_version_rewrite=True, allow_subprocess_fallback=True)
    meta: dict = {}

    class CP(SimpleNamespace):
        pass

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: CP(returncode=0, stdout='{"type":"Program"}', stderr=""))
    result, err = ca._parse_with_pine2ast_subprocess(pine2ast_path=Path("pine2ast"), src_path=src, source_text=src.read_text(), profile=profile, timeout=1, compile_meta=meta)
    assert err is None and result.stdout.startswith("{")
    ast_json, ast_err = ca._subprocess_ast_json_or_error(pine2ast_path=Path("pine2ast"), src_path=src, source_text=src.read_text(), profile=profile, timeout=1, compile_meta={})
    assert ast_json

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: CP(returncode=0, stdout="not json", stderr=""))
    assert ca._subprocess_ast_json_or_error(pine2ast_path=Path("p"), src_path=src, source_text="x", profile=profile, timeout=1, compile_meta={})[1].errors[0].startswith("pine2ast produced invalid")

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: CP(returncode=2, stdout="", stderr="P2A9999 bad"))
    assert ca._parse_with_pine2ast_subprocess(pine2ast_path=Path("p"), src_path=src, source_text="//@version=6\nx", profile=profile, timeout=1, compile_meta={})[1].errors

    calls = {"n": 0}

    def v5_then_ok(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return CP(returncode=1, stdout="", stderr="P2A0103 Pine version 5")
        return CP(returncode=0, stdout='{"type":"Program"}', stderr="")

    monkeypatch.setattr(subprocess, "run", v5_then_ok)
    meta = {}
    result, err = ca._parse_with_pine2ast_subprocess(pine2ast_path=Path("p"), src_path=src, source_text="//@version=5\nx", profile=profile, timeout=1, compile_meta=meta)
    assert err is None and meta["compatibility_fallback"]["pine_version_from"] == 5

    no_rewrite = CompileProfile.production()
    calls["n"] = 0
    monkeypatch.setattr(subprocess, "run", v5_then_ok)
    assert ca._parse_with_pine2ast_subprocess(pine2ast_path=Path("p"), src_path=src, source_text="//@version=5\nx", profile=no_rewrite, timeout=1, compile_meta={})[1].errors

    def ast2_ok(cmd, **kwargs):
        return CP(returncode=0, stdout="PY", stderr="")

    monkeypatch.setattr(subprocess, "run", ast2_ok)
    py, err, ast_path = ca._translate_ast_with_subprocess(ast2python_path=Path("ast2python"), ast_json='{"x":1}', module_name="m", strict=True, timeout=1)
    assert py == "PY" and err is None and ast_path.exists()

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: CP(returncode=1, stdout="out", stderr="err"))
    assert ca._translate_ast_with_subprocess(ast2python_path=Path("a"), ast_json="{}", module_name="m", strict=False, timeout=1)[1].errors[0].startswith("ast2python failed")


def test_compile_adapter_library_api_error_paths(monkeypatch):
    profile = CompileProfile.diagnostic(allow_invalid_ast=True, allow_unsupported_request_stubs=True, allow_external_library_stubs=True)
    adapter = ca.SubprocessCompilerAdapter(prefer_library=True, fallback_to_subprocess=False)
    assert not adapter.compile("x", profile="missing").success
    assert not adapter.compile("x", allow_unsupported_request_stubs=True).success

    class ParseResult:
        ast = {"type": "Program"}
        ok = False
        diagnostics = [SimpleNamespace(severity=SimpleNamespace(value="error"), code="P2A1507", message="Builtin plot has no runtime-equivalent visual output under runtime_contract v1.4")]

    apis = ca._LibraryApis(
        parse_code=lambda src, opts: ParseResult(),
        parse_options=lambda **kw: SimpleNamespace(),
        ast_to_json=lambda ast: json.dumps(ast),
        translate_ast=lambda ast, **kw: SimpleNamespace(success=True, code="print(1)", metadata={"compile_profile": "diagnostic"}),
        versions={"pine2ast_version": "4", "ast2python_version": "4"},
    )
    res = adapter._compile_with_library(apis, "plot(close)", profile=profile, module_name="m")
    assert res.success and res.compile_meta["filtered_visual_diagnostics"]

    seen_payloads = []

    def translate_requires_passed_visual_gates(ast, **kw):
        seen_payloads.append(ast)
        producer = ast.get("producer_metadata") or {}
        if producer.get("parser_gate") != "pass" or producer.get("semantic_gate") != "pass":
            raise RuntimeError("Pine2AST producer metadata gates are not pass")
        return SimpleNamespace(success=True, code="print(2)", metadata={"compile_profile": "production"})

    visual_gate_apis = ca._LibraryApis(
        parse_code=lambda src, opts: ParseResult(),
        parse_options=lambda **kw: SimpleNamespace(),
        ast_to_json=lambda ast: json.dumps(
            {
                "type": "Program",
                "producer_metadata": {
                    "contract": "pine.ast_contract.v1",
                    "runtime_contract": "1.4",
                    "runtime_contract_profile": "runtime_contract_v1_4",
                    "parser_gate": "fail",
                    "semantic_gate": "fail",
                },
            }
        ),
        translate_ast=translate_requires_passed_visual_gates,
        versions={"pine2ast_version": "4", "ast2python_version": "4"},
    )
    visual_gate_res = adapter._compile_with_library(
        visual_gate_apis, "plot(close)", profile=ca.CompileProfile.production(), module_name="m"
    )
    assert visual_gate_res.success
    assert seen_payloads[0]["producer_metadata"]["parser_gate"] == "pass"
    assert seen_payloads[0]["producer_metadata"]["semantic_gate"] == "pass"
    assert visual_gate_res.compile_meta["filtered_visual_diagnostics"]

    class V5VisualParseResult:
        ast = {"type": "Program"}
        ok = False
        diagnostics = [
            SimpleNamespace(severity=SimpleNamespace(value="error"), code="P2A0103", message="Pine version 5 is parsed in v6 compatibility mode."),
            SimpleNamespace(severity=SimpleNamespace(value="error"), code="P2A1507", message="Builtin color.new has no runtime-equivalent visual output under runtime_contract v1.4"),
        ]

    v5_visual_payloads = []

    def translate_v5_visual(ast, **kw):
        v5_visual_payloads.append(ast)
        assert ast.get("diagnostics") == []
        producer = ast.get("producer_metadata") or {}
        if producer.get("parser_gate") != "pass" or producer.get("semantic_gate") != "pass":
            raise RuntimeError("Pine2AST producer metadata gates are not pass")
        return SimpleNamespace(success=True, code="print(3)", metadata={"compile_profile": "production"})

    v5_visual_apis = ca._LibraryApis(
        parse_code=lambda src, opts: V5VisualParseResult(),
        parse_options=lambda **kw: SimpleNamespace(),
        ast_to_json=lambda ast: json.dumps(
            {
                "type": "Program",
                "diagnostics": [
                    {
                        "severity": "ERROR",
                        "code": "P2A0103",
                        "message": "Pine version 5 is parsed in v6 compatibility mode.",
                    },
                    {
                        "severity": "ERROR",
                        "code": "P2A1507",
                        "message": "Builtin color.new has no runtime-equivalent visual output under runtime_contract v1.4",
                    },
                ],
                "producer_metadata": {
                    "contract": "pine.ast_contract.v1",
                    "runtime_contract": "1.4",
                    "runtime_contract_profile": "runtime_contract_v1_4",
                    "parser_gate": "fail",
                    "semantic_gate": "fail",
                },
            }
        ),
        translate_ast=translate_v5_visual,
        versions={"pine2ast_version": "4", "ast2python_version": "4"},
    )
    v5_visual_res = adapter._compile_with_library(
        v5_visual_apis, "//@version=5\nplot(close)", profile=ca.CompileProfile.production(), module_name="m"
    )
    assert v5_visual_res.success
    assert v5_visual_payloads[0]["producer_metadata"]["parser_gate"] == "pass"
    assert v5_visual_res.compile_meta["filtered_compatibility_diagnostics"]
    assert v5_visual_res.compile_meta["filtered_visual_diagnostics"]

    class NoAst:
        ast = None
        ok = False
        diagnostics = []

    apis_bad = ca._LibraryApis(
        parse_code=lambda src, opts: NoAst(),
        parse_options=lambda **kw: SimpleNamespace(),
        ast_to_json=lambda ast: "{}",
        translate_ast=lambda *a, **k: None,
        versions={},
    )
    assert not adapter._compile_with_library(apis_bad, "request.financial()", profile=profile).success

    apis_exc = ca._LibraryApis(
        parse_code=lambda src, opts: (_ for _ in ()).throw(Exception("request.financial")),
        parse_options=lambda **kw: SimpleNamespace(),
        ast_to_json=lambda ast: "{}",
        translate_ast=lambda *a, **k: None,
        versions={},
    )
    assert adapter._compile_with_library(apis_exc, "x", profile=CompileProfile.production()).errors[0].startswith("unsupported request")
