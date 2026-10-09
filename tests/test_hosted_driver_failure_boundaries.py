"""Orchestration fault contracts with failed synthetic matrices only.

No generated row, receipt or projection here is an installed/hosted acceptance.
"""

from pathlib import Path
import shutil

import pytest

from tests.test_hosted_primary_archive_boundaries import archive_inputs
from openpine.verification import qualification_hosted as driver
from openpine.verification import qualification_public as public
from openpine.verification.identity import read_json, write_json
from openpine.verification.pytest_gate import collection_hash

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver_case(tmp_path, monkeypatch):
    host, work, output, identity = archive_inputs(tmp_path)
    for namespace in ("rc6_tests", "int05_tests"):
        (host / namespace).mkdir()
        (host / namespace / "test_fixture.py").write_text("def test_unit_contract(): pass\n")
    (host / "verification").mkdir()
    shutil.copy2(
        ROOT / "verification/protected-qualification-public-allowlist.json",
        host / "verification/protected-qualification-public-allowlist.json",
    )
    node = "test_fixture.py::test_unit_contract"
    locks = {
        label: {"count": 1, "sha256": collection_hash([node]), "deselected": 0}
        for label in ("int04", "int05-owner-contract", "protected")
    }
    write_json(host / "verification/protected-qualification-inventory.json", locks)
    write_json(
        host / "verification/execution-policy.json",
        {
            "components": {"openpine": {"plugins": ["pytest_asyncio.plugin"]}},
            "stabilization": {"protected-workers": {"nodes": {"openpine": [node]}}},
        },
    )
    candidate = work / "private/candidate.json"
    commits = {name: row["sha"] for name, row in read_json(candidate)["components"].items()}
    state = driver.DiagnosticState(host, work, identity["approved_sha"], False, commits=commits)
    exact = work / "private/candidate-wheelhouse"
    exact.mkdir()
    monkeypatch.setattr(
        driver,
        "_prepare_candidate",
        lambda *args: (work / "private/prepare/bundle", exact, candidate, commits),
    )
    from openpine.verification import execution_ci

    def restore_fixture(bundle, destination):
        destination.mkdir()
        write_json(
            destination / "restored.json",
            {
                "executable": str(destination / "venv/bin/python"),
                "roots": {"openpine": str(destination / "sources/openpine")},
            },
        )

    monkeypatch.setattr(execution_ci, "restore", restore_fixture)
    calls = []

    def failed_matrix_and_owner_contract(argv, cwd, folder, **kwargs):
        calls.append(list(argv))
        if "execute" in argv:
            placement = "A" if folder.name.endswith("A") else "B"
            faults = work / "private" / ("faults-" + placement)
            rows = [
                {
                    "placement": placement,
                    "mode": mode,
                    "fault": fault,
                    "ok": False,
                    "automatic_cleanup": False,
                    "neighbour_survived": False,
                    "neighbour_completed": False,
                    "case_stage": "setup",
                    "case_error": "failed",
                    "neighbour_completion_state": "not-reached",
                    "private_detail": "unit private sentinel",
                }
                for mode in driver.MODES
                for fault in driver.FAULTS
            ]
            write_json(faults / "matrix.json", {"complete": True, "cases": rows})
            raise driver.QualificationFailure("command-failed")
        receipt = next(
            Path(arg.split("=", 1)[1]) for arg in argv if arg.startswith("--verification-output=")
        )
        write_json(
            receipt,
            {
                "ok": True,
                "collect_only": False,
                "deselected": 0,
                "nodeids": [node],
                "reports": {
                    node: [
                        {"when": phase, "outcome": "passed", "xfail": False}
                        for phase in ("setup", "call", "teardown")
                    ]
                },
            },
        )

    monkeypatch.setattr(driver, "call", failed_matrix_and_owner_contract)
    return host, work, identity, state, calls


