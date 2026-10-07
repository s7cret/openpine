"""Complete generated-owner preflight uses the existing portable/receipt owners."""

from copy import deepcopy
import json

import pytest

from backtest_engine import BacktestConfig
from backtest_engine.core.intent_replay import IntentReplayIdentity
from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession
from openpine_contracts import ExecutionEvent
from pinelib import RuntimeLanguageContext
from pinelib.errors import PineRuntimeError
from pinelib.runtime.metadata import BarValues, InstrumentContext, TimeframeContext
from pinelib.state.checkpoint import RuntimeCheckpoint, sha
from pinelib.state.digest import AppendOnlyHistory
from openpine.runtime.generated_checkpoint import JOURNAL_DOMAIN

PRODUCERS = {
    "pine2ast": "eb249402e67199b07e9880fadc14e03fb1651edc",
    "ast2python": "9080ed559e5cd9acbfe1400f284314d2af3f9193",
}
SOURCE = """//@version=6
strategy("prepared checkpoint")
var float missing = na
var array<float> prices = array.new_float(0)
array.push(prices, close)
if na(missing) and array.size(prices) == 3
    strategy.order("checkpoint-order", strategy.long, qty=array.size(prices))
"""


@pytest.fixture(scope="module")
def compiled():
    result = NativeRC6CompilerAdapter().compile(
        SOURCE,
        module_name="run02_prepared_checkpoint",
        source_name="prepared-checkpoint.pine",
        producer_commits=PRODUCERS,
    )
    assert result.success, result.errors
    return result


def session(compiled):
    generated = compiled.generated_artifact
    version = generated["version_context"]
    return RC6GeneratedScriptSession(
        artifact={"generated_artifact": generated, "python_code": compiled.python_code},
        language=RuntimeLanguageContext(
            int(version["pine_version"]),
            str(version["spec_snapshot_ref"]),
            "pine-v6",
            str(generated["target_manifest_hash"]),
            str(version["origin"]),
        ),
        instrument=InstrumentContext(
            "SOLUSDT", "binance:spot:SOLUSDT", "BINANCE", "USDT", "SOL", "UTC", "crypto", 1.0, 1.0
        ),
        timeframe=TimeframeContext.parse("1"),
        identity=IntentReplayIdentity(
            "run02-preflight",
            "prepared-checkpoint",
            sha(PRODUCERS),
            "strict_5x",
            "binance:spot:SOLUSDT:1m",
            "binance:spot:SOLUSDT",
            "1m",
        ),
        producer_commit=PRODUCERS["ast2python"],
        engine_config=BacktestConfig(
            "SOLUSDT",
            "1m",
            0,
            120000,
            mintick=1,
            commission_type="none",
            commission_value=0,
            initial_capital=1000,
            force_close_on_end=False,
            semantic_profile="strict_5x",
        ),
    )


def advance(obj, start, stop):
    tape = []
    for i in range(start, stop):
        price = (100.0, 105.0, 110.0)[i]
        bar = BarValues(price, price, price, price, 1.0, i * 60000, (i + 1) * 60000)
        event = ExecutionEvent(
            i, i, 2, 2, bar.time, "HISTORICAL_EVAL", False, True, 0, 0, "BAR_CLOSE"
        )
        tape.extend(obj.execute_callback(bar, event, strategy_values={}).intents)
        obj.finalize_bar(i)
    return tape


def owned(obj):
    return (
        obj.session,
        obj.execution_cursor,
        obj._callback_receipts,
        obj.session.series,
        obj.session.slots,
        obj.session.references,
        obj.session.requests,
        obj.session.transcript,
    )


def reseal(state, *, runtime=False, receipts=False):
    if runtime:
        original = state["runtime"]
        state["runtime"] = RuntimeCheckpoint.seal(
            original["identity_hash"], original["state"], schema_version=original["schema_version"]
        ).to_dict()
    if receipts:
        state["callback_receipts_identity"] = AppendOnlyHistory(
            JOURNAL_DOMAIN, state["callback_receipts"]
        ).identity()
    state["content_hash"] = sha(
        {key: value for key, value in state.items() if key != "content_hash"}
    )
    return state


