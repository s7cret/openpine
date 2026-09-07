"""Linked typed collections, intrabar state and both broker transports.

In-memory transport tests do not claim protected-process acceptance. The four
real-process variants below stay mandatory in the permanent full CI.
"""

import json

import pytest

from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare as prepare_plain
from rc6_tests.test_rc6_library_imports import prepare as prepare_linked
from rc6_tests.test_rc6_lifecycle import make_session, event, values
from rc6_tests.test_rc6_reference_loop_values import advance

FUNCTION = """count(int step)=>
    varip a=array.new<int>(1,0)
    varip m=map.new<string,int>()
    varip q=matrix.new<int>(1,1,0)
    array.set(a,0,array.get(a,0)+step)
    map.put(m,"n",(map.contains(m,"n") ? map.get(m,"n") : 0)+step)
    matrix.set(q,0,0,matrix.get(q,0,0)+step)
    ms=0
    for [key,value] in m
        ms+=value
    qs=0
    for [index,row] in q
        qs+=array.get(row,0)
    [array.get(a,0),ms,qs]
"""


def prepare(version=6, imported=True, on_close=False, recalc=False, realtime=False):
    prefix = "import qa/Counts/1 as lib\n" if imported else FUNCTION
    call = "lib.count" if imported else "count"
    body = prefix + f"[a,m,q]={call}(1)\n[b,n,r]={call}(10)\n"
    if realtime:
        body += 'strategy.entry("tick",strategy.long,qty=a+m+q+b+n+r)\n'
    else:
        body += 'if bar_index==2 and strategy.position_size==0 and a==3 and m==3 and q==3 and b==30 and n==30 and r==30\n    strategy.entry("collections",strategy.long,qty=a)\n'
    if imported:
        libs = {"qa/Counts/1": f'//@version={version}\nlibrary("Counts")\nexport ' + FUNCTION}
        source = f'//@version={version}\nstrategy("collections")\n' + body
        return prepare_linked(version, on_close, recalc, source_override=source, libs=libs)
    return prepare_plain(
        body,
        [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(6)],
        version=version,
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_collection_state_and_iteration_drive_exact_broker_results(
    monkeypatch, tmp_path, version, imported, on_close, recalc
):
    case, rows = prepare(version, imported, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("collections", 2, 3)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3)]
    if imported:
        assert case[0].compile_meta["library_linkage"]["profile"] == "same_version_collections_v3"


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("version", [5, 6])
def test_generated_host_varip_collections_survive_ticks_as_scalar_varip_does(imported, version):
    case, _ = prepare(version, imported, realtime=True)
    obj = make_session(case)
    base = dict(
        index=0,
        last_bar_index=0,
        last_historical_bar_index=-1,
        realtime=True,
        phase="REALTIME_EVAL",
        cause="TICK",
    )
    out = []
    for i in range(4):
        ev = event(sequence=i, recalc=i, tick_index=i, final_tick=(i == 3), **base)
        out.append(float(obj.execute_callback(values(), ev, strategy_values={}).intents[0]["qty"]))
    obj.finalize_bar(0)
    assert out == [33, 66, 99, 132]
    assert len(obj.session.series["close"].committed) == 1
    saved = json.loads(json.dumps(obj.export_state()))
    restored = make_session(case)
    restored.restore_state(saved)
    assert restored.export_state() == saved


@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("cut", [1, 3, 5])
def test_generated_checkpoint_retains_all_three_collection_kinds(imported, cut):
    case, rows = prepare(imported=imported)
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
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_worker_linked_intrabar_collections(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = prepare(on_close=on_close, recalc=True)
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
    out = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert out["ok"] and out["bars_processed"] == len(rows)
    assert [(e["command_id"], float(e["qty"])) for e in out["intent_tape"]] == [("collections", 3)]
    assert [(t.entry_price, t.qty) for t in out["raw_result"].open_trades] == [
        (102 if on_close else 103, 3)
    ]
