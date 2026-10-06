"""Validate review-accounting consistency, not semantic or TradingView acceptance."""

import json
import copy
import os
from pathlib import Path
import re

import pytest

from openpine.verification.identity import digest, read_json
from openpine.verification.review_ledger import (
    SOURCE_PATH,
    normative_contract,
    read_review_ledger,
    validate_reference_paths,
    validate_review_ledger,
)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = json.loads((ROOT / "docs/RC6_REVIEW_36.json").read_text())


def test_all_original_review_ids_exist_exactly_once():
    tasks = LEDGER["tasks"]
    assert [row["id"] for row in tasks] == [f"OP-{i:02d}" for i in range(1, 37)]
    assert LEDGER["schema"] == "openpine.review.acceptance.v1"
    assert re.fullmatch("[0-9a-f]{64}", LEDGER["source_spec_sha256"])
    assert re.fullmatch("[0-9a-f]{40}", LEDGER["snapshot_base"])


@pytest.mark.parametrize("record", LEDGER["tasks"], ids=lambda row: row["id"])
def test_task_status_preserves_remaining_work_and_existing_evidence(record):
    assert record["title"].strip() and record["implemented"].strip()
    assert record["status"] in {"accepted", "partial", "unverified"}
    if record["status"] == "accepted":
        assert not record["remaining"].strip()
        assert record["evidence_paths"]
    else:
        assert record["remaining"].strip()
    for path in record["evidence_paths"]:
        assert not Path(path).is_absolute() and ".." not in Path(path).parts
        assert (ROOT / path).is_file(), path
    assert f"**{record['id']}**" in (ROOT / "docs/RC6_REVIEW_36.md").read_text()


def test_complete_remaining_registry_preserves_history_without_accepting_product():
    matrix = read_json(ROOT / "verification/stage2-remaining-matrix.json")
    report = read_review_ledger(ROOT, matrix)
    assert (report["requirement_count"], report["op_count"], report["stage2_item_count"]) == (
        68,
        36,
        16,
    )
    assert report["registry_complete"] is True
    assert report["status_counts"] == {
        "partial": 44,
        "toqualify": 15,
        "toimplement": 8,
        "blocked": 1,
    }
    assert len(report["unclosed_requirements"]) == 68
    assert report["full_stage2_accepted"] is report["full_release_accepted"] is False
    assert LEDGER["tasks"][-1]["id"] == "OP-36"
    assert LEDGER["tasks"][-1]["status"] == "accepted"
    assert LEDGER["source_spec_sha256"] != report["source_sha256"]


