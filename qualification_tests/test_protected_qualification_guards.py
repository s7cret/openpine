"""Pure guard contracts. Doubles here never count as protected runtime evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import signal
import subprocess

import pytest

from openpine.verification import protected_qualification as harness
from openpine.verification.qualification_public import COMPONENTS, project, write_projection

UNIT = "openpine-worker-" + "a" * 32
ALLOWLIST = Path(__file__).resolve().parents[1] / "verification/protected-qualification-public-allowlist.json"
COMMITS = {name: "a" * 40 for name in COMPONENTS}


@pytest.mark.parametrize("unit", ["", "openpine-worker-*", UNIT + ".service", "../" + UNIT, "other-" + "a" * 32])
def test_unit_observation_refuses_broad_or_external_scope(unit):
    with pytest.raises(ValueError):
        harness.unit_name(unit)


@pytest.mark.parametrize("failure", ["manager-unavailable", "unit-listed"])
def test_failed_show_requires_successful_exact_absence_query(monkeypatch, failure):
    calls = []

    def query(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 or failure == "manager-unavailable" else 0,
                                           UNIT + ".service" if len(calls) == 2 and failure == "unit-listed" else "", "")

    monkeypatch.setattr(harness.subprocess, "run", query)
    with pytest.raises(ValueError, match="cannot observe"):
        harness.UnitObserver().snapshot(UNIT)
    assert calls[1][-1] == UNIT + ".service"


def test_unit_absence_is_backed_by_exact_manager_query(monkeypatch):
    calls = []

    def query(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if len(calls) == 1 else 0, "", "")

    monkeypatch.setattr(harness.subprocess, "run", query)
    observed = harness.UnitObserver().snapshot(UNIT)
    assert observed["members"] == [] and observed["properties"]["ActiveState"] == "inactive"
    assert calls[1][-1] == UNIT + ".service"


@pytest.mark.parametrize("text", ["ActiveState=active", "Unknown=1", "ActiveState=active\nActiveState=inactive"])
def test_unit_properties_reject_incomplete_duplicate_unknown_rows(text):
    with pytest.raises(ValueError):
        harness.parse_unit_properties(text)


def family(fault):
    return {"controller_pid": 10, "command_pid": 20,
            "reason": {"timeout": "cancelled", "sigint": "command-exited", "controller-sigkill": "controller-crashed"}[fault],
            "command_returncode": -signal.SIGINT if fault == "sigint" else None,
            "cleanup_verified": True, "surviving_processes": [], "observation_errors": [],
            "observed_family": [{"pid": 20, "create_time": 1.0, "name": "python"}],
            "cleanup_signals": []}


@pytest.mark.parametrize("fault", harness.FAULTS)
def test_fault_acceptance_requires_requested_actual_outcome(fault):
    receipt = None if fault == "controller-sigkill" else {"status": "timeout" if fault == "timeout" else "failed"}
    code = -signal.SIGKILL if fault == "controller-sigkill" else 1
    harness.validate_fault_family(family(fault), fault, 10, 20, code, receipt)
    with pytest.raises(ValueError, match="not proved"):
        harness.validate_fault_family(family(fault), fault, 10, 20, 0, receipt)


def test_actual_guardian_parent_death_signal_reason_is_supported():
    value = family("controller-sigkill")
    value["reason"] = "cancelled"
    harness.validate_fault_family(value, "controller-sigkill", 10, 20, -signal.SIGKILL, None)


@pytest.mark.parametrize("mutation", ["survivor", "wrong-controller", "wrong-reason", "missing-coordinator", "signal-neighbour", "duplicate-identity"])
def test_fault_acceptance_rejects_unproved_or_foreign_observation(mutation):
    value = family("timeout")
    if mutation == "survivor":
        value["surviving_processes"] = [{"pid": 20}]
    elif mutation == "wrong-controller":
        value["controller_pid"] = 11
    elif mutation == "wrong-reason":
        value["reason"] = "command-exited"
    elif mutation == "missing-coordinator":
        value["observed_family"][0]["pid"] = 21
    elif mutation == "signal-neighbour":
        value["cleanup_signals"] = [{"pid": 30, "create_time": 1.0, "signal": signal.SIGTERM}]
    else:
        value["observed_family"] *= 2
    with pytest.raises(ValueError):
        harness.validate_fault_family(value, "timeout", 10, 20, 1, {"status": "timeout"})


def test_primary_publication_refuses_overwrite(tmp_path):
    filename = tmp_path / "primary.json"
    harness.write_primary(filename, {"ok": False})
    with pytest.raises(FileExistsError):
        harness.write_primary(filename, {"ok": True})
    assert json.loads(filename.read_bytes()) == {"ok": False}


def test_persistence_failure_does_not_prevent_exact_startup_unit_disposal(tmp_path, monkeypatch):
    from openpine.runtime import isolated_worker
    stopped, saved = [], []
    original_write = harness.write_primary

    def failed_launch(argv, **kwargs):
        original_write(Path(kwargs["cwd"]) / "allocated-unit.json", {"unit": UNIT, "coordinator_pid": 20})
        raise ValueError("unit-startup fixture failure")

    def write(path, value):
        saved.append(path.name)
        if path.name == "automatic-result.json":
            raise OSError("disk receipt failure")
        original_write(path, value)

    def snapshot(self, unit, known_group=""):
        assert unit == UNIT
        return {"unit": unit, "properties": {"ActiveState": "inactive"}, "members": []}

    monkeypatch.setattr(harness.subprocess, "Popen", failed_launch)
    monkeypatch.setattr(harness, "write_primary", write)
    monkeypatch.setattr(harness.UnitObserver, "snapshot", snapshot)
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", stopped.append)
    result = harness.run_fault(tmp_path / "unused.json", tmp_path / "fault", "interactive", "timeout")
    assert result["ok"] is False and stopped == [UNIT]
    assert (result["case_stage"], result["case_error"], result["neighbour_completion_state"]) == (
        "setup", "failed", "not-reached")
    assert saved == ["automatic-result.json", "forced-disposal.json"]
    disposal = harness.read_json(tmp_path / "fault/forced-disposal.json")
    assert "automatic primary persistence" in disposal["errors"][0]


def rows():
    return [{"placement": p, "mode": mode, "fault": fault, "ok": True,
             "automatic_cleanup": True, "neighbour_survived": True, "neighbour_completed": True,
             "case_stage": "complete", "case_error": "none", "neighbour_completion_state": "observed-true",
             "raw_private_path": "/private/do-not-project", "error": "do-not-project"}
            for p in ("A", "B") for mode in harness.MODES for fault in harness.FAULTS]


def test_projection_never_closes_raw_retention_and_excludes_raw_fields(tmp_path):
    projection = project("a" * 40, COMMITS, rows(), ALLOWLIST)
    assert projection["protected_matrix_passed"] is True
    assert projection["owner_checks_passed"] is False
    assert projection["raw_primaries_durable"] is False
    assert projection["full_qualification_accepted"] is False
    write_projection(tmp_path / "public", projection)
    assert sorted(p.name for p in (tmp_path / "public").iterdir()) == ["projection.json", "projection.sha256"]
    assert "do-not-project" not in (tmp_path / "public/projection.json").read_text()


def test_partial_matrix_cannot_be_green():
    assert project("a" * 40, COMMITS, rows()[:-1], ALLOWLIST)["protected_matrix_passed"] is False


@pytest.mark.parametrize("mutation", ["duplicate", "identity", "boolean", "contradiction", "missing-completion"])
def test_projection_rejects_forged_or_ambiguous_success(mutation):
    values = copy.deepcopy(rows())
    if mutation == "duplicate":
        values[-1] = values[0]
    elif mutation == "identity":
        values[0]["placement"] = "private/path"
    elif mutation == "boolean":
        values[0]["automatic_cleanup"] = 1
    elif mutation == "contradiction":
        values[0]["neighbour_survived"] = False
    else:
        del values[0]["neighbour_completed"]
    with pytest.raises(ValueError):
        project("a" * 40, COMMITS, values, ALLOWLIST)
