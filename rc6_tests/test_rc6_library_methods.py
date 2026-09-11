"""Public library methods must execute identical broker paths and preserve state.

Expected values/prices are manual fixtures, never learned from the tested engine.
"""

import json

import pytest
from pine2ast.libraries import LibraryStore
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_generated_checkpoint import advance
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_library_imports import prepare
from rc6_tests.test_rc6_worker_admission import _manifest

DECL = """export type Counter
    int n=0
    varip int ticks=0
method plus(Counter self,int step)=>
    self.n+=step
    self.ticks+=step
    self
export method add(Counter self,int step)=>self.plus(step)
export method value(Counter self)=>self.n
"""


def setup(
    version=6, on_close=False, recalc=False, imported=True, broker_guard=True, definition=DECL
):
    libs = {"qa/Signal/1": f'//@version={version}\nlibrary("Signal")\n' + definition}
    declarations = "import qa/Signal/1 as signal" if imported else definition.replace("export ", "")
    typ = "signal.Counter" if imported else "Counter"
    guard = " and strategy.position_size==0" if broker_guard else ""
    source = f"""//@version={version}
strategy("library method execution")
{declarations}
var {typ} a={typ}.new()
var {typ} b={typ}.new()
a.add(1)
b.add(10)
c=a.copy().add(100)
if bar_index==2{guard} and a.value()==3 and b.value()==30 and c.value()==103
    strategy.entry("method",strategy.long,qty=a.value())
"""
    return prepare(version, on_close, recalc, source_override=source, libs=libs)


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
@pytest.mark.parametrize("imported", [False, True])
def test_methods_keep_typed_state_and_exact_broker_commands(
    monkeypatch, tmp_path, version, on_close, recalc, imported
):
    case, rows = setup(version, on_close, recalc, imported)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [("method", 2, 3)]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3)]
    assert case[0].success
    if imported:
        assert case[0].compile_meta["library_linkage"]["profile"] == "same_version_methods_v5"
        assert set(case[0].generated_artifact["external_library_dependency_hashes"]) == {
            "qa/Signal/1",
            "@linkage",
        }


@pytest.mark.parametrize("cut", [1, 3, 5])
def test_linked_methods_resume_same_generated_state_and_reject_other_sources(cut):
    case, rows = setup(broker_guard=False)
    whole = make_session(case)
    expected = advance(whole, rows)
    first = make_session(case)
    prefix = advance(first, rows, stop=cut)
    snapshot = json.loads(json.dumps(first.export_state()))
    resumed = make_session(case)
    resumed.restore_state(snapshot)
    assert prefix + advance(resumed, rows, start=cut) == expected
    assert resumed.export_state() == whole.export_state()
    changed, _ = setup(broker_guard=False, definition=DECL + "\n// corrected publication\n")
    assert (
        changed[0].generated_artifact["content_hash"] != case[0].generated_artifact["content_hash"]
    )
    with pytest.raises(ValueError, match="identity"):
        make_session(changed).restore_state(snapshot)


@pytest.mark.parametrize(
    "bad",
    [
        "var signal.Counter a=signal.Counter.new()\nx=a.plus(1)",
        "n=close\nx=n.fixed()",
        "n=2\nx=n.fixed()\nq=input.int(x)",
    ],
)
def test_private_method_and_qualifier_mismatches_fail_before_worker_start(bad):
    libs = {
        "qa/Signal/1": '//@version=6\nlibrary("Signal")\n'
        + DECL
        + "\nexport method fixed(simple int self)=>7\n"
    }
    result = NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("invalid methods")\nimport qa/Signal/1 as signal\n' + bad,
        library_store=LibraryStore.create(libs),
        producer_commits={"pine2ast": "a" * 40, "ast2python": "b" * 40},
    )
    assert not result.success and not result.python_code


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_worker_runs_exported_methods_without_library_files(
    tmp_path, mode, on_close
):
    from openpine.runtime.isolated_run import run_isolated_artifact

    case, rows = setup(on_close=on_close, recalc=True)
    compiled, context, cfg = case
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
    out = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=cfg, params={}
    )
    assert out["ok"] and out["bars_processed"] == 6
    assert [(e["command_id"], float(e["qty"])) for e in out["intent_tape"]] == [("method", 3)]
    assert [(t.entry_price, t.qty) for t in out["raw_result"].open_trades] == [
        (102 if on_close else 103, 3)
    ]
