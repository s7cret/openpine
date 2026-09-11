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
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    pins = read_json(root / "docs/RC6_LIFECYCLE_SOURCES.json")
    base = {
        "pine_version": 6,
        "symbol_id": "pine:function:ta.tsi",
        "overload_id": "pine:function:ta.tsi#canonical",
        "call_form": "NAMESPACE_FUNCTION",
        "status": "RUNTIME_DIRECT",
        "evidence_status": "EXAMPLES_ALL_PATHS",
        "qualifier_contract": {"status": "COMPATIBLE"},
        "evidence": [{"observation_scope": {"observed_from_bar": 12}}],
    }
    report = {
        "schema_id": "openpine.builtin_evidence_index.v1",
        "plan_hash": read_json(root / "verification/stage2-evidence-plan-lock.json")["plan_hash"],
        "lock_hash": read_json(root / "verification/stage2-callable-lock.json")["content_hash"],
        "source_pins": {key: pins[key] for key in ("pine2ast", "ast2python", "pinelib")},
        "rows": [
            base,
            {**base, "pine_version": 5, "evidence_status": "NO_EXAMPLES", "evidence": []},
        ],
        "ok": True,
        "denominator": 2,
        "direct_signatures": 2,
    }
    return seal(report)


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
