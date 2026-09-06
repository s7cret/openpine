"""Real compiler/runtime/broker/protocol with in-memory IPC; sandbox cases below."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import replace

import pytest
from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES

from backtest_engine import BacktestCallbacks, BacktestEngine
from backtest_engine.core.intent_replay import admit_sealed_intent_tape, apply_live_intents_for_bar
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.run_identity import execution_context_from_admission
from openpine.runtime.isolated_worker import InteractiveWorkerSession
from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.rc6_worker_runtime import (
    RC6InteractiveCallbacks, RC6WorkerProtocol, _engine_bar, _session_from_request,
)
from openpine.runtime.worker_protocol import WorkerProtocolTranscript
from openpine_contracts import ExecutionEvent, validate_worker_protocol_sequence
from pinelib.runtime.metadata import BarValues
from rc6_tests.test_rc6_bulk_execution import bulk_case as _case_fixture, execute_bulk
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar
from rc6_tests.test_rc6_worker_admission import ALL_COMMITS, _deployment, _manifest


@pytest.fixture
def case():
    return _case_fixture.__wrapped__()


def compile_case(case, source):
    compiled = NativeRC6CompilerAdapter().compile(
        source, module_name="lifecycle_review", source_name="lifecycle.pine",
        producer_commits={"pine2ast": ALL_COMMITS["pine2ast"], "ast2python": ALL_COMMITS["ast2python"]},
    )
    assert compiled.success, compiled.errors
    context = execution_context_from_admission(
        _deployment(), _manifest(), run_id="run-review-bulk", strategy_id="strategy-review-bulk",
        artifact={"generated_artifact": compiled.generated_artifact, "python_code": compiled.python_code,
                  "consumer_bundle": compiled.consumer_bundle, "source_map": compiled.source_map,
                  "compile_meta": compiled.compile_meta}, data_snapshot_hash="sha256:" + "f" * 64,
        series_id="binance:spot:SOLUSDT:1m", instrument_id="binance:spot:SOLUSDT",
        exchange="binance", market="spot", symbol="SOLUSDT", timeframe="1m",
        semantic_profile="strict_5x", created_at_utc_ms=0)
    return compiled, context, case[2]


def make_session(case):
    compiled, context, config = case
    return _session_from_request(dict(generated_artifact=compiled.generated_artifact,
        execution_context=context, source=compiled.python_code,
        engine_config=serialize_engine_config(config, "strict_5x"), params={}))


def interactive(case, envelopes, tmp_path):
    compiled, context, config = case
    worker = RC6WorkerProtocol(context)
    session = make_session(case)
    driver = RC6InteractiveCallbacks(session, context)
    parent = InteractiveWorkerSession.__new__(InteractiveWorkerSession)
    parent.protocol = WorkerProtocolTranscript(context)
    parent.protocol_artifact_dir = tmp_path
    parent._last_commit = None
    outgoing, sent = deque(), []

    def write(message):
        sent.append(deepcopy(message))
        worker.accept(message)
        if message["kind"] in {"LOAD_ARTIFACT", "INIT_RUN", "FINALIZE"}:
            return
        outgoing.extend(driver.process(message, worker))

    def read():
        message = outgoing.popleft()
        parent.protocol.accept(message)
        sent.append(deepcopy(message))
        return message

    parent._write_message, parent._read_message = write, read
    hello = worker.append("HELLO", {"worker_id": context["session_id"], "protocol_version": "2.3.0",
                                    "capabilities": list(WORKER_CAPABILITIES)}, 0)
    parent.protocol.accept(hello)
    sent.append(hello)
    write(parent.protocol.append("LOAD_ARTIFACT", dict(
        artifact_hash=compiled.generated_artifact["content_hash"],
        module_hash=compiled.generated_artifact["emitted_module_hash"],
        entrypoint_module=compiled.generated_artifact["entrypoint"]["module"], entrypoint_class="GeneratedScript"),
        created_at_utc_ms=0))
    write(parent.protocol.append("INIT_RUN", dict(
        run_id=context["run_id"], run_hash="sha256:"+"1"*64,
        execution_context_hash=context["content_hash"], execution_context=context,
        semantic_profile="strict_5x", capabilities=list(WORKER_CAPABILITIES)), created_at_utc_ms=0))
    pending, tape, events = {}, [], []

    def protocol_event(event):
        if event["kind"] == "BAR_COMMIT":
            parent.commit_bar(event)
        else:
            events.append(event["execution_event"])
            pending.clear()
            pending.update(parent.evaluate_bar(event) if event["kind"] == "BAR_BEGIN" else parent.evaluate_recalc(event))

    class Strategy:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def run_bar(self, bar, bar_index):
            batch = pending["intents"]
            if batch:
                validated = admit_sealed_intent_tape(batch, sequence_origin=len(tape))
                tape.extend(batch)
                apply_live_intents_for_bar(self.ctx, validated, bar_index, bar_open_time_utc_ms=bar.time)

        def export_state(self):
            return {"test_transport": "in-memory"}

    # Match the production parent: resolve instrument defaults exactly as the worker.
    from openpine.runtime.rc6_config import resolve_engine_config
    broker_config = resolve_engine_config(serialize_engine_config(config, "strict_5x"), context)
    result = BacktestEngine(broker_config).run(Strategy, bars=[_engine_bar(b) for b in envelopes],
        callbacks=BacktestCallbacks(on_protocol_callback=protocol_event), execution_context=context, bar_envelopes=envelopes)
    assert not outgoing
    assert session.execution_cursor.open_bar is None
    body = parent._last_commit["body"]
    write(parent.protocol.append("FINALIZE", dict(run_id=context["run_id"],
        final_sequence=parent._last_commit["sequence"], final_state_hash=body["state_hash"],
        broker_projection_hash=body["broker_projection_hash"],
        last_commit_message_id=parent._last_commit["message_id"],
        last_committed_sequence=parent._last_commit["sequence"]), created_at_utc_ms=0))
    validate_worker_protocol_sequence(sent)
    return result, tape, events, session, sent


@pytest.mark.parametrize("fills", [False, True])
@pytest.mark.parametrize("on_close", [False, True])
def test_bulk_and_interactive_history_flags_and_fills_agree(case, monkeypatch, tmp_path, fills, on_close):
    case = compile_case(case, '''//@version=6
strategy("history")
if bar_index == 0
    strategy.entry("L", strategy.long, qty=1)
if bar_index > 0 and close[1] == 101 and strategy.position_size > 0
    strategy.close("L")
''')
    config = replace(case[2], calc_on_order_fills=fills, process_orders_on_close=on_close,
                     initial_capital=1000, max_recalc_depth=10, end_time=OPENED+3*60_000)
    case = case[0], case[1], config
    rows = [bar(open_time_utc_ms=OPENED+i*60_000, open=101+i, close=101+i, high=102+i, low=100+i) for i in range(4)]
    result, tape, events, session, _ = interactive(case, rows, tmp_path)
    bulk = execute_bulk(monkeypatch, case, bars=rows)
    assert bulk["intent_tape"] == tape
    assert bulk["raw_result"]["final_equity"] == result.final_equity
    assert bulk["raw_result"]["total_trades"] == result.total_trades
    assert len(session.session.series["close"].committed) == 4
    assert all(e["last_bar_index"] == 3 for e in events)
    if fills:
        assert any(e["phase"] == "ORDER_FILL_RECALC" for e in events)
    else:
        assert len(events) == 4


def test_barstate_islast_is_not_true_for_every_interactive_bar(case, monkeypatch, tmp_path):
    case = compile_case(case, '''//@version=6
strategy("last")
if barstate.islast and barstate.islastconfirmedhistory
    strategy.entry("last", strategy.long, qty=1)
''')
    rows = [bar(open_time_utc_ms=OPENED+i*60_000) for i in range(3)]
    _, tape, _, _, _ = interactive(case, rows, tmp_path)
    bulk = execute_bulk(monkeypatch, case, bars=rows)
    assert len(tape) == 1 and tape[0]["bar_index"] == 2
    assert bulk["intent_tape"] == tape


def event(sequence=0, index=0, recalc=0, **kwargs):
    fields = dict(sequence=sequence, bar_index=index, last_bar_index=2, last_historical_bar_index=2,
                  bar_open_time_utc_ms=OPENED+index*60_000, phase="HISTORICAL_EVAL", realtime=False,
                  final_tick=True, tick_index=0, recalc_iteration=recalc, cause="BAR_CLOSE")
    fields.update(kwargs)
    return ExecutionEvent(**fields)


def values(index=0):
    return BarValues(open=100, high=102, low=99, close=101+index,
                     volume=10, time=OPENED+index*60_000, time_close=OPENED+(index+1)*60_000-1)


@pytest.mark.parametrize("fault", ["duplicate", "gap", "time", "bounds", "early_next", "commit"])
def test_callback_cursor_rejects_drift(case, fault):
    session = make_session(case)
    session.execute_callback(values(), event(), strategy_values={})
    if fault == "commit":
        with pytest.raises(ValueError):
            session.finalize_bar(1)
        return
    e = dict(duplicate=event(), gap=event(sequence=2, recalc=1),
             time=event(sequence=1, recalc=1, bar_open_time_utc_ms=OPENED+1),
             bounds=event(sequence=1, recalc=1, last_bar_index=3, last_historical_bar_index=3),
             early_next=event(sequence=1, index=1))[fault]
    with pytest.raises(ValueError):
        session.execute_callback(values(), e, strategy_values={})


def test_realtime_var_and_varip_rollback_and_single_history_commit(case):
    case = compile_case(case, '''//@version=6
strategy("rollback")
var int v=0
varip int t=0
v := v+1
t := t+1
strategy.entry("L",strategy.long,qty=v*10+t)
''')
    session = make_session(case)
    base = dict(index=0, last_bar_index=0, last_historical_bar_index=-1, realtime=True,
                phase="REALTIME_EVAL", cause="TICK", final_tick=False)
    outputs = []
    for tick in range(3):
        e = event(sequence=tick, recalc=tick, **{**base, "tick_index":tick, "final_tick":tick==2})
        outputs.append(session.execute_callback(values(), e, strategy_values={}).intents[0]["qty"])
    session.finalize_bar(0)
    assert outputs == ["11", "12", "13"]
    assert len(session.session.series["close"].committed) == 1


def test_recalc_budget_exhaustion_is_not_bulk_success(case, monkeypatch):
    case = compile_case(case, '''//@version=6
strategy("repeat")
if strategy.position_size == 0
    strategy.entry("L",strategy.long)
else
    strategy.close("L",immediately=true)
''')
    case = case[0], case[1], replace(case[2], calc_on_order_fills=True, process_orders_on_close=True, max_recalc_depth=0)
    with pytest.raises(Exception, match="recalc|recalculation"):
        execute_bulk(monkeypatch, case)


@pytest.mark.parametrize("mode", ["interactive", "bulk_backtest"])
@pytest.mark.parametrize("on_close", [False, True])
def test_isolated_rc6_worker_lifecycle_history_and_fill_recalculation(case, tmp_path, mode, on_close):
    """No sandbox substitution: both actual process transports must succeed."""
    from openpine.runtime.isolated_run import run_isolated_artifact
    compiled, context, original = compile_case(case, '''//@version=6
strategy("sandbox lifecycle")
var int total=0
total := total+1
m = ta.sma(close, 2)
if bar_index == 0
    strategy.entry("L", strategy.long, qty=1)
if bar_index > 0 and m > close[1] and strategy.position_size > 0
    strategy.close("L")
''')
    config = replace(original, end_time=OPENED+3*60_000, calc_on_order_fills=True,
                     process_orders_on_close=on_close, max_recalc_depth=10)
    rows = [bar(open_time_utc_ms=OPENED+i*60_000, open=101+i, close=101+i,
                high=102+i, low=100+i) for i in range(4)]
    for name, value in dict(execution_context=context, admitted_manifest=_manifest(),
            instrument_id=context["instrument_id"], generated_artifact=compiled.generated_artifact,
            bar_envelopes=rows, run_hash="sha256:"+"1"*64,
            protocol_artifact_dir=str(tmp_path / "protocol"), isolated_protocol=mode).items():
        setattr(config, name, value)
    progress = []
    result = run_isolated_artifact(compiled.python_code.encode(), bars=[_engine_bar(row) for row in rows],
                                   config=config, params={}, progress_callback=lambda *x: progress.append(x))
    assert progress[0] == (0, 4) and progress[-1] == (4, 4)
    assert result["bars_processed"] == result["raw_result"].bars_processed == 4
    if mode == "bulk_backtest":
        assert result["result_manifest"]["identity"]["execution_context_hash"] == context["content_hash"]
    assert result["ok"] is True
    assert result["raw_result"].status == "completed"
    assert result["raw_result"].total_trades == 1
    assert not result["raw_result"].open_trades
    # On-close fill recalculation executes the bar-zero condition again.
    # A second entry *intent* is valid; pyramiding must still prevent a second fill.
    expected = [("entry", 0, 0)]
    if on_close:
        expected.append(("entry", 0, 1))
    expected.append(("close", 1, 0))
    assert [(item["kind"], item["bar_index"], item["recalc_iteration"])
            for item in result["intent_tape"]] == expected
    assert len(result["raw_result"].closed_trades) == 1
    trade = result["raw_result"].closed_trades[0]
    assert trade.qty == 1
    assert trade.entry_bar_index == (0 if on_close else 1)
    assert trade.exit_bar_index == 1


@pytest.mark.parametrize("fault", [None, "missing_finalize", "bad_final_reference"])
def test_interactive_stream_requires_final_commit_receipt(case, tmp_path, monkeypatch, fault):
    import io
    import json
    from openpine.runtime.rc6_worker_runtime import run_interactive
    from openpine_contracts import seal_content_hash
    rows = [bar(open_time_utc_ms=OPENED+i*60_000) for i in range(3)]
    _, _, _, _, transcript = interactive(case, rows, tmp_path)
    incoming = [deepcopy(item) for item in transcript
                if item["kind"] not in {"HELLO", "INTENT_BATCH", "RECALC_RESULT"}]
    if fault == "missing_finalize":
        incoming.pop()
    elif fault == "bad_final_reference":
        incoming[-1]["body"]["last_commit_message_id"] = "unrelated-commit"
        incoming[-1] = seal_content_hash(incoming[-1], schema_id="openpine.worker.protocol.v2")
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(item)+"\n" for item in incoming)))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    compiled, context, config = case
    request = dict(generated_artifact=compiled.generated_artifact,
        execution_context=context, source=compiled.python_code,
        engine_config=serialize_engine_config(config, "strict_5x"), params={})
    if fault is None:
        assert run_interactive(request, RC6WorkerProtocol(context)) == 0
    else:
        with pytest.raises(ValueError, match="FINALIZE|finalization"):
            run_interactive(request, RC6WorkerProtocol(context))