def test_failed_matrices_never_become_full_qualification_even_with_owner_contracts(driver_case):
    host, work, identity, state, calls = driver_case
    assert driver._prepare_and_run(host, work, identity["approved_sha"], state) == 1
    assert len(state.rows) == 12
    assert all(not row["ok"] and "private_detail" not in row for row in state.rows)
    assert state.owners_passed
    assert state.failure == ("matrix-A", "command-failed")
    value = state.publish(complete=True)
    assert not value["ok"] and not value["protected_matrix_passed"]
    assert not value["raw_primaries_durable"] and not value["full_qualification_accepted"]
    assert len(calls) == 8
    for placement in ("A", "B"):
        binding = read_json(work / "private" / ("binding-" + placement + ".json"))
        assert binding["placement"] == placement
        assert binding["prefix"].endswith("placement-" + placement + "/venv")
        assert binding["source_commits"] == state.commits
    assert read_json(work / "private/outcome.json")["ok"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "ok",
        "collect-only",
        "missing-phase",
        "foreign-node",
        "deselected",
        "missing-receipt",
        "owner-timeout",
    ],
)
def test_owner_failure_cannot_leave_a_success_checkpoint(driver_case, monkeypatch, mutation):
    host, work, identity, state, calls = driver_case
    original = driver.call

    def corrupt_owner(argv, cwd, output, **options):
        original(argv, cwd, output, **options)
        path = next(
            Path(arg.split("=", 1)[1]) for arg in argv if arg.startswith("--verification-output=")
        )
        if mutation == "owner-timeout":
            raise driver.QualificationFailure("timeout")
        if mutation == "missing-receipt":
            path.unlink()
            return
        value = read_json(path)
        if mutation == "ok":
            value["ok"] = False
        elif mutation == "collect-only":
            value["collect_only"] = True
        elif mutation == "missing-phase":
            value["reports"][value["nodeids"][0]].pop()
        elif mutation == "foreign-node":
            value["nodeids"] = ["foreign.py::test_other"]
        else:
            value["deselected"] = 1
        write_json(path, value)

    monkeypatch.setattr(driver, "call", corrupt_owner)
    if mutation in {"foreign-node", "deselected"}:
        with pytest.raises(ValueError, match="inventory changed"):
            driver._prepare_and_run(host, work, identity["approved_sha"], state)
    else:
        assert driver._prepare_and_run(host, work, identity["approved_sha"], state) == 1
    assert not state.owners_passed
    value = state.publish(complete=True)
    assert not value["ok"] and not value["full_qualification_accepted"]
    assert state.failure == ("matrix-A", "command-failed")


@pytest.mark.parametrize(
    "failure",
    [
        ValueError("unit private sentinel"),
        OSError("unit private sentinel"),
        KeyboardInterrupt("unit private sentinel"),
        SystemExit("unit private sentinel"),
    ],
)
def test_driver_exception_is_private_and_public_fallback_stays_closed(
    driver_case, monkeypatch, failure
):
    host, existing, identity, state, calls = driver_case
    work = existing.parent / "new-run"

    def refuse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(driver, "_prepare_and_run", refuse)
    assert driver.prepare_and_run(host, work, identity["approved_sha"], True) == 1
    value = public.validate_projection(
        work / "public", host / "verification/protected-qualification-public-allowlist.json"
    )
    assert (
        not value["ok"]
        and not value["raw_primaries_durable"]
        and not value["full_qualification_accepted"]
    )
    assert "unit private sentinel" not in (work / "public/projection.json").read_text()
    assert "unit private sentinel" in (work / "private/driver-failure.json").read_text()


def test_existing_work_directory_is_preserved_without_new_diagnostics(driver_case):
    host, work, identity, state, calls = driver_case
    before = {p.relative_to(work): p.read_bytes() for p in work.rglob("*") if p.is_file()}
    assert driver.prepare_and_run(host, work, identity["approved_sha"], False) == 1
    assert before == {p.relative_to(work): p.read_bytes() for p in work.rglob("*") if p.is_file()}


def test_preflight_cannot_be_published_as_protected_acceptance(driver_case):
    host, existing, identity, state, calls = driver_case
    work = existing.parent / "preflight-run"
    assert driver.prepare_and_run(host, work, identity["approved_sha"], True, preflight=True) == 1
    value = public.validate_projection(
        work / "public", host / "verification/protected-qualification-public-allowlist.json"
    )
    assert (
        not value["ok"]
        and not value["protected_matrix_passed"]
        and not value["full_qualification_accepted"]
    )
