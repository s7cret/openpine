"""A result is not successful until the interactive process exits cleanly."""
from __future__ import annotations

import io
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpine.runtime.isolated_worker import InteractiveWorkerSession, IsolatedWorkerError


@pytest.mark.parametrize("outcome", [0, 1, -9, "timeout", "os_error"])
def test_interactive_finalization_verifies_exit_and_cleans_pipes(outcome):
    session = InteractiveWorkerSession.__new__(InteractiveWorkerSession)
    session._closed = False
    session._last_commit = {
        "sequence": 5, "message_id": "commit5", "created_at_utc_ms": 0,
        "body": {"run_id": "run", "state_hash": "state", "broker_projection_hash": "broker"},
    }
    session.protocol = SimpleNamespace(append=Mock(return_value={"kind": "FINALIZE"}))
    session.proc = SimpleNamespace(stdin=io.StringIO(), wait=Mock(return_value=outcome))
    session._write_message = Mock()
    session._kill, session._close_pipes = Mock(), Mock()
    if outcome == "timeout":
        session.proc.wait.side_effect = subprocess.TimeoutExpired("worker", 2)
    elif outcome == "os_error":
        session.proc.wait.side_effect = OSError("process status unavailable")
    if outcome == 0:
        assert session.finalize() == {"kind": "FINALIZE"}
    else:
        with pytest.raises(IsolatedWorkerError):
            session.finalize()
    assert session._closed is True
    assert session.proc.stdin.closed
    session._close_pipes.assert_called_once()
    if isinstance(outcome, str):
        session._kill.assert_called_once()