def test_import_denominator_and_ordered_types_match_complete_source_headings():
    lines = (ROOT / SOURCE_PATH).read_text().splitlines()
    headings = {}
    for number, line in enumerate(lines, 1):
        match = re.match(r"### ([A-Z]+-[0-9]{2})\. (.+) — ([ДИК](?: \+ [ДИК])*)$", line)
        if match:
            headings[match[1]] = (number, match[2], match[3].split(" + "))
    rows = LEDGER["remaining_spec_binding"]["requirements"]
    assert len(headings) == 68 and list(headings) == [row["id"] for row in rows]
    for row in rows:
        assert headings[row["id"]] == (row["source"]["start_line"], row["title"], row["work_type"])
        assert row["source"]["start_line"] <= row["source"]["end_line"] <= len(lines)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-binding",
        "missing-requirement",
        "duplicate-requirement",
        "unknown-id",
        "source-hash",
        "source-range",
        "work-type",
        "owner",
        "repo",
        "consumer",
        "OP-mapping",
        "OP-inverse",
        "Stage2-link",
        "Stage2-owner",
        "old-OP-status",
        "old-OP-evidence",
        "old-schema",
        "old-source-hash",
        "mandatory-3.12",
        "free-threading",
        "disabled-GIL",
        "accepted-summary",
        "false-done",
        "invented-nodeid",
        "invented-identity",
        "invalid-reference",
        "empty-reason",
        "missing-policy",
        "missing-basis",
        "fabricated-partial-receipt",
        "fabricated-raw-receipt",
        "accepted-basis",
        "accepted-tests",
        "accepted-identity",
    ],
)
def test_accounting_rejects_scope_loss_and_unsupported_claims_even_when_resealed(mutation):
    ledger = copy.deepcopy(LEDGER)
    binding = ledger["remaining_spec_binding"]
    row = binding["requirements"][0]
    if mutation == "missing-binding":
        del ledger["remaining_spec_binding"]
    elif mutation == "missing-requirement":
        binding["requirements"].pop()
    elif mutation == "duplicate-requirement":
        binding["requirements"][-1] = copy.deepcopy(row)
    elif mutation == "unknown-id":
        row["id"] = "INT-99"
    elif mutation == "source-hash":
        binding["source"]["sha256"] = "0" * 64
    elif mutation == "source-range":
        row["source"]["start_line"] += 1
    elif mutation == "work-type":
        row["work_type"].reverse()
    elif mutation in {"owner", "repo"}:
        row[mutation] = "optimizer"
    elif mutation == "consumer":
        row["consumer_slices"][0]["repo"] = "other"
    elif mutation == "OP-mapping":
        row["op_mapping"].pop()
    elif mutation == "OP-inverse":
        binding["op_mapping"][0]["requirements"].pop()
    elif mutation == "Stage2-link":
        binding["requirements"][8]["existing_matrix_ids"].pop()
    elif mutation == "Stage2-owner":
        binding["stage2_items"][0]["owner"] = "openpine"
    elif mutation == "old-OP-status":
        ledger["tasks"][0]["status"] = "accepted"
    elif mutation == "old-OP-evidence":
        ledger["tasks"][-1]["evidence_paths"].pop()
    elif mutation == "old-schema":
        ledger["schema"] += ".new"
    elif mutation == "old-source-hash":
        ledger["source_spec_sha256"] = binding["source"]["sha256"]
    elif mutation == "mandatory-3.12":
        binding["python_support"]["mandatory_minors"].insert(0, "3.12")
    elif mutation == "free-threading":
        binding["python_support"]["free_threaded_supported"] = True
    elif mutation == "disabled-GIL":
        binding["python_support"]["gil_enabled"] = False
    elif mutation == "accepted-summary":
        binding["full_release_accepted"] = True
    elif mutation == "false-done":
        row["status"] = "done"
        row["execution_receipts"] = [{"ok": True, "candidate_hash": "historical"}]
        row["raw_receipts"]["descriptors"] = [{"ok": True}]
    elif mutation == "invented-nodeid":
        row["references"]["tests"]["nodeids"] = ["test_invented.py::test_PASS"]
    elif mutation == "invented-identity":
        row["identities"]["candidate_hash"] = "sha256:" + "0" * 64
    elif mutation == "invalid-reference":
        binding["reference_catalog"]["candidate"]["code"][0]["path"] = "../foreign.json"
    elif mutation == "empty-reason":
        row["remaining_reason"] = " "
    elif mutation == "missing-policy":
        binding["execution_policy"] = "other-policy.json"
    elif mutation == "missing-basis":
        del row["references"]["independent_basis"]
    elif mutation == "fabricated-partial-receipt":
        row["execution_receipts"] = [{"ok": True, "status": "accepted"}]
    elif mutation == "fabricated-raw-receipt":
        row["raw_receipts"]["descriptors"] = [{"status": "PASS"}]
    elif mutation == "accepted-basis":
        row["references"]["independent_basis"]["status"] = "accepted"
    elif mutation == "accepted-tests":
        row["references"]["tests"]["status"] = "accepted"
    elif mutation == "accepted-identity":
        row["identities"]["status"] = "accepted"
    binding["contract_hash"] = digest(normative_contract(binding))
    with pytest.raises(ValueError):
        validate_review_ledger(ledger)


def test_source_bytes_are_checked_by_the_production_reader(tmp_path):
    target = tmp_path / "docs"
    target.mkdir()
    (target / "RC6_REVIEW_36.json").write_text(json.dumps(LEDGER))
    (tmp_path / SOURCE_PATH).write_bytes((ROOT / SOURCE_PATH).read_bytes() + b"\nchanged\n")
    with pytest.raises(ValueError, match="source bytes"):
        read_review_ledger(tmp_path)


def pinned_reference_roots():
    stack = Path(os.environ.get("PINE_STACK_ROOT", ROOT.parent))
    return {
        name: ROOT if name == "openpine" else stack / name
        for name in LEDGER["remaining_spec_binding"]["historical_baseline"]["source_pins"]
    }


