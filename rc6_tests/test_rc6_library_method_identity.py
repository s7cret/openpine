"""Execution must keep the exact public overload selected at the import boundary.

A private, more-specific overload is legal inside its own library, never selected
by an unrelated consumer after source projection. Values/prices are hand-derived.
"""

import pytest

from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_library_imports import prepare
from rc6_tests.test_rc6_worker_admission import _manifest


def setup(version=6, on_close=False, recalc=False):
    library = f"""//@version={version}
library("Identity")
method value(simple int self,int step)=>
    var int n=1000
    n+=step
    n
export method value(simple int self,float step)=>
    var float n=100
    n+=step
    n
export method inside(simple int self)=>self.value(1)
"""
    source = f"""//@version={version}
strategy("private selection must not escape")
import qa/Identity/1 as lib
n=2
publicResult=n.value(step=1)
internalResult=n.inside()
if bar_index==2 and strategy.position_size==0 and publicResult==103 and internalResult==1003
    strategy.entry("identity",strategy.long,qty=publicResult-100)
"""
    return prepare(
        version, on_close, recalc, source_override=source, libs={"qa/Identity/1": library}
    )


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_public_and_private_overloads_produce_distinct_results_and_exact_order(
    monkeypatch, tmp_path, version, on_close, recalc
):
    case, rows = setup(version, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("identity", 2, 3)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3)]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_protected_worker_preserves_exact_library_method_identity(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact

    case, rows = setup(on_close=on_close, recalc=True)
    compiled, context, config = case
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
        setattr(config, name, value)
    output = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert output["ok"] and output["bars_processed"] == 6
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in output["intent_tape"]] == [
        ("identity", 2, 3)
    ]
    assert [(t.entry_price, t.qty) for t in output["raw_result"].open_trades] == [
        (102 if on_close else 103, 3)
    ]
