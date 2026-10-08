"""A later controller launch failure must not be replaced by a wait timeout."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import subprocess

import pytest

from openpine.verification import protected_qualification as harness

SECRET = "original-private-launch-error"


@pytest.mark.parametrize("failure", ["second-spawn", "first-handle"])
def test_launch_exception_survives_without_waiting_on_a_previous_role(tmp_path, monkeypatch, failure):
    from openpine.runtime import isolated_worker

    waited, stopped = [], []
    unit = "openpine-worker-" + "a" * 32

    class Process:
        pid = 10

        def __init__(self, argv, **kwargs):
            if Path(kwargs["cwd"]).name == "neighbour":
                raise OSError(errno.ENOENT, SECRET)
            self.gate = os.dup(kwargs["pass_fds"][0])

        def wait(self, timeout):
            waited.append(timeout)
            if self.gate is not None:
                os.close(self.gate)
                self.gate = None
            if timeout == 3:
                raise subprocess.TimeoutExpired("previous live controller", timeout)
            return 0

    class Handle:
        def __init__(self, pid):
            if failure == "first-handle":
                raise OSError(errno.EMFILE, SECRET)

        def close(self):
            pass

    monkeypatch.setattr(harness.subprocess, "Popen", Process)
    monkeypatch.setattr(harness, "StableProcess", Handle)
    monkeypatch.setattr(harness, "wait_ready", lambda *args: {"unit": unit})
    monkeypatch.setattr(harness.UnitObserver, "snapshot", lambda *args: {
        "members": [], "properties": {"ActiveState": "inactive"}})
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", stopped.append)
    output = tmp_path / "case"
    result = harness.run_fault(tmp_path / "unused-spec", output, "interactive", "timeout")
    assert (result["case_stage"], result["case_error"], result["error_type"]) == ("setup", "failed", "FileNotFoundError" if failure == "second-spawn" else "OSError")
    assert SECRET in result["error"]
    assert waited == ([5] if failure == "second-spawn" else [3, 5])
    assert stopped == ([unit] if failure == "second-spawn" else [])
    assert result["forced_disposal_ok"] is True and result["ok"] is False
    automatic = harness.read_json(output / "automatic-result.json")
    assert automatic["error_type"] == result["error_type"] and SECRET in automatic["error"]
