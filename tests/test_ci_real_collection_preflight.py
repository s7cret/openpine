"""Exercise verification collection and preflight with real interpreters and pytest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.verification import execution_cli
from openpine.verification.execution_identity import source_snapshot
from openpine.verification.identity import verify
from openpine.verification.pytest_gate import collection_hash


HOST = Path(__file__).resolve().parents[1]


def _components(tmp_path: Path) -> tuple[SimpleNamespace, Path]:
    stack = tmp_path / "stack"
    for component in execution_cli.COMPONENTS:
        if component == "openpine":
            continue
        root = stack / component
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "' + component + '"\nversion = "5.0.0rc6"\n', encoding="utf-8"
        )
    root = stack / "optimizer"
    (root / "tests").mkdir()
    (root / "tests/test_owner.py").write_text(
        'def test_independent_sum():\n    assert 2 + 3 == 5\n', encoding="utf-8"
    )
    settings = {"components": {"optimizer": {"selectors": ["tests"], "imports": []}}}
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(settings))
    lock = tmp_path / "inventory.json"
    lock.write_text(json.dumps({"optimizer": {"count": 1, "deselected": 0, "sha256":
        collection_hash(["tests/test_owner.py::test_independent_sum"])}}))
    args = SimpleNamespace(host_root=HOST, stack_root=stack, policy=policy,
        component=["optimizer"], interpreters=["lane=" + sys.executable],
        inventory_lock=lock, collection_timeout=45, level="pytest",
        output=tmp_path / "preflight.json")
    return args, root


def test_roots_and_interpreter_specs_reject_missing_or_duplicate_inputs(tmp_path: Path) -> None:
    args, _ = _components(tmp_path)
    roots = execution_cli._roots(args)
    assert set(roots) == set(execution_cli.COMPONENTS)
    assert roots["openpine"] == HOST
    assert execution_cli._python_specs(["lane=" + sys.executable]) == {"lane": sys.executable}
    for specs in (["same=" + sys.executable, "same=" + sys.executable], ["invalid"], ["?=" + sys.executable]):
        with pytest.raises(ValueError, match="unique ID=EXECUTABLE"):
            execution_cli._python_specs(specs)
    (args.stack_root / "optimizer").rename(args.stack_root / "optimizer-hidden")
    with pytest.raises(ValueError, match="missing component source: optimizer"):
        execution_cli._roots(args)


def test_pytest_preflight_probes_child_python_and_keeps_nonacceptance(tmp_path: Path) -> None:
    args, _ = _components(tmp_path)
    report = execution_cli.preflight(args)
    verify(report, "openpine.test_preflight.v1")
    assert report["scope"] == "pytest" and report["full_stage_accepted"] is False
    assert report["source"] == source_snapshot(execution_cli._roots(args))
    observed = report["environments"]["lane"]
    assert observed["identity"]["python"].startswith("3.")
    assert observed["imports"]["pytest"].endswith("/__init__.py")
    assert report["worker"] == {"status": "not_run", "protected_worker_verified": False}
    assert json.loads(args.output.read_text()) == report


def test_real_pytest_collection_locks_nodeids_and_does_not_claim_execution(tmp_path: Path) -> None:
    args, root = _components(tmp_path)
    args.output = tmp_path / "collection.json"
    report = execution_cli.collect_inventories(args)
    verify(report, execution_cli.COLLECTION_SCHEMA)
    assert report["ok"] is True and report["errors"] == []
    assert report["collect_only"] is True and report["execution_pass"] is False
    result = report["inventories"]["optimizer@lane"]
    assert result["nodeids"] == ["tests/test_owner.py::test_independent_sum"]
    assert result["reviewed_lock"]["count"] == 1
    assert report["environments"]["lane"]["identity"]["python"].startswith("3.")
    (root / "tests/test_owner.py").write_text(
        'def test_independent_subtraction():\n    assert 7 - 2 == 5\n', encoding="utf-8"
    )
    args.output = tmp_path / "changed-collection.json"
    changed = execution_cli.collect_inventories(args)
    assert changed["ok"] is False and changed["collect_only"] is True
    assert changed["execution_pass"] is False
    assert any("collection/locked inventory validation failed" in error for error in changed["errors"])
    assert changed["inventories"] == {}


def test_real_collected_plan_cannot_accept_stale_policy_or_source(tmp_path: Path) -> None:
    args, root = _components(tmp_path)
    args.output = tmp_path / "collection.json"
    collection = execution_cli.collect_inventories(args)
    assert collection["ok"] is True
    parser = argparse.ArgumentParser()
    execution_cli.add_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args([
        "test-plan", "--collection", str(args.output), "--policy", str(args.policy),
        "--profile", "component", "--component", "optimizer", "--shards", "1",
        "--coverage", "--output", str(tmp_path / "plan.json"),
    ])
    assert execution_cli.run_command(args) == 0
    plan = json.loads(args.output.read_text())
    assert plan["tasks"][0]["nodeids"] == ["tests/test_owner.py::test_independent_sum"]
    assert plan["tasks"][0]["coverage"] is True
    assert plan["profile"] == "component"
    assert plan["full_acceptance_requires_owner_gates"] is True

    args.output = tmp_path / "stale-plan.json"
    policy = json.loads(args.policy.read_text())
    policy["components"]["optimizer"]["smoke"] = ["tests/*"]
    args.policy.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="different policy"):
        execution_cli.run_command(args)
    assert not args.output.exists()
    args.policy.write_text(json.dumps({"components": {"optimizer": {"selectors": ["tests"], "imports": []}}}))
    (root / "tests/test_owner.py").write_text("def test_changed():\n    assert 2 == 2\n")
    with pytest.raises(ValueError, match="source changed after collection"):
        execution_cli.run_command(args)
    assert not args.output.exists()


def test_cli_rejects_invalid_binding_and_shard_without_launching_campaign(tmp_path: Path) -> None:
    args, _ = _components(tmp_path)
    args.output = tmp_path / "collection.json"
    assert execution_cli.collect_inventories(args)["ok"] is True
    parser = argparse.ArgumentParser()
    execution_cli.add_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args([
        "test-plan", "--collection", str(args.output), "--policy", str(args.policy),
        "--profile", "component", "--component", "optimizer", "--shards", "1",
        "--coverage", "--output", str(tmp_path / "plan.json"),
    ])
    assert execution_cli.run_command(args) == 0
    args.plan = args.output
    args.expected_plan_hash = json.loads(args.plan.read_text())["content_hash"]
    args.output = tmp_path / "binding.json"
    args.command = "test-bind"
    args.root = ["optimizer=" + str(tmp_path / "stack/optimizer"), "optimizer=duplicate"]
    args.python = ["lane=" + sys.executable]
    with pytest.raises(ValueError, match="need unique NAME=PATH bindings"):
        execution_cli.run_command(args)
    assert not args.output.exists()

    args.command = "test-run"
    args.task = ["optimizer@lane", "optimizer@lane"]
    args.shard = []
    with pytest.raises(ValueError, match="unknown or duplicate task selection"):
        execution_cli.run_command(args)
    args.task = []
    args.shard = ["missing-slash"]
    with pytest.raises(ValueError, match="shard needs TASK/SHARD"):
        execution_cli.run_command(args)
