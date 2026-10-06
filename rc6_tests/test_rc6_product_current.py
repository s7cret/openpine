"""INT08 contract self-checks; miniature inputs never qualify the product."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openpine.verification.identity import read_json, seal, write_json
from openpine.verification.stage_gate import (
    PRODUCT_CURRENT_SCHEMA,
    PRODUCT_GATES,
    current_views,
    product_requirements,
    run_product_gate,
)

HOST = Path(__file__).resolve().parents[1]


def reseal(value):
    return seal({key: item for key, item in value.items() if key != "content_hash"})


@pytest.fixture
def projection():
    from rc6_tests.test_rc6_review_ledger import saved_current_projection

    base = saved_current_projection()
    base = reseal(
        {**base, "source_commits": {"openpine": "1" * 40}, "policy_hash": "sha256:" + "4" * 64}
    )
    governance = product_requirements()
    requirements = [
        {
            **row,
            "required_domains": [gate for gate, ids in governance.items() if row["id"] in ids],
            "status": "qualified",
            "remaining_reason": "",
        }
        for row in base["stage2"]["remaining_spec_binding"]["unclosed_requirements"]
    ]
    return seal(
        {
            "schema_id": PRODUCT_CURRENT_SCHEMA,
            **{
                key: base[key]
                for key in (
                    "candidate_hash",
                    "source_commits",
                    "plan_hash",
                    "policy_hash",
                    "inventory_hash",
                    "run_id",
                )
            },
            "stabilization_current": base,
            "product": {
                "status": "accepted",
                "accepted": True,
                "domain_policy_hash": "sha256:" + "5" * 64,
                "gates": {
                    gate: {
                        "status": "passed",
                        "errors": [],
                        "raw_result_hash": "sha256:" + "6" * 64,
                    }
                    for gate in PRODUCT_GATES
                },
                "requirements": requirements,
                "requirement_count": 68,
            },
            "ok": True,
            "full_stage2_accepted": True,
            "full_release_accepted": True,
        }
    )


def test_all_fourteen_domains_and_sixty_eight_requirements_have_positive_projection(projection):
    views = current_views(projection)
    assert set(views) == {"progress", "remainder", "summary", "api", "documentation", "release"}
    assert views["summary"]["required_domains"] == views["summary"]["passed_domains"] == 14
    assert views["summary"]["unclosed_requirements"] == 0
    assert views["remainder"]["items"] == []
    assert len(views["remainder"]["stage2_items"]) == 16
    assert all(view["full_release_accepted"] is True for view in views.values())
    assert all(view["current_hash"] == projection["content_hash"] for view in views.values())
    # Frozen P0 accounting remains historical; this is a separate computed overlay.
    assert views["remainder"]["remaining_spec_binding"]["full_release_accepted"] is False


@pytest.mark.parametrize("gate", PRODUCT_GATES)
@pytest.mark.parametrize("mutation", ["missing", "failed", "not_run", "no_primary_hash"])
def test_every_missing_or_failed_domain_prevents_a_resealed_release_claim(
    projection, gate, mutation
):
    changed = copy.deepcopy(projection)
    if mutation == "missing":
        changed["product"]["gates"].pop(gate)
    elif mutation == "no_primary_hash":
        changed["product"]["gates"][gate].pop("raw_result_hash")
    else:
        changed["product"]["gates"][gate] = {
            "status": "blocked" if mutation == "failed" else "not_run",
            "errors": ["mandatory owner remains unclosed"],
        }
    with pytest.raises(ValueError, match="domain|verdict"):
        current_views(reseal(changed))


@pytest.mark.parametrize(
    "field",
    ["candidate_hash", "plan_hash", "policy_hash", "inventory_hash", "run_id", "source_commits"],
)
def test_foreign_projection_cannot_replace_stabilization_identity(projection, field):
    changed = copy.deepcopy(projection)
    changed[field] = {} if field == "source_commits" else "foreign"
    with pytest.raises(ValueError, match="exact candidate"):
        current_views(reseal(changed))


def test_production_policy_keeps_all_full_scope_domains_unexecuted():
    from openpine.verification.review_ledger import read_review_ledger
    from openpine.verification.stage_gate import _validate_product_policy

    policy = read_json(HOST / "verification/execution-policy.json")["product_acceptance"]
    ids = {r["id"] for r in read_review_ledger(HOST)["unclosed_requirements"]}
    _validate_product_policy(policy, ids)
    assert len(ids) == 68 and len(policy["domains"]) == 14
    assert set().union(*(set(r["requirements"]) for r in policy["domains"].values())) == ids
    assert all(r["obligations"] is None for r in policy["domains"].values())


@pytest.mark.parametrize("gate", [gate for gate in PRODUCT_GATES if gate != "static-build-quality"])
def test_nonstatic_product_obligations_reject_null_independent_expected(gate):
    from openpine.verification.stage_gate import _validate_product_policy

    policy = read_json(HOST / "verification/execution-policy.json")["product_acceptance"]
    ids = set().union(*(set(row["requirements"]) for row in policy["domains"].values()))
    domain = policy["domains"][gate]
    domain["obligations"] = {
        requirement: {
            "nodes": {"openpine": ["case.py::test_contract"]},
            "commands": [
                {
                    "argv": ["python", "case.py"],
                    "cwd": "/source",
                    "inputs": {"oracle": {"path": "/oracle", "sha256": "sha256:" + "1" * 64}},
                    "expected_stdout": None,
                }
            ],
        }
        for requirement in domain["requirements"]
    }
    with pytest.raises(ValueError, match="independent expected"):
        _validate_product_policy(policy, ids)


@pytest.fixture(scope="module")
def product_fixture(tmp_path_factory):
    from rc6_tests.stabilization_fixture import build_fixture

    return build_fixture(tmp_path_factory.mktemp("real-miniature-product-verifier"), product=True)


def test_missing_optional_provider_descriptor_cannot_hide_five_deselections():
    from openpine.verification.architecture import COMPONENTS
    from openpine.verification.stage_gate import _checked_product_pending

    policy = {"components": {name: {} for name in COMPONENTS}}
    plan = {
        "source": {},
        "tasks": [
            {
                "component": "marketdata-provider",
                "environment": "py313",
                "nodeids": ["test_native.py::test_value"],
                "deselected": 5,
            }
        ],
    }
    with pytest.raises(ValueError, match="unaccounted.*deselected"):
        _checked_product_pending(plan, policy, {})


def replay(fixture):
    host, plan, evidence, packet = fixture
    return run_product_gate(
        host, plan, evidence, expected_plan_hash=plan["content_hash"], run_id=packet["run_id"]
    )


def test_real_miniature_commands_and_all_existing_raw_owners_reach_positive_verifier_path(
    product_fixture,
):
    report = replay(product_fixture)
    assert report["ok"] is report["full_release_accepted"] is True
    assert current_views(report)["summary"]["passed_domains"] == 14
    assert current_views(report)["remainder"]["items"] == []
    assert report["stabilization_current"]["full_release_accepted"] is False


@pytest.mark.parametrize("gate", PRODUCT_GATES)
def test_each_product_missing_owner_is_not_run_with_substantive_remainder(product_fixture, gate):
    _, _, evidence, _ = product_fixture
    path = evidence / "product-inputs.json"
    original = path.read_bytes()
    changed = read_json(path)
    changed["domains"].pop(gate)
    try:
        write_json(path, changed)
        report = replay(product_fixture)
        assert not report["ok"] and not report["full_release_accepted"]
        assert report["product"]["gates"][gate]["status"] == "not_run"
        ids = {r["id"] for r in current_views(report)["remainder"]["items"]}
        assert set(product_requirements()[gate]).issubset(ids)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("gate", PRODUCT_GATES)
@pytest.mark.parametrize("mutation", ["foreign_binding", "stdout", "missing_input"])
def test_every_product_owner_reopens_primaries_and_rejects_copied_or_failed_inputs(
    product_fixture, gate, mutation
):
    _, _, evidence, _ = product_fixture
    packet_path = evidence / "product-inputs.json"
    packet = read_json(packet_path)
    saved_packet = packet_path.read_bytes()
    entry = packet["domains"][gate]
    requirement = next(iter(entry))
    command_path = evidence / entry[requirement][0]["path"]
    command = read_json(command_path)
    saved_command = command_path.read_bytes()
    stdout = command_path.parent / "stdout.log"
    saved_stdout = stdout.read_bytes()
    try:
        if mutation == "foreign_binding":
            command["binding"]["run_id"] = "copied-prior-candidate"
            write_json(command_path, reseal(command))
            from openpine.verification.execution_identity import hash_file

            entry[requirement][0]["sha256"] = hash_file(command_path)
            write_json(packet_path, packet)
        elif mutation == "stdout":
            stdout.write_text(json.dumps({"value": 5}))
            from openpine.verification.execution_identity import hash_file

            command["files"]["stdout.log"] = hash_file(stdout)
            write_json(command_path, reseal(command))
            for row in entry.values():
                row[0]["sha256"] = hash_file(command_path)
            write_json(packet_path, packet)
        else:
            entry.pop(requirement)
            write_json(packet_path, packet)
        report = replay(product_fixture)
        assert not report["ok"] and not report["full_release_accepted"]
        assert report["product"]["gates"][gate]["status"] == "blocked"
    finally:
        packet_path.write_bytes(saved_packet)
        command_path.write_bytes(saved_command)
        stdout.write_bytes(saved_stdout)


@pytest.mark.parametrize("field", ["plan_hash", "candidate_hash", "policy_hash", "run_id"])
def test_product_packet_stale_identity_rejected_before_verdict(product_fixture, field):
    _, _, evidence, _ = product_fixture
    path = evidence / "product-inputs.json"
    original = path.read_bytes()
    changed = read_json(path)
    changed[field] = "foreign"
    try:
        write_json(path, changed)
        with pytest.raises(ValueError, match="stale/foreign"):
            replay(product_fixture)
    finally:
        path.write_bytes(original)


def test_current_cli_preserves_stabilization_and_adds_explicit_product_readers():
    import argparse
    from openpine.verification.execution_cli import add_commands

    parser = argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest="command", required=True))
    required = [
        "--host-root",
        "/h",
        "--plan",
        "/p",
        "--expected-plan-hash",
        "hash",
        "--evidence",
        "/e",
        "--run-id",
        "run",
        "--output",
        "/o",
    ]
    legacy = parser.parse_args(["test-current", *required])
    assert legacy.scope == "stabilization"
    product = parser.parse_args(
        ["test-current", *required, "--scope", "product", "--view", "release"]
    )
    assert product.scope == "product" and product.view == "release"
