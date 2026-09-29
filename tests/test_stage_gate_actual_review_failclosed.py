"""Stage gate must reject missing or downgraded evidence, without simulated acceptance."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openpine.verification import stage_gate


_ROOT = Path(__file__).resolve().parents[1]


def _actual_plan_ledger() -> tuple[dict, dict]:
    plan = json.loads((_ROOT / "verification/stages.json").read_text(encoding="utf-8"))
    ledger = json.loads((_ROOT / "docs/RC6_REVIEW_36.json").read_text(encoding="utf-8"))
    stage_gate.validate_stages(plan, ledger)
    return plan, ledger


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p.update(schema_id="invalid"), "invalid stage plan"),
        (lambda p: p.update(source_spec_sha256="0" * 64), "not bound"),
        (lambda p: p.update(baseline="not-a-commit"), "source baseline"),
        (lambda p: p["stages"].pop(), "all eight stages"),
        (lambda p: p["stages"][0].update(title=""), "distinct exit criteria"),
        (lambda p: p["stages"][0].update(exit_criteria=["same", "same"]), "distinct exit criteria"),
        (lambda p: p["stages"][0].update(depends_on=[1]), "dependency"),
        (lambda p: p.update(preserved_tasks=[]), "original task mapping"),
    ],
)
def test_actual_review_plan_rejects_source_stage_or_task_identity_mutation(mutation, message):
    plan, ledger = _actual_plan_ledger()
    bad = copy.deepcopy(plan)
    mutation(bad)
    with pytest.raises(ValueError, match=message):
        stage_gate.validate_stages(bad, ledger)


def test_capability_policy_rejects_missing_denominator_and_unbound_required_row():
    policy = {"schema_id": "openpine.required_capabilities.v1", "minimum_rows": 2, "required": [{"symbol_id": "strategy.entry", "pine_version": "v6"}]}
    with pytest.raises(ValueError, match="invalid capability policy"):
        stage_gate.validate_capabilities({"rows": []}, {**policy, "schema_id": "other"})
    with pytest.raises(ValueError, match="denominator shrank"):
        stage_gate.validate_capabilities({"rows": []}, policy)
    with pytest.raises(ValueError, match="chain is incomplete"):
        stage_gate.validate_capabilities({"rows": [{"symbol_id": "strategy.entry", "pine_version": "v6", "status": "BOUND"}, {"symbol_id": "other", "pine_version": "v6", "status": "UNBOUND"}]}, {**policy, "required": [{"symbol_id": "other", "pine_version": "v6"}]})
    stage_gate.validate_capabilities({"rows": [{"symbol_id": "strategy.entry", "pine_version": "v6", "status": "BOUND"}, {"symbol_id": "other", "pine_version": "v6", "status": "UNBOUND"}]}, policy)


def test_foundation_rejects_pin_mismatch_before_any_unobserved_suite(tmp_path: Path):
    host = _ROOT
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    pins = json.loads((host / "docs/RC6_LIFECYCLE_SOURCES.json").read_text(encoding="utf-8"))
    (evidence / "source-pins.json").write_text(json.dumps({**pins, "pine2ast": "0" * 40}))
    with pytest.raises(ValueError, match="sources differ"):
        stage_gate.run_stage_gate(host, tmp_path, evidence)
    (evidence / "source-pins.json").write_text(json.dumps(pins))
    with pytest.raises((ValueError, FileNotFoundError), match="inventory|No such file"):
        stage_gate.run_stage_gate(host, tmp_path, evidence)
    assert not (evidence / "stage1.json").exists()
