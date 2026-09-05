"""Checkpoint cursors are cross-checked, not merely trusted after outer rehashing."""

from copy import deepcopy
import json

import pytest

from openpine_contracts import ExecutionEvent
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import sha
from pinelib.state.digest import AppendOnlyHistory
from openpine.runtime.generated_checkpoint import JOURNAL_DOMAIN
from rc6_tests.test_rc6_generated_checkpoint import advance, request_case, session
from rc6_tests.test_rc6_requests import case_for


def reseal(state, *, receipts=False):
    if receipts:
        state["callback_receipts_identity"] = AppendOnlyHistory(
            JOURNAL_DOMAIN, state["callback_receipts"]
        ).identity()
    state["content_hash"] = sha({k: v for k, v in state.items() if k != "content_hash"})
    return state


@pytest.fixture
def checkpoint():
    case, bars = request_case()
    runtime = session(case)
    advance(runtime, bars, stop=10)
    return case, runtime.export_state()


@pytest.mark.parametrize(
    "field,value",
    [
        ("intent_sequence", 999),
        ("intent_sequence", 0),
        ("bar_open_time_utc_ms", 0),
        ("tick_index", 4),
        ("recalc_iteration", 2),
        ("last_bar_index", 999),
        ("last_historical_bar_index", 999),
    ],
)
def test_rehashed_outer_cursor_cannot_override_saved_callback(checkpoint, field, value):
    case, saved = checkpoint
    damaged = deepcopy(saved)
    if field == "intent_sequence":
        damaged[field] = value
    else:
        damaged["last_event"][field] = value
        if field == "last_historical_bar_index":
            damaged["last_event"]["last_bar_index"] = value
    target = session(case)
    before = target.export_state()
    with pytest.raises(ValueError):
        target.restore_state(reseal(damaged))
    assert target.export_state() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("bar_open_time_utc_ms", 0),
        ("tick_index", 4),
        ("recalc_iteration", 2),
        ("last_bar_index", 999),
    ],
)
def test_rehashed_receipt_still_must_match_runtime_and_causal_order(checkpoint, field, value):
    case, saved = checkpoint
    damaged = deepcopy(saved)
    damaged["callback_receipts"][-1]["event"][field] = value
    damaged["last_event"][field] = value
    with pytest.raises(ValueError):
        session(case).restore_state(reseal(damaged, receipts=True))


@pytest.mark.parametrize(
    "fault",
    ["missing", "extra", "reordered", "sequence", "bool_count", "negative_count", "bad_hash"],
)
def test_receipt_structure_is_validated_even_after_rehash(checkpoint, fault):
    case, saved = checkpoint
    state = deepcopy(saved)
    rows = state["callback_receipts"]
    if fault == "missing":
        rows.pop(0)
    elif fault == "extra":
        rows.append(deepcopy(rows[-1]))
    elif fault == "reordered":
        rows[0], rows[1] = rows[1], rows[0]
    elif fault == "sequence":
        rows[-1]["runtime_sequence"] += 1
    elif fault == "bool_count":
        rows[-1]["intent_count"] = True
    elif fault == "negative_count":
        rows[-1]["intent_count"] = -1
    else:
        rows[-1]["intent_batch_hash"] = "not-a-hash"
    with pytest.raises(ValueError):
        session(case).restore_state(reseal(state, receipts=True))


def test_receipt_reads_do_not_mutate_committed_history(checkpoint):
    case, saved = checkpoint
    runtime = session(case)
    runtime.restore_state(saved)
    exported = runtime.export_state()
    exported["callback_receipts"][0]["intent_count"] = 100
    assert runtime.export_state() == saved


def test_old_checkpoint_without_receipts_is_not_silently_accepted(checkpoint):
    case, saved = checkpoint
    state = deepcopy(saved)
    state["schema_id"] = "openpine.rc6.generated_checkpoint.v1"
    del state["callback_receipts"]
    del state["callback_receipts_identity"]
    with pytest.raises(ValueError, match="schema"):
        session(case).restore_state(reseal(state))


def test_restoration_at_fill_recalculation_boundary_preserves_contiguous_intents():
    case, bars = case_for("strategy.cancel_all()", count=3)
    whole = session(case)
    b = bars[0]
    values = BarValues(
        **{k: float(b[k]) for k in ("open", "high", "low", "close", "volume")},
        time=b["open_time_utc_ms"],
        time_close=b["close_time_utc_ms"],
    )
    initial = ExecutionEvent(
        0, 0, 2, 2, values.time, "HISTORICAL_EVAL", False, True, 0, 0, "BAR_CLOSE"
    )
    after_fill = ExecutionEvent(
        1, 0, 2, 2, values.time, "ORDER_FILL_RECALC", False, True, 0, 1, "ORDER_FILL", "fill", "100"
    )
    first = whole.execute_callback(values, initial, strategy_values={})
    second = whole.execute_callback(values, after_fill, strategy_values={})
    whole.finalize_bar(0)
    restored = session(case)
    restored.restore_state(json.loads(json.dumps(whole.export_state())))
    assert restored._intent_sequence == 2
    assert [first.intents[0]["sequence"], second.intents[0]["sequence"]] == [0, 1]
    b = bars[1]
    values = BarValues(100, 102, 99, 101, 1, b["open_time_utc_ms"], b["close_time_utc_ms"])
    event = ExecutionEvent(
        2, 1, 2, 2, values.time, "HISTORICAL_EVAL", False, True, 0, 0, "BAR_CLOSE"
    )
    assert (
        restored.execute_callback(values, event, strategy_values={}).intents
        == whole.execute_callback(values, event, strategy_values={}).intents
    )
    restored.finalize_bar(1)
    whole.finalize_bar(1)
    assert restored.export_state() == whole.export_state()
