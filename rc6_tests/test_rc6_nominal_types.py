"""Nominal values across compiler, broker transports and generated checkpoints.

Expected counters/fills are derived by hand. Protected-worker cases are mandatory;
in-memory equality alone is not evidence of sandbox acceptance.
"""

import json

import pytest

from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare as prepare_plain
from rc6_tests.test_rc6_library_imports import prepare as prepare_linked
from rc6_tests.test_rc6_lifecycle import event, make_session, values
from rc6_tests.test_rc6_reference_loop_values import advance


TYPES = """enum Direction
    up="Up"
    down="Down"
type Counter
    int total=0
    varip int ticks=0
    Direction direction=Direction.up
"""
FUNCTION = """count(int step)=>
    var Counter c=Counter.new()
    c.total+=step
    c.ticks+=step
    c.direction:=Direction.down
    c
"""


def prepare(version=6, imported=True, on_close=False, recalc=False, realtime=False):
    prefix = "import qa/Counts/1 as lib\n" if imported else TYPES + FUNCTION
    call = "lib.count" if imported else "count"
    direction = "lib.Direction.down" if imported else "Direction.down"
    body = prefix + f"a={call}(1)\nb={call}(10)\n"
    if realtime:
        body += 'strategy.entry("tick",strategy.long,qty=a.total+b.total+a.ticks+b.ticks)\n'
    else:
        body += f'if bar_index==2 and strategy.position_size==0 and a.total==3 and b.total==30 and a.direction=={direction}\n    strategy.entry("nominal",strategy.long,qty=a.total)\n'
    if imported:
        library = TYPES.replace("enum Direction", "export enum Direction").replace("type Counter", "export type Counter")
        libs = {"qa/Counts/1": f'//@version={version}\nlibrary("Counts")\n' + library + "export " + FUNCTION}
        return prepare_linked(version, on_close, recalc,
                              source_override=f'//@version={version}\nstrategy("nominal")\n' + body, libs=libs)
    return prepare_plain(body, [(100+i, 101+i, 99+i, 100+i) for i in range(6)], version=version,
                         process_orders_on_close=on_close, calc_on_order_fills=recalc)


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_nominal_udf_state_drives_exact_broker_results(monkeypatch, tmp_path, version, imported, on_close, recalc):
    case, rows = prepare(version, imported, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [("nominal", 2, 3)]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3)]
    if imported:
        assert case[0].compile_meta["library_linkage"]["profile"] == "same_version_reference_types_v4"


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
def test_udt_field_varip_keeps_ordinary_field_rollback(version, imported):
    case, _ = prepare(version, imported, realtime=True)
    session = make_session(case)
    initial = event(index=0, sequence=0, last_bar_index=1, last_historical_bar_index=0)
    session.execute_callback(values(), initial, strategy_values={})
    session.finalize_bar(0)
    base = dict(index=1, last_bar_index=1, last_historical_bar_index=0,
                realtime=True, phase="REALTIME_EVAL", cause="TICK")
    quantities = []
    for i in range(4):
        ev = event(sequence=i+1, recalc=i, tick_index=i, final_tick=(i == 3), **base)
        quantities.append(float(session.execute_callback(values(1), ev, strategy_values={}).intents[0]["qty"]))
    session.finalize_bar(1)
    assert quantities == [44, 55, 66, 77]
    snapshot = json.loads(json.dumps(session.export_state()))
    restored = make_session(case)
    restored.restore_state(snapshot)
    assert restored.export_state() == snapshot


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("cut", [1, 3, 5])
def test_nominal_checkpoint_resume_retains_reference_enum_and_callsite_identity(version, imported, cut):
    case, rows = prepare(version, imported)
    whole = make_session(case)
    expected = advance(whole, rows)
    prefix = make_session(case)
    first = advance(prefix, rows, stop=cut)
    restored = make_session(case)
    restored.restore_state(json.loads(json.dumps(prefix.export_state())))
    rest = advance(restored, rows, start=cut)
    assert first + rest == expected
    assert restored.export_state() == whole.export_state()
    assert len(expected) == 1 and float(expected[0]["qty"]) == 3


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("version", [5, 6])
def test_real_protected_worker_nominal_library_types(tmp_path, mode, version):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = prepare(version, on_close=True, recalc=True)
    compiled, context, config = case
    for name, value in dict(execution_context=context, admitted_manifest=_manifest(),
                           instrument_id=context["instrument_id"], generated_artifact=compiled.generated_artifact,
                           bar_envelopes=rows, run_hash="sha256:" + "1" * 64,
                           protocol_artifact_dir=str(tmp_path / "protocol"), isolated_protocol=mode).items():
        setattr(config, name, value)
    result = run_isolated_artifact(compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={})
    assert result["ok"] and result["bars_processed"] == len(rows)
    assert [(e["command_id"], float(e["qty"])) for e in result["intent_tape"]] == [("nominal", 3)]
    assert [(t.entry_price, t.qty) for t in result["raw_result"].open_trades] == [(102, 3)]
