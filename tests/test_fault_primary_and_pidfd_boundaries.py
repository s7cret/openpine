"""Fault-ledger refusal and real owned pidfds; no simulated sandbox acceptance."""

from copy import deepcopy
import signal
import subprocess
import sys
import time

import pytest

from openpine.verification import protected_qualification as qualification


def _family(fault="timeout"):
    return {
        "controller_pid": 11,
        "command_pid": 22,
        "reason": {
            "timeout": "cancelled",
            "sigint": "command-exited",
            "controller-sigkill": "controller-crashed",
        }[fault],
        "observation_errors": [],
        "surviving_processes": [],
        "cleanup_verified": True,
        "observed_family": [{"pid": 22, "create_time": 1.0, "name": "unit-ledger"}],
        "cleanup_signals": [{"pid": 22, "create_time": 1.0, "signal": int(signal.SIGTERM)}],
        "command_returncode": -signal.SIGINT,
    }


def _validate(family, fault="timeout", receipt=None, returncode=None):
    if returncode is None:
        returncode = -signal.SIGKILL if fault == "controller-sigkill" else 1
    if receipt is None and fault != "controller-sigkill":
        receipt = {"status": "timeout" if fault == "timeout" else "failed"}
    qualification.validate_fault_family(family, fault, 11, 22, returncode, receipt)


@pytest.mark.parametrize("fault", ["timeout", "sigint", "controller-sigkill"])
def test_retained_failure_ledger_is_validated_as_failure_not_success(fault):
    _validate(_family(fault), fault)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("controller_pid", 12, "family-controller-mismatch"),
        ("command_pid", 23, "family-command-mismatch"),
        ("reason", "ordinary-success", "family-reason-mismatch"),
        ("observation_errors", ["unobserved"], "family-observation-errors"),
        ("surviving_processes", [23], "family-survivors"),
        ("cleanup_verified", 1, "family-cleanup-unverified"),
        ("observed_family", [], "family-ledger-invalid"),
        ("observed_family", {}, "family-ledger-invalid"),
        ("cleanup_signals", {}, "family-signals-invalid"),
    ],
)
def test_incomplete_fault_observations_cannot_qualify_cleanup(field, value, code):
    family = _family()
    family[field] = value
    with pytest.raises(qualification.FaultPrimaryError) as caught:
        _validate(family)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "mutation",
    [
        "nonobject",
        "extra",
        "pid-bool",
        "pid-zero",
        "time-bool",
        "time-nan",
        "time-inf",
        "time-overflow",
        "name-empty",
        "duplicate",
        "coordinator-missing",
    ],
)
def test_process_birth_identity_cannot_be_approximated_or_duplicated(mutation):
    family = _family()
    row = family["observed_family"][0]
    if mutation == "nonobject":
        family["observed_family"] = [None]
    elif mutation == "extra":
        row["unchecked"] = "value"
    elif mutation == "pid-bool":
        row["pid"] = True
    elif mutation == "pid-zero":
        row["pid"] = 0
    elif mutation == "time-bool":
        row["create_time"] = True
    elif mutation == "time-nan":
        row["create_time"] = float("nan")
    elif mutation == "time-inf":
        row["create_time"] = float("inf")
    elif mutation == "time-overflow":
        row["create_time"] = 10**1000
    elif mutation == "name-empty":
        row["name"] = ""
    elif mutation == "duplicate":
        family["observed_family"].append(deepcopy(row))
    else:
        row["pid"] = 23
    with pytest.raises(qualification.FaultPrimaryError):
        _validate(family)


@pytest.mark.parametrize(
    "mutation",
    [
        "unbounded",
        "nonobject",
        "extra",
        "signal-bool",
        "signal-zero",
        "unhashable",
        "foreign-birth",
        "duplicate",
    ],
)
def test_cleanup_signals_must_target_unique_retained_birth_identities(mutation):
    family = _family()
    row = family["cleanup_signals"][0]
    if mutation == "unbounded":
        family["cleanup_signals"] *= 3
    elif mutation == "nonobject":
        family["cleanup_signals"] = [None]
    elif mutation == "extra":
        row["extra"] = 0
    elif mutation == "signal-bool":
        row["signal"] = True
    elif mutation == "signal-zero":
        row["signal"] = 0
    elif mutation == "unhashable":
        row["pid"] = []
    elif mutation == "foreign-birth":
        row["create_time"] = 2.0
    else:
        family["cleanup_signals"].append(deepcopy(row))
    with pytest.raises(qualification.FaultPrimaryError):
        _validate(family)


