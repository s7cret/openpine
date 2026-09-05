"""OP-06: failed/truncated results and worker exits cannot become success."""
from __future__ import annotations

import io
import subprocess
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpine.runtime.bulk_worker import BulkWorkerSession
from openpine.runtime.isolated_worker import IsolatedWorkerError
from rc6_tests.test_rc6_bulk_execution import bulk_case as _bulk_case_fixture
from rc6_tests.test_rc6_bulk_execution import execute_bulk
from rc6_tests.test_rc6_marketdata_boundary import OPENED, bar


@pytest.fixture
def bulk_case():
    return _bulk_case_fixture.__wrapped__()


def parent_session(payload, context):
    session = BulkWorkerSession.__new__(BulkWorkerSession)
    session.timeout_s = 5
    session.bulk_idle_timeout_s = 60
    session.protocol = SimpleNamespace(execution_context=context)
    session._write_json_line = Mock()
    session._read_message = Mock(return_value=payload)
    return session


@pytest.mark.parametrize("fault", ["failed", "early_stopped", "missing_status", "received", "bool_count", "negative_count", "intent_hash"])
def test_parent_rejects_invalid_result(monkeypatch, bulk_case, fault):
    payload = deepcopy(execute_bulk(monkeypatch, bulk_case))
    if fault in {"failed", "early_stopped"}:
        payload["raw_result"]["status"] = fault
    elif fault == "missing_status":
        payload["raw_result"].pop("status")
    elif fault == "received":
        payload["bars_received"] = 2
    elif fault == "bool_count":
        payload["bars_processed"] = True
    elif fault == "negative_count":
        payload["bars_processed"] = -1
    elif fault == "intent_hash":
        payload["intent_tape"][0]["qty"] = "123"
    session = parent_session(payload, bulk_case[1])
    with pytest.raises(IsolatedWorkerError):
        session.run_bars([bar(open_time_utc_ms=OPENED + i * 60_000) for i in range(3)], {})
    assert session.timeout_s == 5


def test_parent_accepts_actual_sealed_intents(monkeypatch, bulk_case):
    payload = execute_bulk(monkeypatch, bulk_case)
    session = parent_session(payload, bulk_case[1])
    result = session.run_bars([bar(open_time_utc_ms=OPENED + i * 60_000) for i in range(3)], {})
    assert result["ok"] is True
    assert result["bars_processed"] == 3
    assert len(result["intent_tape"]) == 1
    assert result["raw_result"].status == "completed"


@pytest.mark.parametrize("exit_result", [0, 1, -9, "timeout"])
def test_bulk_result_requires_verified_zero_worker_exit(exit_result):
    session = BulkWorkerSession.__new__(BulkWorkerSession)
    session._closed = False
    session.proc = SimpleNamespace(stdin=io.StringIO(), wait=Mock(return_value=exit_result))
    session._kill = Mock()
    session._close_pipes = Mock()
    if exit_result == "timeout":
        session.proc.wait.side_effect = subprocess.TimeoutExpired("worker", 2)
    if exit_result == 0:
        session.__exit__(None, None, None)
    else:
        with pytest.raises(IsolatedWorkerError):
            session.__exit__(None, None, None)
    assert session._closed is True
    session._close_pipes.assert_called_once()
    if exit_result == "timeout":
        session._kill.assert_called_once()


def test_worker_rejects_input_eof_before_final_batch(monkeypatch, bulk_case):
    with pytest.raises(ValueError, match="before its final batch"):
        execute_bulk(monkeypatch, bulk_case, last_batch=False)
