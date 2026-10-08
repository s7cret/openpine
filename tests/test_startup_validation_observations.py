"""Startup reuse must retain strict plans and fresh execution observations."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from openpine.verification import execution_binding, execution_identity, execution_plan
from openpine.verification.identity import read_json, seal, write_json
from openpine.verification.pytest_gate import InventoryGate, collection_hash


def _reseal(value: dict) -> dict:
    return seal({key: item for key, item in value.items() if key != "content_hash"})


@pytest.fixture
def frozen(tmp_path: Path) -> tuple[dict, dict, Path]:
    root = tmp_path / "optimizer"
    (root / "optimizer").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "optimizer"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (root / "optimizer" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_owned.py").write_text(
        "def test_regular():\n    assert 6 * 7 == 42\n\n"
        "def test_performance():\n    assert 3 * 5 == 15\n",
        encoding="utf-8",
    )
    nodes = ["tests/test_owned.py::test_performance", "tests/test_owned.py::test_regular"]
    source = execution_identity.source_snapshot({"optimizer": root})
    environment = execution_identity.environment_snapshot()
    lane = f"py{sys.version_info.major}{sys.version_info.minor}"
    plan = execution_plan.make_plan(
        profile="component",
        policy={"components": {"optimizer": {
            "dependencies": [],
            "pythons": [f"{sys.version_info.major}.{sys.version_info.minor}"],
            "timeout_seconds": 30,
            "memory_mib": 64,
        }}, "untraced_markers": ["performance"]},
        roots={"optimizer": root},
        source=source,
        inventories={"optimizer@" + lane: {
            "nodeids": nodes,
            "node_markers": {nodes[0]: ["performance"], nodes[1]: []},
            "reviewed_lock": {"count": 2, "deselected": 0, "sha256": collection_hash(nodes)},
            "deselected": 0,
            "source_hash": source["content_hash"],
            "environment_hash": environment["content_hash"],
        }},
        environments={lane: {"identity": environment, "executable": sys.executable}},
        requested=["optimizer"],
        shard_count=2,
        coverage=True,
    )
    binding = execution_binding.make_binding(plan, {"optimizer": root}, {lane: sys.executable})
    return plan, binding, root


def _config(plan: dict, binding: dict, tmp_path: Path, name: str) -> SimpleNamespace:
    plan_path, binding_path = tmp_path / (name + "-plan.json"), tmp_path / (name + "-binding.json")
    write_json(plan_path, plan)
    write_json(binding_path, binding)
    shard = next(item for item in plan["tasks"][0]["shards"] if item["coverage"] is False)
    options = {
        "--verification-plan": str(plan_path),
        "--verification-plan-hash": plan["content_hash"],
        "--verification-task": plan["tasks"][0]["id"],
        "--verification-shard": shard["id"],
        "--verification-suite": "optimizer",
        "--verification-run-id": "startup-observation-001",
        "--verification-attempt-id": "a001",
        "--verification-binding": str(binding_path),
        "--verification-output": str(tmp_path / (name + "-phases.json")),
    }
    return SimpleNamespace(getoption=lambda name: options.get(name), option=SimpleNamespace(collectonly=False))


@pytest.mark.parametrize("entry", ["task_shard", "validate_binding", "locations", "checked_locations", "make_binding"])
def test_public_entry_points_reject_resealed_invalid_plan(frozen, entry: str) -> None:
    plan, binding, root = frozen
    invalid = deepcopy(plan)
    invalid["tasks"][0]["shards"][0]["coverage"] = "yes"
    invalid = _reseal(invalid)
    relocated = _reseal({**binding, "plan_hash": invalid["content_hash"]})
    task = invalid["tasks"][0]
    calls = {
        "task_shard": lambda: execution_plan.task_shard(invalid, task["id"], task["shards"][0]["id"]),
        "validate_binding": lambda: execution_binding.validate_binding(invalid, relocated),
        "locations": lambda: execution_binding.locations(invalid, relocated),
        "checked_locations": lambda: execution_binding.checked_locations(invalid, relocated),
        "make_binding": lambda: execution_binding.make_binding(invalid, {"optimizer": root}, dict(binding["interpreters"])),
    }
    with pytest.raises(ValueError, match="invalid shard instrumentation"):
        calls[entry]()


@pytest.mark.parametrize("mutation,message", [
    (lambda value: value.update(coverage=False), "unexpected authority fields"),
    (lambda value: value.update(plan_hash="sha256:" + "0" * 64), "different execution plan"),
    (lambda value: value.update(roots={}), "exactly the frozen source roots"),
    (lambda value: value.update(interpreters={}), "unknown or empty interpreter"),
    (lambda value: value.update(owner_paths=[]), "locator mapping"),
    (lambda value: value.update(roots={"optimizer": "/tmp/../escape"}), "traversal-free"),
    (lambda value: value.update(owner_paths={"/historical/root": "/new", "/historical/root/nested": "/other"}), "overlapping owner replay"),
])
@pytest.mark.parametrize("entry", ["validate_binding", "locations", "checked_locations"])
def test_binding_authority_remains_strict(frozen, mutation, message: str, entry: str) -> None:
    plan, binding, _ = frozen
    malformed = deepcopy(binding)
    mutation(malformed)
    with pytest.raises(ValueError, match=message):
        getattr(execution_binding, entry)(plan, _reseal(malformed))


@pytest.mark.parametrize("entry", ["validate_binding", "locations", "checked_locations"])
def test_binding_seal_is_checked_at_every_public_entry(frozen, entry: str) -> None:
    plan, binding, _ = frozen
    malformed = deepcopy(binding)
    malformed["roots"]["optimizer"] = "/unreviewed/root"
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        getattr(execution_binding, entry)(plan, malformed)


@pytest.mark.parametrize("variant", ["missing", "mapping_type", "list_type", "non_string", "duplicate", "wrong_traced", "wrong_untraced", "duplicate_obligation"])
def test_marker_validation_rejects_each_invalid_shard_obligation(frozen, variant: str) -> None:
    plan, _, _ = frozen
    malformed = deepcopy(plan)
    task = malformed["tasks"][0]
    regular = "tests/test_owned.py::test_regular"
    if variant == "missing":
        del task["node_markers"][regular]
    elif variant == "mapping_type":
        task["node_markers"] = None
    elif variant == "list_type":
        task["node_markers"][regular] = "performance"
    elif variant == "non_string":
        task["node_markers"][regular] = [3]
    elif variant == "duplicate":
        task["node_markers"][regular] = ["performance", "performance"]
    elif variant in {"wrong_traced", "wrong_untraced"}:
        original = variant == "wrong_traced"
        shard = next(item for item in task["shards"] if item["coverage"] is original)
        shard["coverage"] = not original
    else:
        task["shards"].append(deepcopy(task["shards"][0]))
    with pytest.raises(ValueError):
        execution_plan.validate_plan(_reseal(malformed))


def _count_real_observations(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"plan": 0, "source": 0, "environment": 0}
    original_plan = execution_plan.validate_plan
    original_source = execution_identity.source_snapshot
    original_environment = execution_identity.environment_snapshot

    def checked_plan(*args, **kwargs):
        calls["plan"] += 1
        return original_plan(*args, **kwargs)

    def observed_source(*args, **kwargs):
        calls["source"] += 1
        return original_source(*args, **kwargs)

    def observed_environment():
        calls["environment"] += 1
        return original_environment()

    monkeypatch.setattr(execution_plan, "validate_plan", checked_plan)
    monkeypatch.setattr(execution_binding, "validate_plan", checked_plan)
    monkeypatch.setattr(execution_identity, "source_snapshot", observed_source)
    monkeypatch.setattr(execution_binding, "source_snapshot", observed_source)
    monkeypatch.setattr(execution_identity, "environment_snapshot", observed_environment)
    return calls


def test_pytest_preflight_validates_once_but_observes_each_invocation(frozen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, binding, _ = frozen
    calls = _count_real_observations(monkeypatch)
    for name in ("first", "second"):
        gate = InventoryGate(_config(plan, binding, tmp_path, name))
        gate.pytest_sessionstart(SimpleNamespace())
        assert gate.errors == []
        assert gate.before == plan["source"]
        assert gate.environment == plan["environments"][gate.task["environment"]]["identity"]
    assert calls == {"plan": 2, "source": 2, "environment": 2}


def test_pytest_preflight_rejects_source_mutation_with_same_size_and_mtime(frozen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, binding, root = frozen
    calls = _count_real_observations(monkeypatch)
    InventoryGate(_config(plan, binding, tmp_path, "before-source-change")).pytest_sessionstart(SimpleNamespace())
    source = root / "optimizer" / "__init__.py"
    before = source.stat()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    gate = InventoryGate(_config(plan, binding, tmp_path, "after-source-change"))
    with pytest.raises(pytest.exit.Exception, match="candidate source changed before execution"):
        gate.pytest_sessionstart(SimpleNamespace())
    assert calls == {"plan": 2, "source": 2, "environment": 2}
    assert gate.errors == ["candidate source changed before execution"]


def test_pytest_preflight_observes_new_dependency_environment(frozen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, binding, _ = frozen
    calls = _count_real_observations(monkeypatch)
    InventoryGate(_config(plan, binding, tmp_path, "before-environment-change")).pytest_sessionstart(SimpleNamespace())
    distributions = list(execution_identity.importlib.metadata.distributions())
    distributions.append(SimpleNamespace(metadata={"Name": "startup-observation-new-dependency"}, version="0.0.1"))
    monkeypatch.setattr(execution_identity.importlib.metadata, "distributions", lambda: iter(distributions))
    gate = InventoryGate(_config(plan, binding, tmp_path, "after-environment-change"))
    with pytest.raises(pytest.exit.Exception, match="interpreter/dependency environment differs"):
        gate.pytest_sessionstart(SimpleNamespace())
    assert calls == {"plan": 2, "source": 1, "environment": 2}
    assert gate.errors == ["interpreter/dependency environment differs from the plan"]


def test_pytest_finish_reobserves_source_after_successful_phases(frozen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, binding, root = frozen
    calls = _count_real_observations(monkeypatch)
    config = _config(plan, binding, tmp_path, "changed-during-execution")
    gate = InventoryGate(config)
    session = SimpleNamespace(exitstatus=0)
    gate.pytest_sessionstart(session)
    gate.nodes = list(gate.shard["nodeids"])
    gate.node_markers = {node: list(gate.task["node_markers"][node]) for node in gate.nodes}
    gate.reports = {node: [
        {"when": phase, "outcome": "passed", "xfail": False, "duration": 0.01}
        for phase in ("setup", "call", "teardown")
    ] for node in gate.nodes}
    (root / "optimizer" / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    gate.pytest_sessionfinish(session, 0)
    receipt = read_json(Path(config.getoption("--verification-output")))
    assert calls == {"plan": 1, "source": 2, "environment": 1}
    assert receipt["reports"] == gate.reports
    assert receipt["ok"] is False and session.exitstatus == 1
    assert receipt["source_before"] == plan["source"]["content_hash"]
    assert receipt["source_after"] != receipt["source_before"]
    assert receipt["errors"] == ["candidate source changed during execution"]


def test_make_binding_validates_once_and_rechecks_source_bytes(frozen, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, binding, root = frozen
    calls = _count_real_observations(monkeypatch)
    assert execution_binding.make_binding(plan, {"optimizer": root}, dict(binding["interpreters"])) == binding
    assert calls == {"plan": 1, "source": 1, "environment": 0}
    (root / "optimizer" / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="relocated source is not the frozen candidate"):
        execution_binding.make_binding(plan, {"optimizer": root}, dict(binding["interpreters"]))
    assert calls == {"plan": 2, "source": 2, "environment": 0}


def test_bound_interpreter_is_rehashed_despite_same_size_and_mtime(frozen, tmp_path: Path) -> None:
    plan, binding, root = frozen
    executable = tmp_path / "owned-interpreter"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    relocated_plan = deepcopy(plan)
    lane = plan["tasks"][0]["environment"]
    identity = relocated_plan["environments"][lane]["identity"]
    identity["executable_sha256"] = execution_identity.hash_file(executable)
    relocated_plan["environments"][lane]["identity"] = _reseal(identity)
    relocated_plan = _reseal(relocated_plan)
    relocated = execution_binding.make_binding(relocated_plan, {"optimizer": root}, {lane: str(executable)})
    assert execution_binding.checked_locations(relocated_plan, relocated)[1] == {lane: str(executable)}
    before = executable.stat()
    executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    os.utime(executable, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert executable.stat().st_size == before.st_size
    assert executable.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(ValueError, match="bound interpreter differs from frozen executable"):
        execution_binding.checked_locations(relocated_plan, relocated)
    with pytest.raises(ValueError, match="bound interpreter differs from frozen executable"):
        execution_binding.make_binding(relocated_plan, {"optimizer": root}, {lane: str(executable)})


def test_pytest_preflight_rejects_resealed_binding_authority(frozen, tmp_path: Path) -> None:
    plan, binding, _ = frozen
    unsafe = _reseal({**binding, "timeout_seconds": 60})
    gate = InventoryGate(_config(plan, unsafe, tmp_path, "authority-change"))
    with pytest.raises(pytest.exit.Exception, match="unexpected authority fields"):
        gate.pytest_sessionstart(SimpleNamespace())
    assert gate.errors == ["binding has unexpected authority fields"]
