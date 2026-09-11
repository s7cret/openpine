"""Read-only Stage 2 remaining work: four original gates, not a fabricated percent."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from openpine.verification.identity import read_json, seal, verify


def build_stage2_remaining(host_root: Path, builtin_index: dict) -> dict:
    root = host_root.resolve(strict=True)
    matrix = read_json(root / "verification/stage2-remaining-matrix.json")
    verify(matrix, "openpine.stage2_remaining_matrix.v1")
    lock = read_json(root / "verification/stage2-remaining-matrix-lock.json")
    if matrix["content_hash"] != lock["content_hash"]:
        raise ValueError("remaining matrix changed without explicit review")
    stages = read_json(root / "verification/stages.json")
    original = next(row for row in stages["stages"] if row["id"] == 2)
    if (
        matrix["tasks"] != original["tasks"]
        or matrix["criteria"] != original["exit_criteria"]
        or matrix["source_spec_sha256"] != stages["source_spec_sha256"]
    ):
        raise ValueError("remaining work no longer matches the original stage")
    if [row["id"] for row in matrix["items"]] != lock["ids"] or len(set(lock["ids"])) != len(
        lock["ids"]
    ):
        raise ValueError("work-item inventory changed")
    verify(builtin_index, "openpine.builtin_evidence_index.v1")
    plan_lock = read_json(root / "verification/stage2-evidence-plan-lock.json")
    surface_lock = read_json(root / "verification/stage2-callable-lock.json")
    if (
        builtin_index["plan_hash"] != plan_lock["plan_hash"]
        or builtin_index["lock_hash"] != surface_lock["content_hash"]
    ):
        raise ValueError("builtin index belongs to a different evidence plan/surface")
    pins = read_json(root / "docs/RC6_LIFECYCLE_SOURCES.json")
    if any(
        builtin_index["source_pins"].get(key) != pins[key]
        for key in ("pine2ast", "ast2python", "pinelib")
    ):
        raise ValueError("builtin index belongs to a different source revision")
    covered, conditional, missing = [], [], []
    for row in builtin_index["rows"]:
        if row["status"] != "RUNTIME_DIRECT":
            continue
        key = [row[k] for k in ("pine_version", "symbol_id", "overload_id", "call_form")]
        if row["evidence_status"] != "EXAMPLES_ALL_PATHS":
            missing.append(
                {
                    "key": key,
                    "evidence_status": row["evidence_status"],
                    "qualifier_contract": row["qualifier_contract"],
                }
            )
        else:
            covered.append(key)
            if row["evidence"] and all(
                item["observation_scope"]["observed_from_bar"] > 0 for item in row["evidence"]
            ):
                conditional.append(key)
    return seal(
        {
            "schema_id": "openpine.stage2_remaining_report.v1",
            "matrix_hash": matrix["content_hash"],
            "builtin_index_hash": builtin_index["content_hash"],
            "source_pins": builtin_index["source_pins"],
            "stage": 2,
            "status": "in_progress",
            "criteria": [
                {
                    "id": criterion,
                    "status": "not_accepted",
                    "remaining_items": [
                        row["id"] for row in matrix["items"] if row["criterion"] == criterion
                    ],
                }
                for criterion in matrix["criteria"]
            ],
            "items": matrix["items"],
            "item_status_counts": dict(Counter(row["status"] for row in matrix["items"])),
            "work_item_count": len(matrix["items"]),
            "count_policy": matrix["count_policy"],
            "builtin_index_replay_passed": builtin_index["ok"],
            "ok": builtin_index["ok"],
            "installed_callable_denominator": builtin_index["denominator"],
            "direct_signatures": builtin_index["direct_signatures"],
            "direct_with_bounded_examples_all_paths": len(covered),
            "conditioned_horizon_only": conditional,
            "direct_without_all_paths": missing,
            "coverage_is_not_stage_percent": True,
            "full_stage2_accepted": False,
            "tradingview_verified": False,
        }
    )
