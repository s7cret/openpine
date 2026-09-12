"""Compiled native array descriptors must preserve broker-producing searches."""

import pytest

from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes, prepare as prepare_plain
from rc6_tests.test_rc6_library_imports import prepare as prepare_libraries
from rc6_tests.test_rc6_worker_admission import _manifest


def prepare(version=5, imported=False, method=False, on_close=False, recalc=False):
    search = (
        "a.binary_search_leftmost(3),a.binary_search_rightmost(3)"
        if method
        else "array.binary_search_leftmost(a,3),array.binary_search_rightmost(a,3)"
    )
    function = f"""rank() =>
    a=array.new_float()
    array.push(a,-2)
    array.push(a,0)
    array.push(a,1)
    array.push(a,5)
    array.push(a,9)
    [{search}]
"""
    head = (
        "import qa/Rank/1 as lib\n[l,r]=lib.rank()\n" if imported else function + "[l,r]=rank()\n"
    )
    source = f"""//@version={version}
strategy("native array search")
{head}
if bar_index==2 and strategy.position_size==0 and l==2 and r==3
    strategy.entry("search",strategy.long,qty=r)
"""
    if imported:
        libs = {"qa/Rank/1": f'//@version={version}\nlibrary("Rank")\nexport ' + function}
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
@pytest.mark.parametrize("method", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_native_array_search_trades_match_in_both_transports(
    monkeypatch, tmp_path, version, imported, method, on_close, recalc
):
    case, rows = prepare(version, imported, method, on_close, recalc)
    result, tape = compare_modes(monkeypatch, tmp_path, case, rows)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [("search", 2, 3)]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(102 if on_close else 103, 3)]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_protected_worker_searches_native_array(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact

    case, rows = prepare(5, True, True, on_close, True)
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
    output = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in rows], config=config, params={}
    )
    assert output["ok"] and output["bars_processed"] == 6
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in output["intent_tape"]] == [
        ("search", 2, 3)
    ]
    assert [(t.entry_price, t.qty) for t in output["raw_result"].open_trades] == [
        (102 if on_close else 103, 3)
    ]
