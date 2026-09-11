"""Real locked compilation/persistence/readback plus original-source diagnostics.

No network library resolver, storage stubs or disabling of artifact verification.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from pine2ast.libraries import LibraryError, LibraryStore
from pine2ast.libraries.store import canonical, source_hash
from openpine.artifacts.store import ArtifactStore
from openpine.compile import NativeRC6CompilerAdapter
from openpine.compile.library_inputs import admit_library_payload, load_library_lock
from openpine.compile.pipeline import compile_pipeline
from openpine.pine.source import PineSource

COMMITS = {"pine2ast": "a" * 40, "ast2python": "b" * 40}


def sources(v=6, delta=1):
    return {
        "qa/Base/1": f'//@version={v}\nlibrary("Base")\nexport step(float x)=>x+{delta}\n',
        "qa/Public/1": f'//@version={v}\nlibrary("Public")\nimport qa/Base/1 as b\nexport f(float x)=>b.step(x)\n',
    }


def root(v=6):
    return PineSource(
        id="consumer_test",
        name="consumer",
        source_path="consumer.pine",
        source_text=f'//@version={v}\nstrategy("T")\nimport qa/Public/1 as lib\nx=lib.f(close)\n',
    )


def payload(v=6, delta=1):
    libs = sources(v, delta)
    store = LibraryStore.create(libs)
    return {"lock": store.lock(), "sources": libs, "expected_lock_hash": store.content_hash}


def write_project(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    for row in data["lock"]["libraries"]:
        p = folder / row["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data["sources"][row["ref"]], encoding="utf-8")
    path = folder / "pine.lock.json"
    path.write_text(json.dumps(data["lock"]), encoding="utf-8")
    return path


def compile_saved(tmp_path, data=None, v=6, src=None):
    store = ArtifactStore(tmp_path)
    result = compile_pipeline(
        src or root(v),
        NativeRC6CompilerAdapter(),
        extra_options={
            "producer_commits": COMMITS,
            "library_payload": payload(v) if data is None else data,
        },
        artifact_store=store,
    )
    return result, store


@pytest.mark.parametrize("v", [5, 6])
@pytest.mark.parametrize("mode", ["inline", "directory", "store"])
def test_standard_pipeline_roundtrips_locked_artifact_without_original_files(tmp_path, v, mode):
    data = payload(v)
    folder = tmp_path / "project"
    path = write_project(folder, data)
    kwargs = (
        {"library_payload": data}
        if mode == "inline"
        else {
            "library_store": load_library_lock(path, data["expected_lock_hash"])
            if mode == "directory"
            else admit_library_payload(data)
        }
    )
    # All compilation inputs are captured. Rename files before compiling.
    folder.rename(tmp_path / "moved")
    store = ArtifactStore(tmp_path / "artifacts")
    source = root(v)
    result = compile_pipeline(
        source,
        NativeRC6CompilerAdapter(),
        extra_options={"producer_commits": COMMITS, **kwargs},
        artifact_store=store,
    )
    assert result["success"], result["errors"]
    loaded = store.get_artifact(result["artifact_id"], source.id)
    assert loaded["source_text"] == source.source_text
    assert (
        loaded["consumer_bundle"]["source"]["source_hash"]
        == loaded["generated_artifact"]["source_hash"]
    )
    assert (
        loaded["source_text"]
        != loaded["compile_meta"]["library_linkage"]["sources"]["qa/Public/1"]["raw_text"]
    )
    projection = loaded["compile_meta"]["original_source_projection"]
    assert projection["source_map_hash"] == loaded["source_map"]["content_hash"]
    assert {r["location"]["source"] for r in projection["entries"] if r["location"]} >= {
        "consumer.pine",
        "qa/Base/1",
    }
    assert (
        store.get_artifact(result["artifact_id"], source.id)["generated_artifact"]
        == loaded["generated_artifact"]
    )


@pytest.mark.parametrize(
    "which", ["source", "library_raw", "library_receipt", "dependencies", "projection"]
)
def test_persisted_library_artifact_rejects_tampering(tmp_path, which):
    result, store = compile_saved(tmp_path)
    assert result["success"], result["errors"]
    folder = Path(result["artifact_path"])
    if which == "source":
        (folder / "source.pine").write_text(root().source_text + "// changed\n")
    elif which in {"library_raw", "library_receipt", "projection"}:
        p = folder / "compile_meta.json"
        data = json.loads(p.read_text())
        if which == "library_raw":
            data["library_linkage"]["sources"]["qa/Base/1"]["raw_text"] += "// changed\n"
        if which == "library_receipt":
            data["library_linkage"]["content_hash"] = "sha256:" + "f" * 64
        if which == "projection":
            side = data["original_source_projection"]
            next(r for r in side["entries"] if r["location"])["location"]["line"] = 999
            side["content_hash"] = source_hash(
                canonical({k: v for k, v in side.items() if k != "content_hash"})
            )
        p.write_text(json.dumps(data))
    else:
        p = folder / "generated_artifact.json"
        data = json.loads(p.read_text())
        data["external_library_dependency_hashes"]["qa/Base/1"] = "sha256:" + "e" * 64
        p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        store.get_artifact(result["artifact_id"], "consumer_test")


@pytest.mark.parametrize("v", [5, 6])
def test_unused_store_sources_do_not_invalidate_success_but_reachable_change_does(tmp_path, v):
    d = payload(v)
    a, store = compile_saved(tmp_path / "artifacts", d, v)
    unused = sources(v)
    unused["qa/Unused/1"] = f'//@version={v}\nlibrary("Unused")\nexport f()=>1\n'
    s = LibraryStore.create(unused)
    b, _ = compile_saved(
        tmp_path / "artifacts",
        {"lock": s.lock(), "sources": unused, "expected_lock_hash": s.content_hash},
        v,
    )
    c, _ = compile_saved(tmp_path / "artifacts", payload(v, delta=2), v)
    assert a["artifact_id"] == b["artifact_id"]
    assert a["artifact_id"] != c["artifact_id"]
    assert len(store.list_artifacts(root().id)) == 2


@pytest.mark.parametrize("v", [5, 6])
@pytest.mark.parametrize("action", ["compile", "validate"])
def test_structured_diagnostic_projects_library_type_error_to_original_file(v, action):
    d = payload(v)
    d["sources"]["qa/Base/1"] = (
        f'//@version={v}\nlibrary("Base")\n// retained line\nexport step(float x)=>x + "wrong"\n'
    )
    s = LibraryStore.create(d["sources"])
    result = getattr(NativeRC6CompilerAdapter(), action)(
        root(v).source_text, source_name="consumer.pine", producer_commits=COMMITS, library_store=s
    )
    assert not result.success
    locations = [row["location"] for row in result.diagnostics if row["location"]]
    assert any(loc["source"] == "qa/Base/1" and loc["line"] == 4 for loc in locations), (
        result.diagnostics
    )
    assert any("qa/Base/1:4" in msg for msg in result.errors)


@pytest.mark.parametrize("action", ["compile", "validate"])
@pytest.mark.parametrize("bad", ["missing", "syntax", "private", "version"])
def test_invalid_library_is_a_structured_failure_before_emission(action, bad):
    d = payload()
    src = root().source_text
    if bad == "syntax":
        d["sources"]["qa/Base/1"] = '//@version=6\nlibrary("Base")\nexport step(float x)=>)\n'
    elif bad == "private":
        d["sources"]["qa/Public/1"] = d["sources"]["qa/Public/1"].replace("export ", "")
    elif bad == "version":
        d["sources"]["qa/Base/1"] = d["sources"]["qa/Base/1"].replace("version=6", "version=5")
    else:
        src = src.replace("Public/1", "Public/2")
    s = LibraryStore.create(d["sources"])
    r = getattr(NativeRC6CompilerAdapter(), action)(
        src, source_name="root.pine", producer_commits=COMMITS, library_store=s
    )
    assert not r.success and r.python_code is None
    assert r.diagnostics and r.diagnostics[0]["code"].startswith("P2A_LIBRARY_")


@pytest.mark.parametrize(
    "bad",
    ["hash", "source", "extra", "path", "missing", "schema", "duplicate", "no_hash", "no_sources"],
)
def test_inline_inputs_reject_invalid_admission(bad):
    d = payload()
    if bad == "hash":
        d["expected_lock_hash"] = "sha256:" + "f" * 64
    elif bad == "source":
        d["sources"]["qa/Base/1"] += "//changed"
    elif bad == "extra":
        d["path"] = "/etc/passwd"
    elif bad == "path":
        d["lock"]["libraries"][0]["path"] = "../bad.pine"
    elif bad == "missing":
        d["sources"].pop("qa/Base/1")
    elif bad == "schema":
        d["lock"]["schema_id"] = "wrong"
    elif bad == "duplicate":
        d["lock"]["libraries"].append(deepcopy(d["lock"]["libraries"][0]))
    elif bad == "no_hash":
        d.pop("expected_lock_hash")
    else:
        d.pop("sources")
    with pytest.raises(LibraryError):
        admit_library_payload(d)


@pytest.mark.parametrize("pair", [(None, "sha256:" + "a" * 64), ("missing.json", None)])
def test_local_lock_and_hash_must_be_supplied_together(pair):
    with pytest.raises(LibraryError, match="required together"):
        load_library_lock(*pair)


def test_captured_inline_input_is_detached_from_caller_mutations():
    d = payload()
    s = admit_library_payload(d)
    d["sources"].clear()
    d["lock"]["libraries"].clear()
    assert s.source("qa/Base/1") == sources()["qa/Base/1"]


@pytest.mark.parametrize("bad", ["lock", "directory", "source"])
def test_cli_reader_rejects_symlinks(tmp_path, bad):
    d = payload()
    p = write_project(tmp_path / "original", d)
    if bad == "lock":
        (tmp_path / "link.json").symlink_to(p)
        p = tmp_path / "link.json"
    elif bad == "directory":
        (tmp_path / "link").symlink_to(p.parent, target_is_directory=True)
        p = tmp_path / "link" / p.name
    else:
        q = p.parent / d["lock"]["libraries"][0]["path"]
        target = tmp_path / "outside"
        q.rename(target)
        q.symlink_to(target)
    with pytest.raises(LibraryError):
        load_library_lock(p, d["expected_lock_hash"])


def test_validate_compile_share_exact_bundle_and_no_python_in_validate():
    data = payload()
    adapter = NativeRC6CompilerAdapter()
    options = {"source_name": "source.pine", "producer_commits": COMMITS, "library_payload": data}
    a = adapter.validate(root().source_text, **options)
    b = adapter.compile(root().source_text, **options)
    assert a.success and b.success, (a.errors, b.errors)
    assert a.consumer_bundle == b.consumer_bundle
    assert a.python_code is None and a.generated_artifact is None


def test_missing_lock_error_is_actionable_and_never_resolves_files():
    r = NativeRC6CompilerAdapter().compile(root().source_text, producer_commits=COMMITS)
    assert not r.success and r.diagnostics[0]["code"] == "P2A_LIBRARY_REQUIRED"


def test_failed_artifact_identity_changes_with_source_and_dependencies(tmp_path):
    source = root()
    source.source_text += "bad=undeclared\n"
    a, store = compile_saved(tmp_path / "artifacts", payload(), src=source)
    b, _ = compile_saved(tmp_path / "artifacts", payload(delta=2), src=source)
    source.source_text += "// changed\n"
    c, _ = compile_saved(tmp_path / "artifacts", payload(), src=source)
    assert not a["success"] and not b["success"] and not c["success"]
    assert len({a["artifact_id"], b["artifact_id"], c["artifact_id"]}) == 3
    for r in (a, b, c):
        saved = store.get_artifact(r["artifact_id"], source.id)
        assert saved["compile_meta"]["diagnostics"] == r["diagnostics"]
        assert saved["python_code"] == ""


@pytest.mark.parametrize("v", [5, 6])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_stored_artifact_runs_real_broker_logic_and_retains_checkpoint(
    monkeypatch, tmp_path, v, on_close, recalc
):
    from rc6_tests.test_rc6_library_imports import prepare
    from rc6_tests.test_rc6_deferred_exits import compare_modes
    from rc6_tests.test_rc6_lifecycle import make_session
    from openpine.compile.native_rc6 import CompileResult
    from openpine.compile.pipeline import persist_compile_result

    case, rows = prepare(v, on_close, recalc)
    receipt = case[0].compile_meta["library_linkage"]
    name = receipt["root_source_name"]
    source = PineSource(
        id="stored_broker", name="saved", source_text=receipt["sources"][name]["raw_text"]
    )
    store = ArtifactStore(tmp_path / "artifacts")
    saved = persist_compile_result(source, case[0], artifact_store=store)
    loaded = store.get_artifact(saved["artifact_id"], source.id)
    restored = CompileResult(
        success=True,
        **{
            k: loaded[k]
            for k in (
                "python_code",
                "ast_json",
                "source_map",
                "generated_artifact",
                "consumer_bundle",
                "compile_meta",
                "frontend_artifact",
                "support_profile",
                "ast_artifact",
            )
        },
    )
    recovered = (restored, case[1], case[2])
    (tmp_path / "execution").mkdir()
    out, tape = compare_modes(monkeypatch, tmp_path / "execution", recovered, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("library", 2, 33)
    ]
    assert [(t.entry_price, t.qty) for t in out.open_trades] == [(102 if on_close else 103, 33)]

    # Checkpoint of the same generated session resumes after a storage round trip.
    def advance(session, candles, start=0, stop=None):
        from pinelib.runtime.metadata import BarValues
        from openpine_contracts import ExecutionEvent

        tape = []
        for i in range(start, len(candles) if stop is None else stop):
            row = candles[i]
            values = BarValues(
                **{k: float(row[k]) for k in ("open", "high", "low", "close", "volume")},
                time=row["open_time_utc_ms"],
                time_close=row["close_time_utc_ms"],
            )
            event = ExecutionEvent(
                i,
                i,
                len(candles) - 1,
                len(candles) - 1,
                values.time,
                "HISTORICAL_EVAL",
                False,
                True,
                0,
                0,
                "BAR_CLOSE",
            )
            # A standalone generated session has no broker. Supply the explicit
            # position projection for this bounded checkpoint test; actual fills
            # above are exercised through the unchanged real broker.
            tape.extend(
                session.execute_callback(
                    values,
                    event,
                    strategy_values={"strategy.position_size": 0.0 if i <= 2 else 33.0},
                ).intents
            )
            session.finalize_bar(i)
        return tape

    before = make_session(case)
    advance(before, rows, stop=1)
    checkpoint = json.loads(json.dumps(before.export_state()))
    after = make_session(recovered)
    after.restore_state(checkpoint)
    assert advance(before, rows, start=1) == advance(after, rows, start=1)
    assert before.export_state() == after.export_state()


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_worker_runs_stored_linked_artifact(tmp_path, mode, on_close):
    from rc6_tests.test_rc6_library_imports import prepare
    from rc6_tests.test_rc6_worker_admission import _manifest
    from openpine.compile.pipeline import persist_compile_result
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar

    case, rows = prepare(on_close=on_close, recalc=True)
    compiled, context, cfg = case
    receipt = compiled.compile_meta["library_linkage"]
    source_text = receipt["sources"][receipt["root_source_name"]]["raw_text"]
    store = ArtifactStore(tmp_path / "artifacts")
    result = persist_compile_result(
        PineSource(id="real", name="real", source_text=source_text), compiled, artifact_store=store
    )
    saved = store.get_artifact(result["artifact_id"], "real")
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=saved["generated_artifact"],
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(cfg, name, value)
    result = run_isolated_artifact(
        saved["python_code"].encode(), bars=[_engine_bar(b) for b in rows], config=cfg, params={}
    )
    assert result["ok"] and result["bars_processed"] == 6
    assert [(e["command_id"], float(e["qty"])) for e in result["intent_tape"]] == [("library", 33)]
    assert [(t.entry_price, t.qty) for t in result["raw_result"].open_trades] == [
        (102 if on_close else 103, 33)
    ]


@pytest.mark.parametrize("version", [5, 6])
def test_compiler_failure_projects_exact_node_to_original_library(monkeypatch, version):
    import openpine.compile.native_rc6 as module
    from ast2python.errors import BundleInvariantError

    original_compile = module.compile_consumer_bundle
    seen = {}

    def fail_target_at_library_node(bundle, **kwargs):
        linked = kwargs["linked_source"]
        for node in bundle["node_index"]:
            span = node.get("span")
            loc = linked.original_location(span["start_offset"]) if span else None
            if (
                loc
                and loc["source"] == "qa/Base/1"
                and loc["line"] == 3
                and node["kind"] == "BinaryExpr"
            ):
                seen["location"] = loc
                raise BundleInvariantError(
                    "A2P_TARGET_OPERATION_MISSING",
                    "target operation unavailable",
                    details={"node_id": node["node_id"]},
                )
        # Actual node kinds are checked below, not silently passed as success.
        raise AssertionError([(n["kind"], n.get("span")) for n in bundle["node_index"]])

    monkeypatch.setattr(module, "compile_consumer_bundle", fail_target_at_library_node)
    result = NativeRC6CompilerAdapter().compile(
        root(version).source_text,
        source_name="consumer.pine",
        producer_commits=COMMITS,
        library_payload=payload(version),
    )
    assert not result.success and result.python_code is None
    assert result.diagnostics[0]["code"] == "A2P_TARGET_OPERATION_MISSING"
    assert result.diagnostics[0]["phase"] == "compiler"
    assert result.diagnostics[0]["location"] == seen["location"]
    monkeypatch.setattr(module, "compile_consumer_bundle", original_compile)
    assert (
        NativeRC6CompilerAdapter()
        .compile(
            root(version).source_text, producer_commits=COMMITS, library_payload=payload(version)
        )
        .success
    )


def test_unknown_compiler_node_does_not_invent_a_source_location():
    from ast2python.errors import BundleInvariantError
    from openpine.compile.diagnostics import exception_diagnostics
    from openpine.compile.source_context import PreparedSource

    exc = BundleInvariantError(
        "A2P_TARGET_CALL_BINDING", "bad binding", details={"node_id": "unknown"}
    )
    diag = exception_diagnostics(
        exc, PreparedSource("", "root"), phase="compiler", bundle={"node_index": []}
    )
    assert diag[0]["location"] is None


@pytest.mark.parametrize("suffix", ["//first", "//second"])
def test_failed_link_preserves_requested_lock_identity(tmp_path, suffix):
    from openpine.compile.pipeline import persist_compile_result

    ids = []
    for marker in ("", suffix):
        libs = sources()
        libs["qa/Base/1"] = '//@version=6\nlibrary("Base")\nexport step(float x)=>)\n' + marker
        store = LibraryStore.create(libs)
        result = NativeRC6CompilerAdapter().compile(
            root().source_text, producer_commits=COMMITS, library_store=store
        )
        assert not result.success
        assert result.compile_meta["requested_library_lock_hash"] == store.content_hash
        assert "library_linkage" not in result.compile_meta
        saved = persist_compile_result(root(), result, artifact_store=ArtifactStore(tmp_path))
        ids.append(saved["artifact_id"])
    assert ids[0] != ids[1]