@pytest.mark.parametrize(
    "fault,code,receipt,returncode",
    [
        ("timeout", "fault-exit-mismatch", {"status": "timeout"}, 0),
        ("controller-sigkill", "receipt-unexpected", {}, -signal.SIGKILL),
        ("timeout", "receipt-missing", None, 1),
        ("timeout", "receipt-invalid", [], 1),
        ("timeout", "receipt-status-mismatch", {"status": "completed"}, 1),
        ("sigint", "command-exit-mismatch", {"status": "failed"}, 1),
    ],
)
def test_actual_exit_and_receipt_must_prove_requested_fault(fault, code, receipt, returncode):
    family = _family(fault)
    if code == "command-exit-mismatch":
        family["command_returncode"] = 0
    with pytest.raises(qualification.FaultPrimaryError) as caught:
        qualification.validate_fault_family(family, fault, 11, 22, returncode, receipt)
    assert caught.value.code == code


@pytest.mark.parametrize("kind", ["family", "receipt"])
def test_fault_primaries_missing_or_malformed_keep_closed_category(tmp_path, kind):
    path = tmp_path / "primary.json"
    with pytest.raises(qualification.FaultPrimaryError) as caught:
        qualification.read_fault_primary(path, kind)
    assert caught.value.code == kind + "-missing"
    path.write_bytes(b"not JSON")
    with pytest.raises(qualification.FaultPrimaryError) as caught:
        qualification.read_fault_primary(path, kind)
    assert caught.value.code == kind + "-invalid"
    path.write_text("[]")
    with pytest.raises(qualification.FaultPrimaryError):
        qualification.read_fault_primary(path, kind)
    path.write_text('{"retained":true}')
    assert qualification.read_fault_primary(path, kind) == {"retained": True}


def test_primary_publication_is_immutable_and_cleans_failed_temporary(tmp_path):
    path = tmp_path / "primary.json"
    qualification.write_primary(path, {"value": "original"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        qualification.write_primary(path, {"value": "replacement"})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        qualification.read_json(alias)
    assert qualification.sha256(path).startswith("sha256:")


def test_real_pidfd_signals_only_owned_child_and_observes_exit():
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    neighbour = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    handle = qualification.StableProcess(child.pid)
    try:
        assert handle.alive()
        handle.send(0)
        handle.send(signal.SIGTERM)
        child.wait(timeout=5)
        assert not handle.alive()
        assert neighbour.poll() is None
        handle.close()
        handle.close()
    finally:
        handle.close()
        for process in (child, neighbour):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


@pytest.mark.parametrize("pid", [True, 0, -1, "22", None])
def test_pidfd_never_coerces_unowned_process_identity(pid):
    with pytest.raises(ValueError):
        qualification.StableProcess(pid)


@pytest.mark.parametrize(
    "state,main,members,live",
    [
        ("active", "1", [1], True),
        ("active", "0", [1], False),
        ("active", "not-pid", [1], False),
        ("active", "1", [], False),
        ("inactive", "1", [1], False),
    ],
)
def test_live_unit_requires_manager_and_member_agreement(state, main, members, live):
    observation = {"properties": {"ActiveState": state, "MainPID": main}, "members": members}
    assert qualification.live_unit(observation) is live


@pytest.mark.parametrize(
    "state,members,alive,cleaned",
    [
        ("inactive", [], False, True),
        ("failed", [], False, True),
        ("active", [], False, False),
        ("inactive", [1], False, False),
        ("inactive", [], True, False),
    ],
)
def test_cleanup_does_not_ignore_retained_process_handle(state, members, alive, cleaned):
    class Handle:
        def alive(self):
            return alive

    observation = {"properties": {"ActiveState": state}, "members": members}
    assert qualification.cleaned_unit(observation, [Handle()]) is cleaned


def test_wait_ready_rejects_exit_timeout_and_invalid_hello(tmp_path):
    class Process:
        def poll(self):
            return 1

    path = tmp_path / "ready.json"
    with pytest.raises(ValueError, match="exited before"):
        qualification.wait_ready(path, Process(), time.monotonic() + 1)

    class Pending:
        def poll(self):
            return None

    with pytest.raises(TimeoutError):
        qualification.wait_ready(path, Pending(), time.monotonic() - 1)
    qualification.write_primary(
        path, {"unit": "openpine-worker-" + "a" * 32, "hello": {"kind": "WRONG"}}
    )
    with pytest.raises(ValueError, match="HELLO"):
        qualification.wait_ready(path, Pending(), time.monotonic() + 1)
