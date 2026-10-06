"""Work packages do not imply acceptance or replace semantic denominators."""

from pathlib import Path
import shutil

import pytest

from openpine.verification.identity import read_json, seal, write_json
from openpine.verification.stage2_remaining import build_stage2_remaining

ROOT = Path(__file__).resolve().parents[1]


def fixture(root):
    for path in (
        "verification/stage2-remaining-matrix.json",
        "verification/stage2-remaining-matrix-lock.json",
        "verification/stages.json",
        "verification/stage2-callable-lock.json",
        "verification/stage2-evidence-plan-lock.json",
        "docs/RC6_LIFECYCLE_SOURCES.json",
        "docs/RC6_REVIEW_36.json",
        "docs/OPENPINE_5_0_REMAINING_SPEC_2026-10-06.md",
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    from copy import deepcopy
    import hashlib
    from rc6_tests.test_rc6_evidence_index import setup
    from openpine.verification.builtins import builtin_evidence_report
    from openpine.verification.evidence_index import build_evidence_index, make_surface_lock

    pins = read_json(root / "docs/RC6_LIFECYCLE_SOURCES.json")
    host, evidence, surface, _, plan = setup(root / "producer")
    second = {**deepcopy(surface["rows"][0]), "pine_version": 5}
    surface = reseal({**surface, "rows": [*surface["rows"], second]})
    write_json(host / "settings.json", {"observed_from_bar": 12})
    settings_hash = hashlib.sha256((host / "settings.json").read_bytes()).hexdigest()
    corpus = read_json(host / "manifest.json")
    corpus["cases"][0]["settings"]["sha256"] = settings_hash
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    assignments = read_json(host / "assignments-lock.json")
    assignments = reseal({**assignments, "corpus_hash": corpus["content_hash"]})
    write_json(host / "assignments-lock.json", assignments)
    plan["groups"][0].update(
        corpus="producer/host/manifest.json",
        corpus_hash=corpus["content_hash"],
        assignment_lock="producer/host/assignments-lock.json",
        assignment_hash=assignments["content_hash"],
    )
    plan = reseal(plan)
    lock = make_surface_lock(surface)
    for folder in (evidence / "reports").glob("*/*"):
        observed = read_json(folder / "observations.json")
        observed["min"].update(source_identity=pins, settings_sha256=settings_hash)
        write_json(folder / "observations.json", observed)
        report = builtin_evidence_report(
            surface,
            host / "manifest.json",
            observed,
            corpus_hash=corpus["content_hash"],
            assignments=read_json(folder / "assignments.json"),
        )
        write_json(folder / "report.json", report)
    write_json(root / "verification/stage2-evidence-plan.json", plan)
    write_json(
        root / "verification/stage2-evidence-plan-lock.json",
        {"plan_hash": plan["content_hash"], "expected_group_paths": 10, "group_count": 1},
    )
    write_json(root / "verification/stage2-callable-lock.json", lock)
    return build_evidence_index(
        surface, lock, plan, host_root=root, evidence_roots=[evidence], source_pins=pins
    )


def reseal(report):
    return seal({k: v for k, v in report.items() if k != "content_hash"})


def test_conditioned_continuation_and_remaining_rows_are_visible(tmp_path):
    report = build_stage2_remaining(tmp_path, fixture(tmp_path))
    assert report["work_item_count"] == 16 and len(report["criteria"]) == 4
    assert report["direct_with_bounded_examples_all_paths"] == 1
    assert len(report["conditioned_horizon_only"]) == 1
    assert len(report["direct_without_all_paths"]) == 1
    assert report["full_stage2_accepted"] is report["tradingview_verified"] is False
    assert report["coverage_is_not_stage_percent"] is True
    assert all(row["status"] == "not_accepted" for row in report["criteria"])
    binding = report["remaining_spec_binding"]
    assert binding["registry_complete"] is True and binding["requirement_count"] == 68
    assert len(binding["unclosed_requirements"]) == 68
    assert binding["full_stage2_accepted"] is binding["full_release_accepted"] is False


@pytest.mark.parametrize("field", ["plan_hash", "lock_hash", "source_pins", "content_hash"])
def test_stale_or_corrupted_index_rejected(tmp_path, field):
    index = fixture(tmp_path)
    if field == "source_pins":
        index[field]["pinelib"] = "0" * 40
    else:
        index[field] = "wrong"
    if field != "content_hash":
        index = reseal(index)
    with pytest.raises(ValueError):
        build_stage2_remaining(tmp_path, index)


@pytest.mark.parametrize("field", ["criteria", "tasks", "items", "source_spec_sha256"])
def test_scope_cannot_be_reduced_by_changing_matrix_alone(tmp_path, field):
    index = fixture(tmp_path)
    file = tmp_path / "verification/stage2-remaining-matrix.json"
    matrix = read_json(file)
    if field == "source_spec_sha256":
        matrix[field] = "wrong"
    else:
        matrix[field] = matrix[field][1:]
    write_json(file, reseal(matrix))
    with pytest.raises(ValueError):
        build_stage2_remaining(tmp_path, index)


def test_failed_index_remains_failed_without_losing_the_matrix(tmp_path):
    index = fixture(tmp_path)
    index["ok"] = False
    result = build_stage2_remaining(tmp_path, reseal(index))
    assert not result["ok"] and result["work_item_count"] == 16


def test_cli_writes_read_only_report(tmp_path):
    from openpine.verification.__main__ import main

    index = fixture(tmp_path)
    write_json(tmp_path / "index.json", index)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = main(
        [
            "stage2-remaining",
            "--host-root",
            str(tmp_path),
            "--builtin-index",
            str(tmp_path / "index.json"),
            "--output",
            str(tmp_path / "result.json"),
        ]
    )
    assert result == 0
    assert before == {p: Path(p).read_bytes() for p in before}
    assert not read_json(tmp_path / "result.json")["full_stage2_accepted"]


@pytest.mark.parametrize("mutation", ["owner", "removed-item", "promoted-status"])
def test_resealed_matrix_and_lock_cannot_rewrite_original_items(tmp_path, mutation):
    index = fixture(tmp_path)
    file = tmp_path / "verification/stage2-remaining-matrix.json"
    matrix = read_json(file)
    if mutation == "owner":
        matrix["items"][0]["owner"] = "openpine"
    elif mutation == "removed-item":
        matrix["items"].pop()
    else:
        matrix["items"][0]["status"] = "accepted"
    matrix = reseal(matrix)
    write_json(file, matrix)
    lock_file = tmp_path / "verification/stage2-remaining-matrix-lock.json"
    lock = read_json(lock_file)
    lock["content_hash"] = matrix["content_hash"]
    lock["ids"] = [row["id"] for row in matrix["items"]]
    write_json(lock_file, lock)
    with pytest.raises(ValueError, match="Stage 2"):
        build_stage2_remaining(tmp_path, index)


def test_remaining_reader_cannot_bypass_deleted_binding(tmp_path):
    index = fixture(tmp_path)
    file = tmp_path / "docs/RC6_REVIEW_36.json"
    ledger = read_json(file)
    del ledger["remaining_spec_binding"]
    write_json(file, ledger)
    with pytest.raises(ValueError, match="missing remaining"):
        build_stage2_remaining(tmp_path, index)


@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize(
    "fault", ["failed", "missing", "empty", "unknown-gap", "missing-provenance"]
)
def test_real_remaining_cli_rejects_failed_missing_and_forged_evidence(tmp_path, fault, diagnostic):
    import os
    from openpine.verification.execution_process import run_logged
    import sys

    index = fixture(tmp_path)
    index.update(
        full_builtin_expected_accepted=False, full_stage2_accepted=False, tradingview_verified=False
    )
    if fault in {"failed", "missing"}:
        index["groups"][0].update(
            status="FAILED" if fault == "failed" else "NOT_RUN",
            reasons=["OBSERVATION_FAILED"] if fault == "failed" else [],
        )
        index.update(ok=True, all_declared_runs_passed=False, passed_group_paths=9)
    else:
        index.update(
            ok=False,
            all_declared_runs_passed=False,
            diagnostic_provisional_ok=True,
            passed_group_paths=0,
            deferred_group_paths=len(index["groups"]),
        )
        for group in index["groups"]:
            group.update(status="TEMPORARY_UNVERIFIED", deferred_cases=["not-in-corpus"])
        index["unresolved_authority_cases"] = [
            {
                "case_id": "not-in-corpus",
                "requirement_id": "BOGUS",
                "authority_status": "UNVERIFIED",
                "provenance": {"path": "missing-file.json", "sha256": "0" * 64},
            }
        ]
        if fault == "empty":
            index.update(groups=[], required_group_paths=0, deferred_group_paths=0)
    inputs = tmp_path / "index.json"
    write_json(inputs, reseal(index))
    before = inputs.read_bytes()
    output = tmp_path / "result.json"
    argv = [
        sys.executable,
        *(["-I"] if sys.flags.isolated else []),
        "-B",
        "-m",
        "openpine.verification",
        "stage2-remaining",
        "--host-root",
        str(tmp_path),
        "--builtin-index",
        str(inputs),
        "--output",
        str(output),
    ]
    if diagnostic:
        argv.append("--diagnostic-provisional")
    result = run_logged(argv, cwd=ROOT, output=tmp_path / "cli", env=dict(os.environ), timeout=60)
    assert result["returncode"] != 0
    assert inputs.read_bytes() == before
    if output.exists():
        report = read_json(output)
        assert report["ok"] is report["diagnostic_provisional_ok"] is False
        assert report["full_stage2_accepted"] is report["tradingview_verified"] is False


def provisional_fixture(root):
    from copy import deepcopy
    import hashlib
    from openpine.verification.builtins import builtin_evidence_report
    from openpine.verification.evidence_index import build_evidence_index
    from openpine.verification.identity import digest

    report = fixture(root)
    host, evidence = root / "producer/host", root / "producer/run"
    corpus = read_json(host / "manifest.json")
    case_ids = ["unresolved-one", "unresolved-two", "unresolved-three"]
    corpus["cases"].extend({**deepcopy(corpus["cases"][0]), "id": name} for name in case_ids)
    corpus = reseal(corpus)
    write_json(host / "manifest.json", corpus)
    assignments = reseal(
        {**read_json(host / "assignments-lock.json"), "corpus_hash": corpus["content_hash"]}
    )
    write_json(host / "assignments-lock.json", assignments)
    plan = read_json(root / "verification/stage2-evidence-plan.json")
    plan["groups"][0].update(
        corpus_hash=corpus["content_hash"], assignment_hash=assignments["content_hash"]
    )
    plan = reseal(plan)
    write_json(root / "verification/stage2-evidence-plan.json", plan)
    write_json(
        root / "verification/stage2-evidence-plan-lock.json",
        {"plan_hash": plan["content_hash"], "expected_group_paths": 10, "group_count": 1},
    )
    proof = root / "verification/fixture-authority.json"
    write_json(proof, {"status": "UNVERIFIED", "scope": "independent synthetic policy fixture"})
    proof_hash = hashlib.sha256(proof.read_bytes()).hexdigest()
    declared = [
        {
            "case_id": name,
            "requirement_id": "BUILTIN-03",
            "authority_status": "UNVERIFIED",
            "provenance": {"path": "verification/fixture-authority.json", "sha256": proof_hash},
        }
        for name in case_ids
    ]
    write_json(
        root / "verification/unresolved-authority.json",
        seal(
            {
                "schema_id": "openpine.unresolved_authority_registry.v1",
                "policy": "diagnostic only",
                "rows": declared,
            }
        ),
    )
    # Reconstruct the tiny surface from its producer rows and reviewed lock.
    lock = read_json(root / "verification/stage2-callable-lock.json")
    from rc6_tests.test_rc6_evidence_index import setup

    _, _, original, _, _ = setup(root / "surface-input")
    surface = reseal(
        {
            **original,
            "rows": [original["rows"][0], {**deepcopy(original["rows"][0]), "pine_version": 5}],
        }
    )
    assert report["surface_hash"] == surface["content_hash"]
    pins = read_json(root / "docs/RC6_LIFECYCLE_SOURCES.json")
    for folder in (evidence / "reports").glob("*/*"):
        observed = read_json(folder / "observations.json")
        for name in case_ids:
            observed[name] = {
                **deepcopy(observed["min"]),
                "events": [{"bar": 0, "value": 999}],
                "semantic_authority": {
                    "classification": "UNVERIFIED",
                    "confirmed": False,
                    "independent_receipt_sha256": proof_hash,
                },
            }
        write_json(folder / "observations.json", observed)
        write_json(
            folder / "report.json",
            builtin_evidence_report(
                surface,
                host / "manifest.json",
                observed,
                corpus_hash=corpus["content_hash"],
                assignments=read_json(folder / "assignments.json"),
            ),
        )
    result = build_evidence_index(
        surface, lock, plan, host_root=root, evidence_roots=[evidence], source_pins=pins
    )
    assert result["diagnostic_provisional_ok"] and not result["ok"]
    assert len(result["unresolved_authority_cases"]) == 3
    assert {digest(r) for r in result["unresolved_authority_cases"]} == {
        digest(r) for r in declared
    }
    return result


@pytest.mark.parametrize(
    "fault", ["valid", "registry", "provenance", "missing-proof", "fourth", "runtime", "compile", "compile-type"]
)
@pytest.mark.parametrize("diagnostic", [False, True])
def test_real_diagnostic_cli_requires_exact_declared_authority(tmp_path, fault, diagnostic):
    import os
    from openpine.verification.execution_process import run_logged
    import sys

    index = provisional_fixture(tmp_path)
    if fault == "registry":
        index["unresolved_authority_cases"][0]["requirement_id"] = "BOGUS"
    elif fault == "provenance":
        index["unresolved_authority_cases"][0]["provenance"]["sha256"] = "0" * 64
    elif fault == "missing-proof":
        (tmp_path / "verification/fixture-authority.json").unlink()
    elif fault == "fourth":
        for group in index["groups"]:
            group["deferred_cases"].append("undeclared-fourth")
    elif fault == "runtime":
        index["groups"][0].update(
            status="FAILED",
            reasons=["OBSERVATION_FAILED"],
            deferred_cases=[],
            unresolved_authority=[],
        )
        index["deferred_group_paths"] -= 1
    elif fault in {"compile", "compile-type"}:
        outcome = index["groups"][0]["unassigned_outcomes"][0]
        assert outcome["status"] == "RUNTIME_MISMATCH"
        assert outcome["authority"] == "UNVERIFIED"
        outcome["first_divergence"] = {
            "path": "$.compile", "expected": True,
            "actual": False if fault == "compile" else 1,
        }
    inputs, output = tmp_path / "index.json", tmp_path / "result.json"
    write_json(inputs, reseal(index))
    before = inputs.read_bytes()
    argv = [
        sys.executable,
        *(["-I"] if sys.flags.isolated else []),
        "-B",
        "-m",
        "openpine.verification",
        "stage2-remaining",
        "--host-root",
        str(tmp_path),
        "--builtin-index",
        str(inputs),
        "--output",
        str(output),
    ]
    if diagnostic:
        argv.append("--diagnostic-provisional")
    result = run_logged(argv, cwd=ROOT, output=tmp_path / "cli", env=dict(os.environ), timeout=60)
    assert result["returncode"] == (0 if fault == "valid" and diagnostic else 1)
    assert inputs.read_bytes() == before
    if output.exists():
        report = read_json(output)
        assert (
            report["ok"]
            is report["full_stage2_accepted"]
            is report["tradingview_verified"]
            is False
        )
        assert report["diagnostic_provisional_ok"] is (fault == "valid")
        assert len(report["unresolved_authority_cases"]) == 3


@pytest.mark.parametrize("actual_compile", [False, 1])
def test_compile_divergence_cannot_be_runtime_authority_debt(tmp_path, actual_compile):
    from copy import deepcopy
    from openpine.verification.conformance import (
        compare_corpus, load_corpus, validate_saved_case_result,
    )

    fixture(tmp_path)
    manifest = tmp_path / "producer/host/manifest.json"
    corpus = load_corpus(manifest)
    case = corpus["cases"][0]
    observed = read_json(next((tmp_path / "producer/run/reports").glob("*/*/observations.json")))
    observed[case["id"]]["compile"] = actual_compile
    result = compare_corpus(manifest, observed, expected_corpus_hash=corpus["content_hash"])
    outcome = result["results"][0]
    assert outcome["status"] == "COMPILE_MISMATCH"
    assert outcome["first_divergence"] == {
        "path": "$.compile", "expected": True, "actual": actual_compile,
    }
    validate_saved_case_result(outcome, case, corpus["profile"])
    forged = {**deepcopy(outcome), "status": "RUNTIME_MISMATCH"}
    with pytest.raises(ValueError, match="category"):
        validate_saved_case_result(forged, case, corpus["profile"])


@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "pass-labels",
        "missing-unassigned",
        "missing-assigned",
        "failed-assigned",
        "row-credit",
        "outcome-divergence",
        "outcome-version",
        "outcome-version-type",
        "outcome-missing-field",
        "outcome-extra-field",
        "outcome-scalar-divergence",
    ],
)
def test_real_saved_index_cli_rejects_relabelled_or_missing_evidence(tmp_path, fault, diagnostic):
    from collections import Counter
    import os
    import sys
    from openpine.verification.execution_process import run_logged

    index = (
        provisional_fixture(tmp_path)
        if fault in {"pass-labels", "missing-unassigned"} or fault.startswith("outcome-")
        else fixture(tmp_path)
    )
    for group in index["groups"]:
        group.update(status="PASS", reasons=[], deferred_cases=[], unresolved_authority=[])
        if fault == "missing-unassigned":
            group["unassigned_outcomes"] = []
        if fault.startswith("outcome-"):
            for outcome in group["unassigned_outcomes"]:
                outcome["status"] = "PASS"
                if fault != "outcome-divergence":
                    outcome["first_divergence"] = None
                if fault == "outcome-version":
                    outcome["pine_version"] = 5
                elif fault == "outcome-version-type":
                    outcome["pine_version"] = 6.0
                elif fault == "outcome-missing-field":
                    outcome.pop("first_divergence")
                elif fault == "outcome-extra-field":
                    outcome["schema_id"] = "foreign-outcome"
                elif fault == "outcome-scalar-divergence":
                    outcome["first_divergence"] = "retained mismatch"
    index.update(
        ok=True,
        all_declared_runs_passed=True,
        diagnostic_provisional_ok=False,
        passed_group_paths=len(index["groups"]),
        deferred_group_paths=0,
        unresolved_authority_cases=[],
    )
    assigned = next(row for row in index["rows"] if row["evidence"])
    if fault == "missing-assigned":
        assigned.update(
            evidence=[], evidence_status="NO_EXAMPLES", passing_paths=[], unique_cases=0
        )
    elif fault == "failed-assigned":
        for item in assigned["evidence"]:
            item.update(status="FAILED", observed_statuses=["RUNTIME_MISMATCH"])
        assigned.update(evidence_status="FAILED", passing_paths=[])
    elif fault == "row-credit":
        untested = next(row for row in index["rows"] if not row["evidence"])
        assert untested["pine_version"] == 5 and untested["evidence_status"] == "NO_EXAMPLES"
        untested["evidence_status"] = "EXAMPLES_ALL_PATHS"
    # Keep the forged aggregate counters internally consistent, so the
    # regression requires validation of the retained evidence itself.
    index["counts"] = dict(Counter(row["evidence_status"] for row in index["rows"]))
    index["direct_with_examples_all_paths"] = sum(
        row["status"] == "RUNTIME_DIRECT" and row["evidence_status"] == "EXAMPLES_ALL_PATHS"
        for row in index["rows"]
    )
    inputs, output = tmp_path / "index.json", tmp_path / "result.json"
    write_json(inputs, reseal(index))
    before = inputs.read_bytes()
    argv = [
        sys.executable,
        *(["-I"] if sys.flags.isolated else []),
        "-B",
        "-m",
        "openpine.verification",
        "stage2-remaining",
        "--host-root",
        str(tmp_path),
        "--builtin-index",
        str(inputs),
        "--output",
        str(output),
    ]
    if diagnostic:
        argv.append("--diagnostic-provisional")
    result = run_logged(argv, cwd=ROOT, output=tmp_path / "cli", env=dict(os.environ), timeout=60)
    assert result["returncode"] == 1
    assert inputs.read_bytes() == before
    if output.exists():
        report = read_json(output)
        assert (
            report["ok"]
            is report["full_stage2_accepted"]
            is report["tradingview_verified"]
            is False
        )
