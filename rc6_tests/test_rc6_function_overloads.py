"""Declaration-bound overloads through disk persistence and the existing broker.

Expected values are independently hand-derived from Pine bodies. compare_modes
uses in-process transport substitution only; four separate cases retain real
protected-worker requirements. This is not a recorded TradingView oracle.
"""

from __future__ import annotations

import json

import pytest
from pine2ast.libraries import LibraryStore

from openpine.artifacts.store import ArtifactStore
from openpine.compile.native_rc6 import CompileResult, NativeRC6CompilerAdapter
from openpine.compile.pipeline import persist_compile_result
from openpine.pine.source import PineSource
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_generated_checkpoint import advance
from rc6_tests.test_rc6_library_imports import prepare
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_worker_admission import _manifest

DECL = """export count(int step)=>
    var int n=0
    n+=step
    n
export count(float step)=>
    var float n=0.0
    n+=step
    n
"""


def setup(version=6, on_close=False, recalc=False, imported=True, broker_guard=True, decl=DECL):
    namespace = "signal." if imported else ""
    libs = {"qa/Signal/1": f'//@version={version}\nlibrary("Signal")\n' + decl}
    definitions = "import qa/Signal/1 as signal" if imported else decl.replace("export ", "")
    guard = " and strategy.position_size==0" if broker_guard else ""
    source = f"""//@version={version}
strategy("function families")
{definitions}
a={namespace}count(1)
b={namespace}count(step=10.0)
if bar_index==2{guard} and a==3 and b==30
    strategy.entry("overloads",strategy.long,qty=a+b)
"""
    return prepare(version, on_close, recalc, source_override=source, libs=libs), source


def roundtrip(case, text, folder):
    src = PineSource(id="overload-roundtrip", name="families", source_text=text)
    store = ArtifactStore(folder)
    receipt = persist_compile_result(src, case[0], artifact_store=store)
    loaded = store.get_artifact(receipt["artifact_id"], src.id)
    assert loaded["source_text"] == text
    compiled = CompileResult(
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
    return (compiled, case[1], case[2])


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_stored_overloads_drive_independent_counters_and_real_fills(
    monkeypatch, tmp_path, version, imported, on_close, recalc
):
    (case, rows), source = setup(version, on_close, recalc, imported)
    recovered = roundtrip(case, source, tmp_path / "artifacts")
    result, tape = compare_modes(monkeypatch, tmp_path, recovered, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("overloads", 2, 33)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 33)]
    caps = case[0].consumer_bundle["consumer_contract"]["required_capabilities"]
    assert ("library_function_overloads_v1" if imported else "user_function_overloads_v1") in caps


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("cut", [1, 3, 5])
def test_state_restore_after_persistence_keeps_overload_identity(tmp_path, version, imported, cut):
    (case, rows), source = setup(version=version, imported=imported, broker_guard=False)
    recovered = roundtrip(case, source, tmp_path / "artifacts")
    whole = make_session(recovered)
    expected = advance(whole, rows)
    partial = make_session(recovered)
    first = advance(partial, rows, stop=cut)
    saved = json.loads(json.dumps(partial.export_state()))
    resumed = make_session(recovered)
    resumed.restore_state(saved)
    assert first + advance(resumed, rows, start=cut) == expected
    assert resumed.export_state() == whole.export_state()
    (other, _), _ = setup(
        version=version,
        imported=imported,
        broker_guard=False,
        decl=DECL.replace("n+=step", "n+=step*2"),
    )
    with pytest.raises(ValueError, match="identity"):
        make_session(other).restore_state(saved)


@pytest.mark.parametrize(
    "bad", ['signal.count("bad")', "signal.count(1,step=1)", "signal.count(unknown=2)"]
)
def test_invalid_overload_never_reaches_emission(bad):
    source = '//@version=6\nstrategy("bad")\nimport qa/Signal/1 as signal\nx=' + bad + "\n"
    result = NativeRC6CompilerAdapter().compile(
        source,
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
        library_store=LibraryStore.create(
            {"qa/Signal/1": '//@version=6\nlibrary("Signal")\n' + DECL}
        ),
    )
    assert not result.success and not result.python_code and result.diagnostics


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_workers_execute_persisted_overloaded_library(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact

    (case, rows), text = setup(on_close=on_close, recalc=True)
    compiled, context, cfg = roundtrip(case, text, tmp_path / "artifacts")
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(cfg, name, value)
    result = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=cfg, params={}
    )
    assert result["ok"] and result["bars_processed"] == 6
    assert [(e["command_id"], float(e["qty"])) for e in result["intent_tape"]] == [
        ("overloads", 33)
    ]
    assert [(t.entry_price, t.qty) for t in result["raw_result"].open_trades] == [
        (102 if on_close else 103, 33)
    ]
