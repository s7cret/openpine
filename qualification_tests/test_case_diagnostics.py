"""Closed diagnostic branches only; doubles never qualify protected execution."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openpine.verification import protected_qualification as harness
from openpine.verification import qualification_public as public

ALLOWLIST = Path(__file__).resolve().parents[1] / "verification/protected-qualification-public-allowlist.json"
SECRET = "private-secret-/raw/stdout-PID-12345-traceback"
NATIVE = {"ok": True, "bars": 6, "intent": ["search", 2, 3], "trade": [103, 3]}


def row():
    return {"placement": "A", "mode": "interactive", "fault": "timeout", "ok": False,
            "automatic_cleanup": True, "neighbour_survived": True, "neighbour_completed": False,
            "case_stage": "family-receipt", "case_error": "none", "neighbour_completion_state": "not-reached"}


def projection(value, allowlist=ALLOWLIST):
    return public.project("a" * 40, {name: "a" * 40 for name in public.COMPONENTS},
                          [value], allowlist, stage="matrix-A", error="command-failed")


@pytest.mark.parametrize("branch,expected", [
    ("release", ("neighbour-release", "failed", "not-reached", False)),
    ("wait-timeout", ("neighbour-wait", "timeout", "observed-false", False)),
    ("nonzero-exit", ("neighbour-wait", "nonzero-exit", "observed-false", False)),
    ("missing", ("native-result", "missing-result", "observed-false", False)),
    ("malformed", ("native-result", "invalid-result", "observed-false", False)),
    ("wrong-type", ("native-result", "invalid-result", "observed-false", False)),
    ("extra-field", ("native-result", "invalid-result", "observed-false", False)),
    ("wrong-bars", ("native-result", "invalid-result", "observed-false", False)),
    ("wrong-trade", ("native-result", "invalid-result", "observed-false", False)),
    ("symlink", ("native-result", "invalid-result", "observed-false", False)),
    ("success", ("complete", "none", "observed-true", True)),
])
def test_completion_branches_retain_closed_observations(tmp_path, branch, expected):
    result = row()
    native = tmp_path / "native-result.json"
    if branch == "release":
        (tmp_path / "release").mkdir()
    elif branch == "malformed":
        native.write_text(SECRET)
    elif branch == "wrong-type":
        native.write_text(json.dumps([SECRET]))
    elif branch == "symlink":
        outside = tmp_path / "private"
        outside.write_text(SECRET)
        native.symlink_to(outside)
    elif branch != "missing":
        value = copy.deepcopy(NATIVE)
        if branch == "extra-field":
            value["raw"] = SECRET
        elif branch == "wrong-bars":
            value["bars"] = 5
        elif branch == "wrong-trade":
            value["trade"] = [102, 3]
        native.write_text(json.dumps(value))

    class Neighbour:
        def wait(self, timeout):
            assert timeout == 15
            if branch == "release":
                pytest.fail("release failure must not wait for completion")
            if branch == "wait-timeout":
                raise subprocess.TimeoutExpired(SECRET, timeout)
            return 1 if branch == "nonzero-exit" else 0

    harness._complete_neighbour(Neighbour(), tmp_path, result)
    assert tuple(result[k] for k in ("case_stage", "case_error", "neighbour_completion_state",
                                     "neighbour_completed")) == expected
    result["ok"] = result["neighbour_completed"]
    result.update(error=SECRET, traceback=SECRET, stdout=SECRET, path=SECRET, pid=12345)
    value = projection(result)
    assert value["schema_id"] == "openpine.protected_qualification.public_projection.v2"
    assert set(value["cases"][0]) == public.CASE
    assert SECRET not in json.dumps(value) and "12345" not in json.dumps(value)


def test_unexpected_wait_failure_does_not_claim_observed_false(tmp_path):
    result = row()

    class Neighbour:
        def wait(self, timeout):
            raise RuntimeError(SECRET)

    with pytest.raises(RuntimeError, match="private-secret"):
        harness._complete_neighbour(Neighbour(), tmp_path, result)
    assert result["neighbour_completion_state"] == "unverified"
    assert result["neighbour_completed"] is False
    assert SECRET not in json.dumps(projection(result))


@pytest.mark.parametrize("invalid,expected", [
    ("family", ("family-receipt", "family-observation-errors", "not-reached")),
    ("receipt", ("family-receipt", "receipt-status-mismatch", "not-reached")),
    ("fault-timeout", ("fault", "timeout", "not-reached")),
    ("automatic", ("automatic-observation", "failed", "not-reached")),
    ("wait-interrupted", ("neighbour-wait", "interrupted", "unverified")),
    ("wait-unverified", ("neighbour-wait", "failed", "unverified")),
    ("disposal", ("complete", "disposal-failed", "observed-true")),
])
def test_fault_boundaries_retain_reached_stage_and_completion_state(tmp_path, monkeypatch, invalid, expected):
    from openpine.runtime import isolated_worker

    units = {"affected": "openpine-worker-" + "a" * 32, "neighbour": "openpine-worker-" + "b" * 32}
    stopped, ended = set(), set()
    native_attempts = []

    class Handle:
        def __init__(self, pid):
            self.pid = pid
        def alive(self):
            return self.pid not in ended
        def close(self):
            pass

    class Process:
        def __init__(self, argv, **kwargs):
            self.pid = 10 if Path(kwargs["cwd"]).name == "affected" else 11
            self.gate = os.dup(kwargs["pass_fds"][0])
            folder = Path(kwargs["cwd"]) / "command"
            folder.mkdir()
            family = {"controller_pid": self.pid, "command_pid": 20, "reason": "cancelled",
                      "cleanup_verified": True, "surviving_processes": [], "observation_errors": [],
                      "observed_family": [{"pid": 20, "create_time": 1.0, "name": "python"}],
                      "cleanup_signals": []}
            if invalid == "family":
                family["observation_errors"] = [SECRET]
            harness.write_primary(folder / "process-family.json", family)
            harness.write_primary(folder / "command.json", {"status": "failed" if invalid == "receipt" else "timeout"})
        def wait(self, timeout):
            if self.gate is not None:
                os.close(self.gate)
                self.gate = None
            if self.pid == 10 and timeout == 75 and invalid == "fault-timeout":
                raise subprocess.TimeoutExpired(SECRET, timeout)
            if self.pid == 11 and timeout == 15:
                native_attempts.append(True)
                if invalid == "wait-interrupted":
                    raise KeyboardInterrupt(SECRET)
                if invalid == "wait-unverified":
                    raise RuntimeError(SECRET)
                harness.write_primary(tmp_path / "case/neighbour/native-result.json", NATIVE)
            ended.update({self.pid, 20} if self.pid == 10 else {11, 30})
            return 0 if self.pid == 11 and timeout == 15 else 1

    def snapshot(self, unit, known_group=""):
        pid = 20 if unit == units["affected"] else 30
        if invalid == "automatic" and pid == 20 and pid in ended and unit not in stopped:
            raise ValueError(SECRET)
        active = unit not in stopped and pid not in ended
        return {"unit": unit, "members": [pid] if active else [],
                "properties": {"ActiveState": "active" if active else "inactive",
                               "MainPID": str(pid), "ControlGroup": unit}}

    def stop(unit):
        stopped.add(unit)
        ended.add(20 if unit == units["affected"] else 30)
        if invalid == "disposal" and unit == units["neighbour"]:
            raise OSError(SECRET)

    monkeypatch.setattr(harness.subprocess, "Popen", Process)
    monkeypatch.setattr(harness, "StableProcess", Handle)
    monkeypatch.setattr(harness.UnitObserver, "snapshot", snapshot)
    monkeypatch.setattr(harness, "wait_ready", lambda path, process, deadline: {
        "unit": units[path.parent.name], "coordinator_pid": 20 if process.pid == 10 else 30})
    monkeypatch.setattr(harness.importlib, "import_module", lambda name: SimpleNamespace(
        Process=lambda pid: SimpleNamespace(parents=lambda: [SimpleNamespace(pid=10)])))
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", stop)
    output = tmp_path / "case"
    result = harness.run_fault(tmp_path / "unused-binding", output, "interactive", "timeout")
    if invalid not in {"fault-timeout", "automatic"}:
        assert result["automatic_cleanup"] is True and result["neighbour_survived"] is True
    assert tuple(result[k] for k in ("case_stage", "case_error", "neighbour_completion_state")) == expected
    assert (output / "neighbour/release").exists() == bool(native_attempts)
    assert stopped == set(units.values()) and result["forced_disposal_ok"] == (invalid != "disposal")
    assert result["ok"] is False
    if invalid == "disposal":
        automatic = harness.read_json(output / "automatic-result.json")
        assert automatic["ok"] is True and automatic["case_error"] == "none"
        assert result["neighbour_completed"] is True
    result["placement"] = "A"
    assert SECRET not in json.dumps(projection(result))


@pytest.mark.parametrize("field", sorted(public.CASE_DIAGNOSTICS))
@pytest.mark.parametrize("invalid", [SECRET, None, True, [], ""])
def test_case_enums_reject_arbitrary_values_without_leaking(field, invalid):
    value = row()
    value[field] = invalid
    with pytest.raises(ValueError) as caught:
        projection(value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("field", sorted(public.CASE_DIAGNOSTICS))
def test_missing_diagnostics_are_never_inferred(field):
    value = row()
    del value[field]
    with pytest.raises(ValueError):
        projection(value)


@pytest.mark.parametrize("field,policy_field", [
    ("case_stage", "permitted_case_stages"), ("case_error", "permitted_case_errors"),
    ("neighbour_completion_state", "permitted_neighbour_completion_states")])
def test_allowlist_enum_expansion_is_rejected(tmp_path, field, policy_field):
    policy = json.loads(ALLOWLIST.read_bytes())
    policy[policy_field].append(SECRET)
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(policy))
    with pytest.raises(ValueError):
        projection(row(), path)


@pytest.mark.parametrize("mutation", ["not-reached-true", "observed-true-false", "wrong-stage", "green-error", "negative-none"])
def test_completion_diagnostics_cannot_weaken_success(mutation):
    value = row()
    if mutation == "not-reached-true":
        value["neighbour_completed"] = True
    elif mutation == "observed-true-false":
        value["neighbour_completion_state"] = "observed-true"
    elif mutation == "wrong-stage":
        value.update(case_stage="family-receipt", neighbour_completed=True,
                     neighbour_completion_state="observed-true")
    elif mutation == "green-error":
        value.update(ok=True, neighbour_completed=True, case_stage="complete", case_error="disposal-failed",
                     neighbour_completion_state="observed-true")
    else:
        value.update(neighbour_completed=True, case_stage="complete", neighbour_completion_state="observed-true")
    with pytest.raises(ValueError):
        projection(value)


def test_public_guard_rejects_extra_case_data(tmp_path):
    value = projection(row())
    value["cases"][0]["stdout"] = SECRET
    folder = tmp_path / "public"
    public.write_projection(folder, value)
    with pytest.raises(ValueError, match="field set"):
        public.validate_projection(folder, ALLOWLIST)
