"""Fail-closed contracts for sealed execution plans and pytest receipts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from openpine.verification.execution_identity import environment_snapshot, source_snapshot
from openpine.verification.execution_plan import (
    assign_shards,
    make_plan,
    select_components,
    task_shard,
    validate_plan,
)
from openpine.verification.identity import seal, verify
from openpine.verification.pytest_gate import (
    collection_hash,
    validate_inventory,
    validate_phase_reports,
)


def _sealed_plan(tmp_path: Path) -> dict:
    root = tmp_path / "optimizer"
    package = root / "optimizer"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "optimizer"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_unit.py").write_text(
        "def test_fast() -> None:\n    assert 4 * 5 == 20\n\n"
        "def test_performance_marker() -> None:\n    assert 21 // 3 == 7\n",
        encoding="utf-8",
    )
    source = source_snapshot({"optimizer": root})
    environment = environment_snapshot()
    lane = f"py{sys.version_info.major}{sys.version_info.minor}"
    nodeids = [
        "tests/test_unit.py::test_fast",
        "tests/test_unit.py::test_performance_marker",
    ]
    inventory = {
        "nodeids": nodeids,
        "node_markers": {nodeids[0]: [], nodeids[1]: ["performance"]},
        "reviewed_lock": {
            "count": len(nodeids),
            "deselected": 0,
            "sha256": collection_hash(nodeids),
        },
        "deselected": 0,
        "source_hash": source["content_hash"],
        "environment_hash": environment["content_hash"],
    }
    policy = {
        "components": {
            "optimizer": {
                "dependencies": [],
                "pythons": [f"{sys.version_info.major}.{sys.version_info.minor}"],
                "timeout_seconds": 30,
                "memory_mib": 64,
            }
        },
        "untraced_markers": ["performance"],
    }
    return make_plan(
        profile="component",
        policy=policy,
        roots={"optimizer": root},
        source=source,
        inventories={"optimizer@" + lane: inventory},
        environments={lane: {"identity": environment, "executable": sys.executable}},
        requested=["optimizer"],
        shard_count=2,
        coverage=True,
    )


def _reseal(plan: dict) -> dict:
    return seal({key: value for key, value in plan.items() if key != "content_hash"})


def test_real_sealed_plan_separates_untraced_markers_and_shards(tmp_path: Path) -> None:
    plan = _sealed_plan(tmp_path)
    verify(plan, "openpine.test_execution_plan.v1")
    task = plan["tasks"][0]

    assert [shard["coverage"] for shard in task["shards"]] == [True, False]
    assert [shard["nodeids"] for shard in task["shards"]] == [
        ["tests/test_unit.py::test_fast"],
        ["tests/test_unit.py::test_performance_marker"],
    ]
    assert task_shard(plan, task["id"], "s000")[1]["coverage"] is True
    with pytest.raises(ValueError, match="unknown shard"):
        task_shard(plan, task["id"], "s999")
    with pytest.raises(ValueError, match="unknown task"):
        task_shard(plan, "optimizer@missing", "s000")


def test_plan_validator_rejects_sealed_but_unsafe_task_variants(tmp_path: Path) -> None:
    plan = _sealed_plan(tmp_path)
    variants: list[tuple[str, object, str]] = [
        ("coverage", "yes", "invalid shard instrumentation"),
        ("memory_mib", 63, "invalid task memory reservation"),
        ("plugins", ["not-a-plugin!"], "invalid plugin list"),
        ("exclusive_group", 3, "invalid exclusive group"),
        ("required_gates", ["bad gate"], "invalid required owner gates"),
    ]
    for key, value, message in variants:
        candidate = deepcopy(plan)
        if key == "coverage":
            candidate["tasks"][0]["shards"][0][key] = value
        elif key == "required_gates":
            candidate[key] = value
        else:
            candidate["tasks"][0][key] = value
        with pytest.raises(ValueError, match=message):
            validate_plan(_reseal(candidate))

    missing_markers = deepcopy(plan)
    del missing_markers["tasks"][0]["node_markers"]["tests/test_unit.py::test_fast"]
    with pytest.raises(ValueError, match="task markers omit required nodes"):
        validate_plan(_reseal(missing_markers))

    duplicate = deepcopy(plan)
    duplicate["tasks"].append(deepcopy(duplicate["tasks"][0]))
    with pytest.raises(ValueError, match="duplicate obligation"):
        validate_plan(_reseal(duplicate))


def test_selection_and_assignment_fail_closed_for_unsafe_inputs() -> None:
    policy = {
        "components": {
            "engine": {"dependencies": []},
            "ui": {"dependencies": ["engine"]},
        },
        "critical_paths": ["shared/*"],
    }
    # engine is prepared as ui's dependency, but its full suite is not an
    # affected test obligation for a ui-local source change.
    assert select_components(policy, "affected", [], ["ui/views.py"])[0] == ["ui"]
    assert select_components(policy, "affected", [], ["engine/shared/schema.py"])[1] == [
        "shared contract/fixture change escalates: engine/shared/schema.py"
    ]
    assert select_components(policy, "affected", [], ["engine/../escape.py"])[1] == [
        "unknown change escalates to full inventory"
    ]
    assert assign_shards(
        ["tests/a.py::test_a", "tests/a.py::test_b", "tests/b.py::test_c"],
        2,
        {"tests/a.py::test_a": 4.0, "tests/a.py::test_b": 1.0},
    )[0]["nodeids"] == ["tests/a.py::test_a", "tests/a.py::test_b"]
    for nodes, count, durations, message in [
        ([], 1, None, "empty inventory"),
        (["tests/a.py::test_a"], 0, None, "shard count"),
        (["tests/a.py::test_a"], 1, {"tests/a.py::test_a": float("inf")}, "invalid duration"),
    ]:
        with pytest.raises(ValueError, match=message):
            assign_shards(nodes, count, durations)

    cyclic = deepcopy(policy)
    cyclic["components"]["engine"]["dependencies"] = ["ui"]
    with pytest.raises(ValueError, match="cyclic dependency graph"):
        select_components(cyclic, "component", ["engine"])
    with pytest.raises(ValueError, match="profile needs an explicit component"):
        select_components(policy, "component", [])
    with pytest.raises(ValueError, match="unknown test profile"):
        select_components(policy, "not-a-profile", ["engine"])
    with pytest.raises(ValueError, match="unknown component"):
        select_components(policy, "component", ["missing"])
    malformed = deepcopy(policy)
    malformed["components"]["engine"]["dependencies"] = ["missing"]
    with pytest.raises(ValueError, match="invalid dependency graph"):
        select_components(malformed, "component", ["engine"])


def test_plan_identity_validator_rejects_resealed_boundary_mismatches(tmp_path: Path) -> None:
    plan = _sealed_plan(tmp_path)
    with pytest.raises(ValueError, match="execution plan identity mismatch"):
        validate_plan(plan, expected_hash="sha256:" + "0" * 64)

    cases: list[tuple[dict, str]] = []
    relative_root = deepcopy(plan)
    relative_root["roots"]["optimizer"] = "not-absolute"
    cases.append((relative_root, "plan root mismatch"))

    bad_environment = deepcopy(plan)
    environment = bad_environment["environments"].pop(next(iter(bad_environment["environments"])))
    bad_environment["environments"] = {"bad!": environment}
    bad_environment["tasks"][0]["environment"] = "bad!"
    bad_environment["tasks"][0]["id"] = "optimizer@bad!"
    cases.append((bad_environment, "invalid environment identity"))

    bad_commits = deepcopy(plan)
    bad_commits["source_commits"] = {"optimizer": "short"}
    cases.append((bad_commits, "plan producer commits"))

    bad_variant = deepcopy(plan)
    bad_variant["tasks"][0]["variant"] = "synthetic"
    cases.append((bad_variant, "unsupported task execution boundary"))

    bad_hash = deepcopy(plan)
    bad_hash["tasks"][0]["nodeids_hash"] = "sha256:" + "0" * 64
    cases.append((bad_hash, "task inventory hash/order mismatch"))

    bad_shard = deepcopy(plan)
    bad_shard["tasks"][0]["shards"][0]["id"] = "wrong"
    cases.append((bad_shard, "invalid shard ID"))

    missing_assignment = deepcopy(plan)
    missing_assignment["tasks"][0]["shards"] = []
    cases.append((missing_assignment, "missing/unexpected/duplicate shard assignment"))

    for candidate, message in cases:
        with pytest.raises(ValueError, match=message):
            validate_plan(_reseal(candidate))


def test_real_temporary_pytest_gate_writes_collection_and_execution_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_file = tmp_path / "test_owned.py"
    test_file.write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def operand() -> int:\n"
        "    return 9\n\n\n"
        "def test_real_owned_execution(operand: int) -> None:\n"
        "    assert operand * operand == 81\n",
        encoding="utf-8",
    )
    nodeid = "test_owned.py::test_real_owned_execution"
    lock = tmp_path / "inventory.json"
    lock.write_text(
        json.dumps(
            {"optimizer": {"count": 1, "deselected": 0, "sha256": collection_hash([nodeid])}}
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    collect_output = tmp_path / "collected.json"
    common = [
        "-o",
        "addopts=",
        "-p",
        "openpine.verification.pytest_gate",
        "--verification-lock=" + str(lock),
        "--verification-suite=optimizer",
    ]
    assert pytest.main([*common, "--collect-only", "--verification-output=" + str(collect_output), str(test_file)]) == 0
    collected = json.loads(collect_output.read_text(encoding="utf-8"))
    assert collected["collect_only"] is True and collected["ok"] is False
    assert collected["nodeids"] == [nodeid] and collected["reports"] == {}

    executed_output = tmp_path / "executed.json"
    assert pytest.main([*common, "--verification-output=" + str(executed_output), str(test_file)]) == 0
    executed = json.loads(executed_output.read_text(encoding="utf-8"))
    assert executed["collect_only"] is False and executed["ok"] is True
    assert validate_phase_reports([nodeid], executed["reports"]) == []


def test_reviewed_inventory_and_phase_receipts_reject_partial_success() -> None:
    baseline_nodes = ["tests/base.py::test_base"]
    added = "tests/new.py::test_new"
    expected = {
        "identity_mode": "reviewed_addition_to_hashed_baseline",
        "count": 2,
        "deselected": 0,
        "added_nodeids": [added],
        "baseline": {
            "count": 1,
            "deselected": 0,
            "sha256": collection_hash(baseline_nodes),
        },
    }
    validate_inventory(baseline_nodes + [added], expected, 0)
    for nodeids, lock in [
        ([], expected),
        (baseline_nodes, expected),
        (baseline_nodes + [added], {"count": 2, "deselected": 0, "sha256": "sha256:bad"}),
    ]:
        with pytest.raises(ValueError):
            validate_inventory(nodeids, lock, 0)

    node = "tests/base.py::test_base"
    passing = [
        {"when": "setup", "outcome": "passed", "xfail": False, "duration": 0.0},
        {"when": "call", "outcome": "passed", "xfail": False, "duration": 0.01},
        {"when": "teardown", "outcome": "passed", "xfail": False, "duration": 0.0},
    ]
    assert validate_phase_reports([node], {node: passing}) == []
    broken = deepcopy(passing)
    broken[1]["xfail"] = True
    broken[2]["duration"] = True
    errors = validate_phase_reports([node], {node: broken, "extra::test": []})
    assert "unexpected test report: extra::test" in errors
    assert "required test did not pass all phases: " + node in errors


def test_generated_ui_typescript_buildinfo_does_not_change_source_identity(tmp_path: Path) -> None:
    root = tmp_path / "openpine"
    root.mkdir()
    (root / "source.py").write_text("APP = 1\n", encoding="utf-8")
    baseline = source_snapshot({"openpine": root})
    ui = root / "openpine-ui"
    ui.mkdir()
    generated = ui / "tsconfig.tsbuildinfo"
    generated.write_text("temporary build metadata\n", encoding="utf-8")
    assert source_snapshot({"openpine": root}) == baseline
    generated.write_text("rewritten by vue-tsc\n", encoding="utf-8")
    assert source_snapshot({"openpine": root}) == baseline
    (ui / "tsconfig.json").write_text('{"compilerOptions": {}}\n', encoding="utf-8")
    assert source_snapshot({"openpine": root}) != baseline