def test_successful_preparation_is_detached_and_restore_reuses_it_once(compiled, monkeypatch):
    source = session(compiled)
    assert advance(source, 0, 2) == []
    wire = json.loads(json.dumps(source.export_state()))
    target = session(compiled)
    assert advance(target, 0, 1) == []
    before, identities = target.export_state(), owned(target)
    prepared = target.prepare_restore(wire)
    assert target.export_state() == before and all(
        a is b for a, b in zip(identities, owned(target), strict=True)
    )
    assert prepared.runtime is not target.session
    assert prepared.runtime.checkpoint().to_dict() == wire["runtime"]
    assert prepared.cursor.last.to_dict() == wire["last_event"]
    assert prepared.receipts.identity() == wire["callback_receipts_identity"]
    # Mutating the input envelope after prepare cannot change the admitted owners.
    expected = deepcopy(wire)
    wire["runtime"]["state"]["references"]["objects"][0]["committed"][0] = 999
    wire["callback_receipts"][0]["intent_count"] = 999
    assert prepared.runtime.checkpoint().to_dict() == expected["runtime"]
    assert prepared.receipts[0]["intent_count"] == 0
    original, calls = target.prepare_restore, []

    def counted(state):
        admitted = original(state)
        calls.append(admitted)
        return admitted

    monkeypatch.setattr(target, "prepare_restore", counted)
    target.restore_state(expected)
    assert len(calls) == 1 and target.session is calls[0].runtime
    assert [
        (item["command_id"], item["qty"], item["bar_index"]) for item in advance(target, 2, 3)
    ] == [("checkpoint-order", "3", 2)]
    control = session(compiled)
    advance(control, 0, 3)
    assert target.export_state() == control.export_state()


@pytest.mark.parametrize("operation", ["prepare_restore", "restore_state"])
@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "identity",
        "counter",
        "sequence",
        "late-reference",
        "late-slot",
        "late-transcript",
        "late-receipt",
        "cursor",
        "pending-abort",
    ],
)
def test_early_and_late_failure_preserves_live_generated_owner(compiled, operation, fault):
    obj = session(compiled)
    advance(obj, 0, 2)
    saved, identities = obj.export_state(), owned(obj)
    bad = deepcopy(saved)
    if fault == "hash":
        bad["intent_sequence"] += 1
    elif fault == "identity":
        bad["identity"]["producer_commit"] = PRODUCERS["pine2ast"]
    elif fault == "counter":
        bad["intent_sequence"] = True
    elif fault == "sequence":
        bad["runtime"]["state"]["sequence"] = 999
    elif fault == "late-reference":
        bad["runtime"]["state"]["references"]["objects"][0]["kind"] = "foreign"
    elif fault == "late-slot":
        bad["runtime"]["state"]["slots"][0]["working"]["$pinelib_ref"]["object_id"] = "missing"
    elif fault == "late-transcript":
        bad["runtime"]["state"]["transcript"]["entries"][-1]["state_hash"] = sha("forged")
    elif fault == "late-receipt":
        bad["callback_receipts"][-1]["intent_count"] = True
    elif fault == "cursor":
        bad["last_event"]["bar_index"] = 0
    else:
        bad["runtime"]["state"]["pending_abort"] = {"late": True}
    if fault != "hash":
        reseal(
            bad,
            runtime=fault
            in {"sequence", "late-reference", "late-slot", "late-transcript", "pending-abort"},
            receipts=fault == "late-receipt",
        )
    with pytest.raises((ValueError, PineRuntimeError)):
        getattr(obj, operation)(bad)
    assert obj.export_state() == saved
    assert all(a is b for a, b in zip(identities, owned(obj), strict=True))


@pytest.mark.parametrize("fault", ["cycle", "deep", "nonfinite"])
def test_outer_transport_is_bounded_before_generated_canonical_hash(compiled, fault, monkeypatch):
    import openpine.runtime.generated_checkpoint as owner

    obj = session(compiled)
    advance(obj, 0, 1)
    saved, identities = obj.export_state(), owned(obj)
    bad = deepcopy(saved)
    if fault == "cycle":
        bad["identity"]["late"] = bad
    elif fault == "deep":
        value = 0
        for _ in range(140):
            value = [value]
        bad["identity"]["late"] = value
    else:
        bad["identity"]["late"] = float("inf")

    def forbidden_hash(*args):
        raise AssertionError("unbounded envelope reached the host canonical codec")

    with monkeypatch.context() as guard:
        guard.setattr(owner, "canonical_json", forbidden_hash)
        with pytest.raises(PineRuntimeError):
            obj.prepare_restore(bad)
    assert obj.export_state() == saved
    assert all(a is b for a, b in zip(identities, owned(obj), strict=True))


def test_uncommitted_live_owner_rejects_preparation(compiled):
    obj = session(compiled)
    saved = obj.export_state()
    bar = BarValues(100, 100, 100, 100, 1, 0, 60000)
    event = ExecutionEvent(0, 0, 2, 2, 0, "HISTORICAL_EVAL", False, True, 0, 0, "BAR_CLOSE")
    obj.execute_callback(bar, event, strategy_values={})
    identities = owned(obj)
    with pytest.raises(ValueError, match="uncommitted"):
        obj.prepare_restore(saved)
    assert all(a is b for a, b in zip(identities, owned(obj), strict=True))