def test_every_repository_reference_exists_at_its_exact_pinned_revision():
    report = read_review_ledger(ROOT, reference_roots=pinned_reference_roots())
    assert report["registry_complete"] is True


@pytest.mark.parametrize("mutation", ["missing-path", "foreign-object", "missing-root"])
def test_pinned_reference_replay_rejects_missing_or_foreign_paths(mutation):
    binding = copy.deepcopy(LEDGER["remaining_spec_binding"])
    roots = pinned_reference_roots()
    ref = binding["reference_catalog"]["oracle"]["code"][1]
    assert ref["component"] == "pinelib"
    if mutation == "missing-path":
        ref["path"] = "pinelib/math"
    elif mutation == "foreign-object":
        ref["git_object"]["oid"] = "0" * 40
    else:
        del roots["pinelib"]
    with pytest.raises(ValueError, match="pinned reference|repository"):
        validate_reference_paths(binding, roots)


def saved_current_projection():
    from openpine.verification.identity import seal
    from openpine.verification.stage_gate import CURRENT_SCHEMA, STABILIZATION_GATES

    return seal(
        {
            "schema_id": CURRENT_SCHEMA,
            "candidate_hash": "sha256:" + "1" * 64,
            "plan_hash": "sha256:" + "2" * 64,
            "inventory_hash": "sha256:" + "3" * 64,
            "run_id": "projection-unit-fixture",
            "stage2": {
                "status": "in_progress",
                "full_stage2_accepted": False,
                "criteria": [],
                "remaining": [],
                "remaining_spec_binding": validate_review_ledger(LEDGER),
            },
            "stabilization": {
                "accepted": True,
                "status": "accepted",
                "gates": {name: {"status": "passed"} for name in STABILIZATION_GATES},
            },
            "ok": True,
            "full_stage2_accepted": False,
        }
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "null",
        "zero-denominator",
        "no-unclosed",
        "false-stage2",
        "false-release",
        "foreign-contract",
        "duplicate-ID",
        "owner",
        "done",
        "false-status-counts",
    ],
)
def test_resealed_saved_current_rejects_missing_or_forged_remaining_projection(mutation):
    from openpine.verification.identity import seal
    from openpine.verification.stage_gate import current_views

    current = saved_current_projection()
    assert current_views(current)["remainder"]["remaining_spec_binding"]["requirement_count"] == 68
    current.pop("content_hash")
    stage2 = current["stage2"]
    projection = stage2["remaining_spec_binding"]
    if mutation == "missing":
        del stage2["remaining_spec_binding"]
    elif mutation == "null":
        stage2["remaining_spec_binding"] = None
    elif mutation == "zero-denominator":
        projection["requirement_count"] = 0
    elif mutation == "no-unclosed":
        projection["unclosed_requirements"] = []
    elif mutation in {"false-stage2", "false-release"}:
        projection[
            "full_stage2_accepted" if mutation == "false-stage2" else "full_release_accepted"
        ] = True
    elif mutation == "foreign-contract":
        projection["contract_hash"] = "sha256:" + "0" * 64
    elif mutation == "duplicate-ID":
        projection["unclosed_requirements"][-1] = copy.deepcopy(
            projection["unclosed_requirements"][0]
        )
    elif mutation == "owner":
        projection["unclosed_requirements"][0]["owner"] = "other"
    elif mutation == "done":
        projection["unclosed_requirements"][0]["status"] = "done"
    else:
        projection["status_counts"] = {"done": 68}
    with pytest.raises(ValueError):
        current_views(seal(current))


def test_legacy_saved_current_requires_explicit_opt_in_and_has_no_remaining_acceptance():
    from openpine.verification.identity import seal
    from openpine.verification.stage_gate import current_views

    current = saved_current_projection()
    current.pop("content_hash")
    del current["stage2"]["remaining_spec_binding"]
    views = current_views(seal(current), allow_legacy=True)
    assert views["remainder"]["remaining_spec_scope"] == "legacy_without_remaining_spec"
    assert views["remainder"]["remaining_spec_binding"] is None
    assert views["remainder"]["full_stage2_accepted"] is False
