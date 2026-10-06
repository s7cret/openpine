"""Real emitted heap graphs handed to the generic extension-slot API.

Pine source and linked libraries are compiled without stubs; the admitted
GeneratedScript runs unchanged. The extension root is explicit, not claimed as
a compiler-generated Pine binding. Exact integer expectations are hand-authored.
Full/compact restore, intrabar commit/abort and deferred publication are distinct.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_executor import RC6RuntimeExecutor
from openpine.verification.execution_process import run_logged
from pine2ast.libraries import LibraryStore
from pinelib import CallbackFrame, RuntimeLanguageContext
from pinelib.errors import PineRuntimeError
from pinelib.reference.array import array_get, array_pop, array_push, array_set, array_slice
from pinelib.reference.heap import ReferenceHandle
from pinelib.reference.udt import udt_get
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
from pinelib.state.checkpoint import to_portable

ROOT = Path(__file__).resolve().parents[1]


def compile_graph(path, version, imported, shape, transient=False):
    commits = json.loads((ROOT / "docs/RC6_LIFECYCLE_SOURCES.json").read_text())
    git = shutil.which("git")
    assert git is not None
    result = run_logged([git, "rev-parse", "HEAD"], cwd=ROOT,
        output=path / "provenance", env=dict(os.environ), timeout=10)
    assert result["returncode"] == 0
    commits["openpine"] = (path / "provenance/stdout.log").read_text().strip()
    nominal = shape == "udt"
    declaration = "type Holder\n    array<int> backing\n    array<int> window\n" if nominal else ""
    body = "make()=>\n    backing=array.new_int(1,7)\n    array.push(backing,8)\n    window=array.slice(backing,1,2)\n" if transient else "make()=>\n    backing=array.new_int(3,7)\n    window=array.slice(backing,1,3)\n"
    body += "    Holder.new(backing,window)\n" if nominal else "    window\n"
    kwargs = {}
    if imported:
        library = declaration.replace("type Holder", "export type Holder") + "export " + body
        text = f'//@version={version}\nlibrary("Roots")\n' + library
        kwargs["library_store"] = LibraryStore.create({"qa/Roots/1": text})
        prefix, call = "import qa/Roots/1 as roots\n", "roots.make()"
    else:
        text = None
        prefix, call = declaration + body, "make()"
    source = f'//@version={version}\nindicator("retained graph")\n' + prefix
    source += f'root={call}\nplot(array.get({"root.window" if nominal else "root"},0))\n'
    result = NativeRC6CompilerAdapter().compile(
        source, module_name="retained_graph", source_name="retained-graph.pine",
        producer_commits=commits, **kwargs,
    )
    assert result.success, result.errors
    artifact = {name: getattr(result, name) for name in (
        "generated_artifact", "python_code", "consumer_bundle", "source_map", "compile_meta",
    )}
    if imported:
        assert "qa/Roots/1" in result.generated_artifact["external_library_dependency_hashes"]
    return artifact, source, text, commits


def executor(artifact, source, version, compact):
    host = RC6RuntimeExecutor(
        artifact=artifact,
        language=RuntimeLanguageContext(version, "stage2", f"pine-v{version}",
            "sha256:" + hashlib.sha256(source.encode()).hexdigest(), "compiler_annotation"),
        instrument=InstrumentContext(ticker="SOLUSDT", tickerid="BINANCE:SOLUSDT", prefix="BINANCE",
            currency="USDT", basecurrency="SOL", timezone="UTC", instrument_type="crypto", mintick=0.01),
        timeframe=TimeframeContext.parse("1"),
    )
    host.session.commit_full_identity = not compact
    return host


def begin(host, sequence, deferred, realtime=False):
    bar = int(realtime)
    return host.session.begin(CallbackFrame(
        "REALTIME_EVAL" if realtime else "HISTORICAL_EVAL", host.session.sequence + 1,
        bar_index=bar, last_bar_index=1, realtime=realtime, final_tick=not realtime,
        tick_index=sequence - 1 if realtime else 0, defer_bar_commit=deferred,
    ), values=BarValues(open=100, high=101, low=99, close=100, volume=10,
        time=1700000000000 + bar * 60000, time_close=1700000060000 + bar * 60000))


def generated_graph(tx, shape):
    rows = [row for row in tx.references.to_json()["objects"] if not row["committed_exists"]]
    views = [row for row in rows if row["kind"] == "array" and isinstance(row["working"], dict)]
    assert len(views) == 1
    row = views[0]
    window = ReferenceHandle(row["object_id"], "array")
    marker = row["working"]["$pinelib_array_slice"]["parent"]["$pinelib_ref"]
    backing = ReferenceHandle(marker["object_id"], "array")
    if shape == "udt":
        objects = [row for row in rows if row["kind"] == "udt"]
        assert len(objects) == 1
        root = ReferenceHandle(objects[0]["object_id"], "udt")
        assert udt_get(tx.references, root, "backing") == backing
        assert udt_get(tx.references, root, "window") == window
    else:
        root = window
    return root, backing, window


def save_case(path, artifact, source, library, commits, snapshots, trace):
    path.joinpath("source.pine").write_text(source)
    if library is not None:
        path.joinpath("library.pine").write_text(library)
    path.joinpath("generated.py").write_text(artifact["python_code"])
    for name, value in {"artifact": artifact, "commits": commits, "trace": trace, **snapshots}.items():
        path.joinpath(name + ".json").write_text(json.dumps(value, sort_keys=True) + "\n")


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("shape", ["array", "udt"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("finish", ["abort", "nonfinal_commit"])
@pytest.mark.parametrize("deferred", [False, True])
def test_lang08_emitted_graph_root_survives_fresh_host_runtime_restore(tmp_path, version, imported, shape, compact, finish, deferred):
    artifact, source, library, commits = compile_graph(tmp_path, version, imported, shape)
    save_case(tmp_path, artifact, source, library, commits, {},
        {"expected_constructor_value": 7, "kind": "allocation_retention"})
    host = executor(artifact, source, version, compact)
    tx = begin(host, 0, deferred)
    host.generated_class(tx).run()
    tx.commit()
    if deferred:
        host.session.finalize_bar(0)
    tx = begin(host, 1, deferred, True)
    host.generated_class(tx).run()
    root, backing, window = generated_graph(tx, shape)
    assert array_get(tx.references, window, 0) == 7
    value = {"root": root, "aliases": (window, window)}
    tx.set_slot("extension-root", value, owner="extension", varip=True)
    array_set(tx.references, backing, 1, 9)
    assert array_get(tx.references, window, 0) == 9
    if finish == "abort":
        tx.abort()
    else:
        tx.commit()
        if deferred:
            with pytest.raises(PineRuntimeError, match="active|provisional"):
                host.session.checkpoint()
            tx = begin(host, 2, deferred, True)
            assert all(tx.references.contains(handle.object_id) for handle in (root, backing, window))
            assert array_get(tx.references, window, 0) == 7
            tx.abort()
    saved = json.loads(json.dumps(host.session.checkpoint().to_dict()))
    clone = executor(artifact, source, version, compact)
    clone.session.restore(saved)
    assert clone.session.checkpoint().to_dict() == saved
    snapshots = {"first_checkpoint": saved}
    trace = {"expected_constructor_value": 7, "emitted_graph_root": to_portable(value),
             "pine_version": version, "imported": imported, "shape": shape,
             "compact": compact, "finish": finish, "deferred": deferred}
    for current in (host, clone):
        tx = begin(current, 2, deferred, True)
        assert all(tx.references.contains(handle.object_id) for handle in (root, backing, window))
        assert to_portable(tx.state("extension-root", owner="extension", schema_version="1", initial=None, varip=True)) == to_portable(value)
        assert array_get(tx.references, window, 0) == 7
        if shape == "udt":
            assert udt_get(tx.references, root, "window") == window
        # Run the actual emitted callback again while the extension aliases
        # retain the previous emitted allocation; no generated code is patched.
        current.generated_class(tx).run()
        array_set(tx.references, window, 0, 11)
        assert array_get(tx.references, backing, 1) == 11
        tx.abort()
        assert array_get(current.session.references, window, 0) == 7
        rows = {row["object_id"]: row for row in current.session.references.to_json()["objects"]}
        assert all("intrabar_persistence" not in rows[h.object_id] for h in (root, backing, window))
    final = json.loads(json.dumps(host.session.checkpoint().to_dict()))
    assert clone.session.checkpoint().to_dict() == final
    executor(artifact, source, version, compact).session.restore(final)
    snapshots["final_checkpoint"] = final
    trace["observed_after_rollback"] = 7
    save_case(tmp_path, artifact, source, library, commits, snapshots, trace)


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("shape", ["array", "udt"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_lang08_emitted_transient_view_root_rejected_before_slot_mutation(tmp_path, version, imported, shape, compact, deferred):
    artifact, source, library, commits = compile_graph(tmp_path, version, imported, shape, transient=True)
    save_case(tmp_path, artifact, source, library, commits, {},
        {"expected_value_before_rejection": 8, "expected_rejection": "PL1611"})
    host = executor(artifact, source, version, compact)
    tx = begin(host, 0, deferred)
    host.generated_class(tx).run()
    tx.commit()
    if deferred:
        host.session.finalize_bar(0)
    tx = begin(host, 1, deferred, True)
    host.generated_class(tx).run()
    root, backing, window = generated_graph(tx, shape)
    assert array_get(tx.references, window, 0) == 8
    before = host.session.slots.to_json()
    with pytest.raises(PineRuntimeError, match="bounds") as error:
        tx.set_slot("extension-root", {"root": root}, owner="extension", varip=True)
    assert error.value.code == "PL1611"
    assert host.session.slots.to_json() == before
    tx.abort()
    assert not any(host.session.references.contains(h.object_id) for h in (root, backing, window))
    saved = json.loads(json.dumps(host.session.checkpoint().to_dict()))
    clone = executor(artifact, source, version, compact)
    clone.session.restore(saved)
    assert clone.session.checkpoint().to_dict() == saved
    save_case(tmp_path, artifact, source, library, commits, {"final_checkpoint": saved},
        {"expected_value_before_rejection": 8, "error_code": error.value.code,
         "slot_mutation": False, "pine_version": version, "imported": imported,
         "shape": shape, "compact": compact, "deferred": deferred})


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("mutation", ["state_alias", "udt_field"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("deferred", [False, True])
def test_lang08_emitted_late_graph_mutation_rejects_callback_atomically(tmp_path, version, imported, mutation, compact, deferred):
    shape = "udt" if mutation == "udt_field" else "array"
    artifact, source, library, commits = compile_graph(tmp_path, version, imported, shape)
    save_case(tmp_path, artifact, source, library, commits, {},
        {"mutation": mutation, "expected_rejection": "PL1611"})
    host = executor(artifact, source, version, compact)
    tx = begin(host, 0, deferred)
    host.generated_class(tx).run()
    tx.commit()
    if deferred:
        host.session.finalize_bar(0)
    before = json.loads(json.dumps(host.session.checkpoint().to_dict()))
    tx = begin(host, 1, deferred, True)
    host.generated_class(tx).run()
    root, backing, window = generated_graph(tx, shape)
    value = tx.state("extension-root", owner="extension", schema_version="1",
                     initial={"nested": [root]}, varip=True)
    array_push(tx.references, backing, 8)
    late = array_slice(tx.references, backing, 3, 4, "late-window")
    if mutation == "udt_field":
        tx.set_udt_field_v1(root, "window", late)
    else:
        value["nested"].append(late)
    assert array_get(tx.references, late, 0) == 8
    with pytest.raises(PineRuntimeError, match="bounds") as error:
        tx.abort()
    assert error.value.code == "PL1611"
    assert host.session._active is None and tx.closed
    assert host.session.checkpoint().to_dict() == before
    assert not any(host.session.references.contains(h.object_id) for h in (root, backing, window, late))
    clone = executor(artifact, source, version, compact)
    clone.session.restore(before)
    assert clone.session.checkpoint().to_dict() == before
    for current in (host, clone):
        tx = begin(current, 1, deferred, True)
        current.generated_class(tx).run()
        tx.abort()
    final = json.loads(json.dumps(host.session.checkpoint().to_dict()))
    assert clone.session.checkpoint().to_dict() == final
    save_case(tmp_path, artifact, source, library, commits,
        {"before_checkpoint": before, "final_checkpoint": final},
        {"mutation": mutation, "error_code": error.value.code, "attempt_atomic": True,
         "pine_version": version, "imported": imported, "compact": compact, "deferred": deferred})


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("shape", ["array", "udt"])
@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("transition", ["callback_abort", "late_root_rejection", "working_shrink_rejection"])
def test_lang08_emitted_confirmed_deferred_transient_baseline_and_publication(tmp_path, version, imported, shape, compact, transition):
    artifact, source, library, commits = compile_graph(tmp_path, version, imported, shape, transient=True)
    save_case(tmp_path, artifact, source, library, commits, {},
        {"transition": transition, "expected_published_value": 8})
    host = executor(artifact, source, version, compact)
    tx = begin(host, 0, True)
    host.generated_class(tx).run()
    first = generated_graph(tx, shape)
    tx.commit()
    previous = host.session._state_json()
    transcript = host.session.transcript.to_dict()
    pending = host.session._pending_bar_frame
    tx = begin(host, 1, True)
    host.generated_class(tx).run()
    root, backing, window = generated_graph(tx, shape)
    if transition == "callback_abort":
        tx.abort()
        saved = json.loads(json.dumps(host.session.checkpoint().to_dict()))
        clone = executor(artifact, source, version, compact)
        clone.session.restore(saved)
        assert clone.session.checkpoint().to_dict() == saved
        for current in (host, clone):
            tx = begin(current, 2, True)
            current.generated_class(tx).run()
            tx.commit()
            current.session.finalize_bar(0)
        final = host.session.checkpoint().to_dict()
        assert clone.session.checkpoint().to_dict() == final
    else:
        if transition == "late_root_rejection":
            value = tx.state("extension-root", owner="extension", schema_version="1",
                             initial={"nested": []}, varip=True)
            value["nested"].append(root)
            finish = tx.abort
        else:
            array_pop(tx.references, backing)
            finish = tx.commit
        with pytest.raises(PineRuntimeError, match="bounds") as error:
            finish()
        assert error.value.code == "PL1611"
        assert tx.closed and host.session._active is None
        assert host.session._state_json() == previous
        assert host.session.transcript.to_dict() == transcript
        assert host.session._pending_bar_frame == pending
        assert array_get(host.session.references, first[2], 0) == 8
        with pytest.raises(PineRuntimeError, match="active|provisional"):
            host.session.checkpoint()
        host.session.finalize_bar(0)
        final = host.session.checkpoint().to_dict()
        executor(artifact, source, version, compact).session.restore(final)
    save_case(tmp_path, artifact, source, library, commits, {"final_checkpoint": final},
        {"transition": transition, "pine_version": version, "imported": imported,
         "shape": shape, "compact": compact, "observed_published_value": 8})
