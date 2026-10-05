"""Focused real-owner checks for the source register's inventory row.

Other component mappings are deliberately outside this unit's denominator;
full owner composition is exercised by test_rc6_stabilization, not fabricated.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import read_json
from openpine.verification.stabilization_evidence import verify_reconciliation

ROOT = Path(__file__).resolve().parents[1]
REGISTER = "verification/rc6-branch-reconciliation-register.json"
INVENTORY = "verification/inventory.json"


@pytest.fixture
def inventory_scope(tmp_path):
    original = read_json(ROOT / REGISTER)
    row = next(row for row in original["rows"] if row["id"] == INVENTORY)
    register = {**original, "rows": [copy.deepcopy(row)]}
    target = tmp_path / REGISTER
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(register))
    policy = {"register": REGISTER, "required_ids": [INVENTORY],
              "sha256": hash_file(target)}
    # Observe actual candidate bytes independently of the register's claim.
    plan = {"source": {"components": {"openpine": {"files": {
        INVENTORY: {"sha256": hash_file(ROOT / INVENTORY)}
    }}}}, "tasks": []}
    return tmp_path, plan, policy


def test_current_inventory_row_resolves_actual_candidate_bytes(inventory_scope):
    host, plan, policy = inventory_scope
    before = (host / REGISTER).read_bytes()
    assert verify_reconciliation(plan, host, policy) == {
        "register_sha256": policy["sha256"], "decisions": 1
    }
    assert (host / REGISTER).read_bytes() == before


def test_inventory_reason_matches_reviewed_count_without_rebaselining():
    row = next(row for row in read_json(ROOT / REGISTER)["rows"] if row["id"] == INVENTORY)
    inventory = read_json(ROOT / INVENTORY)["openpine"]
    assert f"inventory count {inventory['count']}, baseline {inventory['baseline']['count']} retained" in row["reason"]
    assert inventory["baseline"] == {
        "count": 11027, "deselected": 0,
        "sha256": "sha256:13aa883b450b42cb10c95a5e7141061eb464e4a9a549c7c63e215190fe3c2dcd"
    }


def test_policy_binds_exact_register_bytes():
    policy = read_json(ROOT / "verification/execution-policy.json")["stabilization"]["branch-reconciliation"]
    assert policy["register"] == REGISTER
    assert policy["sha256"] == hash_file(ROOT / REGISTER)
    assert policy["required_ids"] == [row["id"] for row in read_json(ROOT / REGISTER)["rows"]]


@pytest.mark.parametrize("checksum", [
    "sha256:cf287f31f82c6b3ed6ef3b9b15034c98d8f478a81b2733642bfc38273bada40b",
    "sha256:" + "0" * 64,
])
def test_stale_or_wrong_mapping_fails_closed_preserving_bytes(inventory_scope, checksum):
    host, plan, policy = inventory_scope
    path = host / REGISTER
    register = read_json(path)
    register["rows"][0]["mappings"][0]["sha256"] = checksum
    path.write_text(json.dumps(register))
    # Even correctly re-bound register bytes cannot bless a false source mapping.
    policy["sha256"] = hash_file(path)
    before = path.read_bytes()
    frozen_plan = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="stale reconciliation implementation mapping"):
        verify_reconciliation(plan, host, policy)
    assert path.read_bytes() == before
    assert plan == frozen_plan


def test_register_file_tampering_fails_closed_preserving_bytes(inventory_scope):
    host, plan, policy = inventory_scope
    path = host / REGISTER
    path.write_bytes(path.read_bytes() + b"\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="reconciliation register changed"):
        verify_reconciliation(plan, host, policy)
    assert path.read_bytes() == before
