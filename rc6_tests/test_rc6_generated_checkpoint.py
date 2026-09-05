"""Exact generated state and command cursors across committed-bar checkpoints."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest
from openpine_contracts import ExecutionEvent
from pinelib.runtime.metadata import BarValues
from pinelib.state.checkpoint import sha
from openpine.runtime.rc6_worker_runtime import _session_from_request
from openpine.runtime.rc6_config import serialize_engine_config
from rc6_tests.test_rc6_requests import ID, case_for, source_rows
from rc6_tests.test_rc6_bulk_execution import execute_bulk


def session(case):
    compiled, context, config = case
    return _session_from_request(
        dict(
            generated_artifact=compiled.generated_artifact,
            execution_context=context,
            source=compiled.python_code,
            engine_config=serialize_engine_config(config, "strict_5x"),
            params={},
        )
    )


def advance(runtime, candles, start=0, stop=None):
    tape = []
    for i in range(start, len(candles) if stop is None else stop):
        b = candles[i]
        values = BarValues(
            **{k: float(b[k]) for k in ("open", "high", "low", "close", "volume")},
            time=b["open_time_utc_ms"],
            time_close=b["close_time_utc_ms"],
        )
        event = ExecutionEvent(
            i,
            i,
            len(candles) - 1,
            len(candles) - 1,
            values.time,
            "HISTORICAL_EVAL",
            False,
            True,
            0,
            0,
            "BAR_CLOSE",
        )
        tape.extend(runtime.execute_callback(values, event, strategy_values={}).intents)
        runtime.finalize_bar(i)
    return tape


def request_case():
    return case_for(
        f'var int n=0\nn:=n+1\nx=request.security("{ID}","5",ta.sma(close,2))\nif x>0 and n>8\n    strategy.order("state",strategy.long,qty=n)'
    )


def test_restored_state_and_intent_suffix_equal_uninterrupted():
    case, candles = request_case()
    whole = session(case)
    expected = advance(whole, candles)
    split = session(case)
    prefix = advance(split, candles, stop=7)
    checkpoint = json.loads(json.dumps(split.export_state()))
    restored = session(case)
    restored.restore_state(checkpoint)
    suffix = advance(restored, candles, start=7)
    assert prefix + suffix == expected
    assert restored.export_state() == whole.export_state()
    assert restored.session.state_hash == whole.session.state_hash
    assert restored.session.semantic_state_hash == whole.session.semantic_state_hash
    assert restored.session.requests.registry.dataset_count == 1


@pytest.mark.parametrize(
    "fault", ["hash", "identity", "counter", "runtime", "cursor", "missing_cursor"]
)
def test_failed_restore_is_atomic(fault):
    case, candles = request_case()
    obj = session(case)
    advance(obj, candles, stop=4)
    saved = obj.export_state()
    bad = deepcopy(saved)
    if fault == "hash":
        bad["intent_sequence"] += 1
    elif fault == "identity":
        bad["identity"]["artifact_hash"] = "sha256:" + "f" * 64
    elif fault == "counter":
        bad["intent_sequence"] = True
    elif fault == "runtime":
        bad["runtime"]["state"]["sequence"] = 999
    elif fault == "cursor":
        bad["last_event"]["bar_index"] = 2
    else:
        bad["last_event"] = None
    if fault != "hash":
        bad["content_hash"] = sha({k: v for k, v in bad.items() if k != "content_hash"})
    with pytest.raises((ValueError, RuntimeError)):
        obj.restore_state(bad)
    assert obj.export_state() == saved


def test_uncommitted_bar_cannot_be_exported_or_overwritten():
    case, candles = request_case()
    obj = session(case)
    empty = obj.export_state()
    b = candles[0]
    values = BarValues(100, 102, 99, 101, 1, b["open_time_utc_ms"], b["close_time_utc_ms"])
    obj.execute_callback(
        values,
        ExecutionEvent(
            0, 0, 14, 14, values.time, "HISTORICAL_EVAL", False, True, 0, 0, "BAR_CLOSE"
        ),
        strategy_values={},
    )
    with pytest.raises(ValueError, match="uncommitted"):
        obj.export_state()
    with pytest.raises(ValueError, match="uncommitted"):
        obj.restore_state(empty)
    obj.finalize_bar(0)
    assert obj.export_state()["last_event"]["bar_index"] == 0


def test_changed_requested_data_cannot_reuse_checkpoint():
    from openpine.runtime.request_data import build_request_manifest

    case, candles = request_case()
    a = session(case)
    advance(a, candles, stop=4)
    cfg = replace(case[2])
    cfg.request_manifest = build_request_manifest(case[1], [source_rows(prices=(11, 21, 31))])
    b = session((case[0], case[1], cfg))
    with pytest.raises(ValueError, match="identity"):
        b.restore_state(a.export_state())
    assert b.session.sequence == -1


def test_bulk_resume_export_contains_real_series_requests_and_counter(monkeypatch):
    case, candles = request_case()
    cfg = replace(case[2], export_resume_state=True)
    cfg.request_manifest = case[2].request_manifest
    result = execute_bulk(monkeypatch, case, config=cfg, bars=candles)
    state = result["raw_result"]["resume_state"]["strategy_state"]
    assert state["schema_id"] == "openpine.rc6.generated_checkpoint.v2"
    assert state["runtime"]["state"]["series"] and state["runtime"]["state"]["requests"]
    assert state["last_event"]["bar_index"] == 14
    assert state["intent_sequence"] == len(result["intent_tape"])
