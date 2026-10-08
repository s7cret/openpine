"""Strict private primary checks expose only fixed public family error codes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import signal

import pytest

from openpine.verification import protected_qualification as harness
from openpine.verification import qualification_public as public

ALLOWLIST = Path(__file__).resolve().parents[1] / "verification/protected-qualification-public-allowlist.json"
SECRET = "private-/raw/path-stdout-traceback-PID-12345"


def primary(fault="timeout"):
    family = {"controller_pid": 70, "command_pid": 80,
              "reason": {"timeout": "cancelled", "controller-sigkill": "cancelled",
                         "sigint": "command-exited"}[fault],
              "cleanup_verified": True, "surviving_processes": [], "observation_errors": [],
              "observed_family": [{"pid": 80, "create_time": 1.0, "name": SECRET}],
              "cleanup_signals": [], "command_returncode": -signal.SIGINT}
    receipt = None if fault == "controller-sigkill" else {"status": "timeout" if fault == "timeout" else "failed"}
    return family, -signal.SIGKILL if fault == "controller-sigkill" else 1, receipt


def projected(code, *, stage="family-receipt", completion="not-reached", fault=None):
    if fault is None:
        fault = {"receipt-unexpected": "controller-sigkill", "command-exit-mismatch": "sigint"}.get(code, "timeout")
    row = {"placement": "A", "mode": "interactive", "fault": fault, "ok": False,
           "automatic_cleanup": True, "neighbour_survived": True,
           "neighbour_completed": completion == "observed-true", "case_stage": stage,
           "case_error": code, "neighbour_completion_state": completion,
           "error": SECRET, "stdout": SECRET, "path": SECRET, "pid": 12345}
    return public.project("a" * 40, {n: "a" * 40 for n in public.COMPONENTS}, [row],
                          ALLOWLIST, stage="matrix-A", error="command-failed")


@pytest.mark.parametrize("fault", harness.FAULTS)
def test_all_actual_fault_receipt_contracts_remain_accepted(fault):
    family, code, receipt = primary(fault)
    harness.validate_fault_family(family, fault, 70, 80, code, receipt)
    if fault == "sigint":
        family["command_returncode"] = 128 + signal.SIGINT
        harness.validate_fault_family(family, fault, 70, 80, code, receipt)


@pytest.mark.parametrize("mutation,expected", [
    ("controller", "family-controller-mismatch"),
    ("command", "family-command-mismatch"),
    ("reason", "family-reason-mismatch"),
    ("errors", "family-observation-errors"),
    ("survivors", "family-survivors"),
    ("cleanup", "family-cleanup-unverified"),
    ("ledger", "family-ledger-invalid"),
    ("identity", "family-identity-invalid"),
    ("duplicate-identity", "family-identity-duplicate"),
    ("coordinator", "family-coordinator-missing"),
    ("signals", "family-signals-invalid"),
    ("signal", "family-signal-invalid"),
    ("foreign-signal", "family-signal-foreign"),
    ("duplicate-signal", "family-signal-duplicate"),
    ("exit", "fault-exit-mismatch"),
    ("missing-receipt", "receipt-missing"),
    ("invalid-receipt", "receipt-invalid"),
    ("receipt-status", "receipt-status-mismatch"),
    ("unexpected-receipt", "receipt-unexpected"),
    ("command-exit", "command-exit-mismatch"),
])
def test_each_mandatory_primary_rejection_has_a_fixed_code(mutation, expected):
    fault = "controller-sigkill" if mutation == "unexpected-receipt" else "sigint" if mutation == "command-exit" else "timeout"
    family, code, receipt = primary(fault)
    if mutation in {"controller", "command"}:
        family[mutation + "_pid"] = 90
    elif mutation == "reason":
        family["reason"] = SECRET
    elif mutation == "errors":
        family.update(observation_errors=[SECRET], cleanup_verified=False)
    elif mutation == "survivors":
        family.update(surviving_processes=[{"pid": 80, "status": SECRET}], cleanup_verified=False)
    elif mutation == "cleanup":
        family["cleanup_verified"] = False
    elif mutation == "ledger":
        family["observed_family"] = []
    elif mutation == "identity":
        family["observed_family"][0]["create_time"] = float("nan")
    elif mutation == "duplicate-identity":
        family["observed_family"] *= 2
    elif mutation == "coordinator":
        family["observed_family"][0]["pid"] = 90
    elif mutation == "signals":
        family["cleanup_signals"] = None
    elif mutation == "signal":
        family["cleanup_signals"] = [{"pid": 80, "create_time": 1.0, "signal": SECRET}]
    elif mutation in {"foreign-signal", "duplicate-signal"}:
        family["cleanup_signals"] = [{"pid": 90 if mutation == "foreign-signal" else 80,
                                      "create_time": 1.0, "signal": int(signal.SIGTERM)}]
        if mutation == "duplicate-signal":
            family["cleanup_signals"] *= 2
    elif mutation == "exit":
        code = 0
    elif mutation == "missing-receipt":
        receipt = None
    elif mutation == "invalid-receipt":
        receipt = [SECRET]
    elif mutation in {"receipt-status", "unexpected-receipt"}:
        receipt = {"status": SECRET}
    elif mutation == "command-exit":
        family["command_returncode"] = 0
    before = copy.deepcopy(family)
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.validate_fault_family(family, fault, 70, 80, code, receipt)
    assert caught.value.code == expected
    # No primary is repaired to justify acceptance after a negative check.
    assert json.dumps(family, sort_keys=True) == json.dumps(before, sort_keys=True)
    value = projected(caught.value.code)
    assert SECRET not in json.dumps(value) and "12345" not in json.dumps(value)


@pytest.mark.parametrize("kind", ["family", "receipt"])
@pytest.mark.parametrize("failure", ["missing", "json", "type", "symlink", "oversize"])
def test_primary_io_failures_use_fixed_codes_without_paths(tmp_path, kind, failure):
    path = tmp_path / SECRET.replace("/", "_")
    if failure == "json":
        path.write_text(SECRET)
    elif failure == "type":
        path.write_text(json.dumps([SECRET]))
    elif failure == "symlink":
        target = tmp_path / "private"
        target.write_text(json.dumps({"private": SECRET}))
        path.symlink_to(target)
    elif failure == "oversize":
        path.write_bytes(b" " * (harness.MAX_JSON + 1))
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.read_fault_primary(path, kind)
    assert caught.value.code == kind + ("-missing" if failure == "missing" else "-invalid")
    assert SECRET not in json.dumps(projected(caught.value.code))


@pytest.mark.parametrize("field,value", [("create_time", 10**1000),
                                        ("create_time", float("inf")),
                                        ("pid", True), ("name", "")])
def test_invalid_identity_values_do_not_fall_back_to_generic_failure(field, value):
    family, code, receipt = primary()
    family["observed_family"][0][field] = value
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.validate_fault_family(family, "timeout", 70, 80, code, receipt)
    assert caught.value.code == "family-identity-invalid"


@pytest.mark.parametrize("code", sorted(public.FAMILY_ERRORS))
def test_all_closed_family_codes_roundtrip_without_private_context(tmp_path, code):
    error = harness.FaultPrimaryError(code, SECRET)
    value = projected(error.code)
    assert value["cases"][0]["case_error"] == code
    assert set(value["cases"][0]) == public.CASE
    assert value["protected_matrix_passed"] is False and value["ok"] is False
    assert SECRET not in json.dumps(value) and "12345" not in json.dumps(value)
    output = tmp_path / "public"
    public.write_projection(output, value)
    assert public.validate_projection(output, ALLOWLIST) == value
    assert {p.name for p in output.iterdir()} == {"projection.json", "projection.sha256"}


@pytest.mark.parametrize("code", sorted(public.FAMILY_ERRORS))
@pytest.mark.parametrize("stage,completion", [("fault", "not-reached"), ("neighbour-wait", "observed-false")])
def test_family_codes_cannot_claim_another_reached_stage(code, stage, completion):
    with pytest.raises(ValueError, match="reached stage"):
        projected(code, stage=stage, completion=completion)


def test_arbitrary_primary_error_text_and_allowlist_expansion_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="closed vocabulary"):
        harness.FaultPrimaryError(SECRET, SECRET)
    with pytest.raises(ValueError, match="closed vocabulary"):
        projected(SECRET)
    policy = json.loads(ALLOWLIST.read_bytes())
    policy["permitted_case_errors"].append(SECRET)
    expanded = tmp_path / "policy.json"
    expanded.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="closed contract"):
        public.project("a" * 40, {n: "a" * 40 for n in public.COMPONENTS}, [], expanded)


@pytest.mark.parametrize("code,fault", [("receipt-unexpected", "timeout"),
                                        ("receipt-unexpected", "sigint"),
                                        ("receipt-status-mismatch", "controller-sigkill"),
                                        ("command-exit-mismatch", "timeout")])
def test_primary_diagnostics_cannot_contradict_the_requested_fault(code, fault):
    with pytest.raises(ValueError, match="requested fault"):
        projected(code, fault=fault)


@pytest.mark.parametrize("which", ["identities", "signals"])
def test_private_family_ledger_bounds_are_unchanged(which):
    family, code, receipt = primary()
    if which == "identities":
        family["observed_family"] *= 4097
        expected = "family-ledger-invalid"
    else:
        family["cleanup_signals"] = [{"pid": 80, "create_time": 1.0, "signal": signal.SIGTERM}] * 3
        expected = "family-signals-invalid"
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.validate_fault_family(family, "timeout", 70, 80, code, receipt)
    assert caught.value.code == expected
