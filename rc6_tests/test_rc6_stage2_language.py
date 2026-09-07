"""Stage 2 scalar language semantics through the real compiler and broker.

Only IPC is substituted in compare_modes. Four separate process cases retain
all normal sandbox requirements. Expected orders are manually derived.
"""

import json

import pytest

from rc6_tests.test_rc6_deferred_exits import prepare, compare_modes
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_generated_checkpoint import advance

ROWS = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(6)]
BODY = """count(int step=1)=>
    var int n=0
    n+=step
    n
a=count()
b=count(step=10)
once bar_index>=2
    if strategy.position_size==0
        strategy.entry("once",strategy.long,qty=a+b)
"""


@pytest.mark.parametrize("on_close", [False, True])
@pytest.mark.parametrize("recalc", [False, True])
def test_once_and_two_written_function_calls_drive_one_exact_order(
    monkeypatch, tmp_path, on_close, recalc
):
    case, bars = prepare(
        BODY,
        ROWS,
        initial_capital=100000,
        process_orders_on_close=on_close,
        calc_on_order_fills=recalc,
    )
    result, tape = compare_modes(monkeypatch, tmp_path, case, bars)
    assert [(e["command_id"], e["bar_index"], float(e["qty"])) for e in tape] == [("once", 2, 33)]
    assert [(t.entry_bar_index, t.entry_price, t.qty) for t in result.open_trades] == [
        (2 if on_close else 3, 102 if on_close else 103, 33)
    ]


@pytest.mark.parametrize("version", [4, 5, 6])
def test_sparse_parameter_history_uses_previous_function_evaluation(monkeypatch, tmp_path, version):
    body = """previous(float value)=>value[1]
x=if bar_index%2==0
    previous(close)
else
    na
if bar_index==2 and x==100
    strategy.entry("history",strategy.long,qty=2)
"""
    case, bars = prepare(body, ROWS, version=version)
    result, tape = compare_modes(monkeypatch, tmp_path, case, bars)
    assert [(e["command_id"], e["bar_index"]) for e in tape] == [("history", 2)]
    assert [(t.entry_price, t.qty) for t in result.open_trades] == [(103, 2)]


@pytest.mark.parametrize("version", [4, 5, 6])
def test_missing_boolean_branch_and_history_are_version_exact(monkeypatch, tmp_path, version):
    body = """bool condition=if close>101
    true
if bar_index==0 and not condition
    strategy.entry("false",strategy.long,qty=1)
"""
    case, bars = prepare(body, ROWS, version=version)
    result, tape = compare_modes(monkeypatch, tmp_path, case, bars)
    assert [(e["command_id"], e["bar_index"]) for e in tape] == (
        [("false", 0)] if version == 6 else []
    )
    assert len(result.open_trades) == (1 if version == 6 else 0)


@pytest.mark.parametrize("version", [5, 6])
def test_function_final_conditional_and_dynamic_loop_end(monkeypatch, tmp_path, version):
    body = """quantity(int value)=>
    if value>0
        value
    else
        0
int total=0
int bound=1
for i=0 to bound
    total+=1
    bound:=2
if bar_index==0
    strategy.entry("range",strategy.long,qty=quantity(total))
"""
    case, bars = prepare(body, ROWS, version=version)
    result, tape = compare_modes(monkeypatch, tmp_path, case, bars)
    assert float(tape[0]["qty"]) == (3 if version == 6 else 2)
    assert result.open_trades[0].qty == (3 if version == 6 else 2)


@pytest.mark.parametrize("cut", [1, 3, 5])
def test_generated_checkpoint_preserves_lexical_state_and_once(cut):
    case, bars = prepare(BODY.replace("    if strategy.position_size==0\n        ", "    "), ROWS)
    whole = make_session(case)
    expected = advance(whole, bars)
    split = make_session(case)
    prefix = advance(split, bars, stop=cut)
    saved = json.loads(json.dumps(split.export_state()))
    resumed = make_session(case)
    resumed.restore_state(saved)
    suffix = advance(resumed, bars, start=cut)
    assert prefix + suffix == expected
    assert resumed.export_state() == whole.export_state()
    assert [(e["command_id"], float(e["qty"])) for e in expected] == [("once", 33)]


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_real_worker_stage2_once_function_history(tmp_path, mode, on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    from openpine.runtime.rc6_worker_runtime import _engine_bar
    from rc6_tests.test_rc6_worker_admission import _manifest

    case, bars = prepare(
        BODY,
        ROWS,
        initial_capital=100000,
        calc_on_order_fills=True,
        process_orders_on_close=on_close,
    )
    compiled, context, config = case
    for name, value in dict(
        execution_context=context,
        admitted_manifest=_manifest(),
        instrument_id=context["instrument_id"],
        generated_artifact=compiled.generated_artifact,
        bar_envelopes=bars,
        run_hash="sha256:" + "1" * 64,
        protocol_artifact_dir=str(tmp_path / "protocol"),
        isolated_protocol=mode,
    ).items():
        setattr(config, name, value)
    output = run_isolated_artifact(
        compiled.python_code.encode(), bars=[_engine_bar(b) for b in bars], config=config, params={}
    )
    assert output["ok"] and output["bars_processed"] == len(bars)
    assert [(e["command_id"], float(e["qty"])) for e in output["intent_tape"]] == [("once", 33)]
    assert [(t.entry_price, t.qty) for t in output["raw_result"].open_trades] == [
        (102 if on_close else 103, 33)
    ]
