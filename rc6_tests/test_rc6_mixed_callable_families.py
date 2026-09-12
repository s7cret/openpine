"""Mixed callable families through persisted artifacts and the existing broker.

Local expected values are hand-derived engineering cases, not TV exports. The
ordinary transport harness is in-process; separate cases require real isolation.
"""

from __future__ import annotations

import json

import pytest
from pine2ast.libraries import LibraryStore

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_function_overloads import roundtrip, setup as setup_family
from rc6_tests.test_rc6_generated_checkpoint import advance
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_worker_admission import _manifest

DECL = """export count(float step)=>
    var float n=0.0
    n+=step
    n
export method count(int step)=>
    var int n=0
    n+=step
    n
"""


def setup(**kwargs):
    return setup_family(decl=DECL, **kwargs)


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_mixed_declarations_survive_persistence_and_produce_real_fills(
    monkeypatch, tmp_path, version, imported, on_close, recalc
):
    (case, rows), text = setup(version=version, imported=imported, on_close=on_close, recalc=recalc)
    recovered = roundtrip(case, text, tmp_path / "artifacts")
    result, tape = compare_modes(monkeypatch, tmp_path, recovered, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("overloads", 2, 33)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 33)]
    caps = recovered[0].consumer_bundle["consumer_contract"]["required_capabilities"]
    assert "mixed_user_callable_families_v1" in caps
    kinds = {c["call_form"] for c in recovered[0].consumer_bundle["semantic_facts"]["calls"]}
    assert {"USER_METHOD", "USER_FUNCTION"} <= kinds


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("cut", [1, 3, 5])
def test_mixed_checkpoint_retains_declaration_and_written_call_identity(
    tmp_path, version, imported, cut
):
    (case, rows), text = setup(version=version, imported=imported, broker_guard=False)
    recovered = roundtrip(case, text, tmp_path / "artifacts")
    whole = make_session(recovered)
    expected = advance(whole, rows)
    partial = make_session(recovered)
    first = advance(partial, rows, stop=cut)
    saved = json.loads(json.dumps(partial.export_state()))
    restored = make_session(recovered)
    restored.restore_state(saved)
    assert first + advance(restored, rows, start=cut) == expected
    assert restored.export_state() == whole.export_state()
    (other, _), _ = setup_family(
        version=version,
        imported=imported,
        broker_guard=False,
        decl=DECL.replace("n+=step", "n+=step*2"),
    )
    with pytest.raises(ValueError, match="identity"):
        make_session(other).restore_state(saved)


@pytest.mark.parametrize(
    "bad",
    ["signal.count(true)", "signal.count(1,step=2)", "signal.count()", "signal.count(unknown=1)"],
)
def test_invalid_mixed_library_call_is_rejected_before_emission(bad):
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
def test_protected_worker_executes_persisted_mixed_library(tmp_path, mode, on_close):
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
