"""Reference/loop/import changes drive real broker orders and preserve checkpoints."""

import json

import pytest
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_library_imports import prepare as prepare_libraries
from rc6_tests.test_rc6_deferred_exits import prepare as prepare_plain


def prepare(version=6, imported=True, on_close=False, recalc=False):
    function = """build(float x) =>
    var a=array.new_float()
    array.push(a,x)
    a
"""
    head = (
        "import qa/Values/1 as values\na=values.build(close)\n"
        if imported
        else function + "a=build(close)\n"
    )
    source = f"""//@version={version}
strategy("reference loops")
{head}
instance=array.new_float(1,close)
previous=instance[1]
old=na(previous) ? 0 : array.get(previous,0)
sum=0.0
last=for [index,value] in a
    if index==1
        continue
    sum+=value
    sum
j=0
[n,result]=while j<3
    j+=1
    if j==2
        break
    [array.size(a),last]
if bar_index==2 and strategy.position_size==0 and last==202 and old==101 and n==3 and result==202
    strategy.entry("reference-loop",strategy.long,qty=array.size(a))
"""
    # This is not a source substitute: both variants are original Pine programs.
    if imported:
        libs = {"qa/Values/1": f'//@version={version}\nlibrary("Values")\nexport ' + function}
        return prepare_libraries(version, on_close, recalc, source_override=source, libs=libs)
    return prepare_plain(
        "\n".join(source.splitlines()[2:]) + "\n",
        [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(6)],
        version=version,
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )


@pytest.mark.parametrize("version", [5, 6])
@pytest.mark.parametrize("imported", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_references_loops_and_locked_functions_produce_identical_trades(
    monkeypatch, tmp_path, version, imported, on_close, recalc
):
    case, rows = prepare(version, imported, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [
        ("reference-loop", 2, 3.0)
    ]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3.0)]
    linkage = case[0].compile_meta.get("library_linkage")
    assert bool(linkage) == imported
    if imported:
        assert linkage["profile"] == "same_version_arrays_v2"


def advance(runtime, rows, start=0, stop=None):
    # Checkpoint test is the generated session, not a whole-broker restart. Supply
    # its required read-only flat-position snapshot explicitly, as in a fresh host.
    from openpine_contracts import ExecutionEvent
    from pinelib.runtime.metadata import BarValues

    tape = []
    for index in range(start, len(rows) if stop is None else stop):
        row = rows[index]
        values = BarValues(
            **{name: float(row[name]) for name in ("open", "high", "low", "close", "volume")},
            time=row["open_time_utc_ms"],
            time_close=row["close_time_utc_ms"],
        )
        event = ExecutionEvent(
            index,
            index,
            len(rows) - 1,
            len(rows) - 1,
            values.time,
            "HISTORICAL_EVAL",
            False,
            True,
            0,
            0,
            "BAR_CLOSE",
        )
        tape.extend(
            runtime.execute_callback(
                values, event, strategy_values={"strategy.position_size": 0.0}
            ).intents
        )
        runtime.finalize_bar(index)
    return tape


@pytest.mark.parametrize("cut", [1, 3, 5])
@pytest.mark.parametrize("imported", [False, True])
def test_reference_heap_and_history_survive_generated_session_checkpoint(cut, imported):
    case, rows = prepare(imported=imported)
    whole = make_session(case)
    expected = advance(whole, rows)
    part = make_session(case)
    prefix = advance(part, rows, stop=cut)
    saved = json.loads(json.dumps(part.export_state()))
    restored = make_session(case)
    restored.restore_state(saved)
    suffix = advance(restored, rows, start=cut)
    assert prefix + suffix == expected
    assert restored.export_state() == whole.export_state()
    assert len(expected) == 1 and float(expected[0]["qty"]) == 3


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_worker_reference_loop_imports(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, rows = prepare(on_close=on_close, recalc=True)
    compiled, context, config = case
    for key, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=rows,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, key, value)
    out = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert out["ok"] and out["bars_processed"] == len(rows)
    assert [(e["command_id"], float(e["qty"])) for e in out["intent_tape"]] == [
        ("reference-loop", 3.0)
    ]
    assert [(t.entry_price, t.qty) for t in out["raw_result"].open_trades] == [
        (102 if on_close else 103, 3.0)
    ]


@pytest.mark.parametrize("version", [4, 5, 6])
def test_explicit_array_binding_and_set_use_versioned_syntax(monkeypatch, tmp_path, version):
    typename = "float[]" if version == 4 else "array<float>"
    body = f"""f() =>
    var {typename} a=array.new_float(1,0)
    array.set(a,0,array.get(a,0)+1)
    array.get(a,0)
x=f()
"""
    body += (
        'strategy.entry("array",strategy.long,qty=x,when=bar_index==2)\n'
        if version < 6
        else 'if bar_index==2\n    strategy.entry("array",strategy.long,qty=x)\n'
    )
    case, rows = prepare_plain(body, [(100, 101, 99, 100)] * 5, version=version)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], float(e["qty"])) for e in tape] == [("array", 3.0)]
    assert result.open_trades[0].qty == 3.0
