"""Current status uses raw owner execution, never self-sealed PASS summaries."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal
from openpine.verification.stabilization_evidence import verify_command


def reseal(value):
    value = copy.deepcopy(value)
    value.pop("content_hash", None)
    return seal(value)


@pytest.fixture
def command(tmp_path):
    argv = [sys.executable, "-I", "-c", 'import json; print(json.dumps({"value": 42}))']
    root = tmp_path / "command"
    run_logged(argv, cwd=tmp_path, output=root, env={"PATH": os.environ["PATH"]})
    return root, {"argv": argv, "cwd": str(tmp_path)}, {"value": 42}


def test_real_command_raw_result(command):
    root, expected, output = command
    assert (
        verify_command(root, expected, expected_stdout=output)["status"] == "completed"
    )


@pytest.mark.parametrize(
    "mutation", ["stdout", "resealed-output", "argv", "cwd", "missing-log", "failed"]
)
def test_command_tampering_cannot_be_resealed(command, mutation):
    root, expected, output = command
    receipt = read_json(root / "command.json")
    if mutation in {"stdout", "resealed-output"}:
        (root / "stdout.log").write_text('{"value": 41}\n')
        if mutation == "resealed-output":
            from openpine.verification.execution_identity import hash_file

            receipt["files"]["stdout.log"] = hash_file(root / "stdout.log")
    elif mutation == "missing-log":
        (root / "stderr.log").unlink()
    elif mutation in {"argv", "cwd"}:
        receipt[mutation] = ["true"] if mutation == "argv" else "/"
    else:
        receipt["returncode"] = 1
    (root / "command.json").write_text(json.dumps(reseal(receipt)))
    with pytest.raises((ValueError, OSError)):
        verify_command(root, expected, expected_stdout=output)


@pytest.fixture(scope="module")
def full_fixture(tmp_path_factory):
    from rc6_tests.stabilization_fixture import build_fixture

    return build_fixture(tmp_path_factory.mktemp("real-minimal-stabilization"))


def replay(fixture):
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = fixture
    return run_stabilization_gate(
        host,
        plan,
        evidence,
        expected_plan_hash=plan["content_hash"],
        run_id=packet["run_id"],
    )


def test_seven_raw_owners_accept_real_minimal_execution_and_keep_language_debt(
    full_fixture,
):
    from openpine.verification.stage_gate import current_views

    current = replay(full_fixture)
    assert current["ok"], current
    assert all(
        row["status"] == "passed" for row in current["stabilization"]["gates"].values()
    )
    assert current["stage2"]["status"] == "in_progress"
    assert current["full_stage2_accepted"] is False
    views = current_views(current)
    assert {view["candidate_hash"] for view in views.values()} == {
        current["candidate_hash"]
    }
    assert {view["plan_hash"] for view in views.values()} == {current["plan_hash"]}
    assert views["remainder"]["items"]


@pytest.mark.parametrize(
    "gate",
    [
        "branch-reconciliation",
        "foundation",
        "protected-workers",
        "coverage",
        "frontend",
        "packages",
        "test-performance",
    ],
)
def test_each_missing_owner_is_not_run_not_accepted(full_fixture, gate):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"].pop(gate)
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert (
            not current["ok"]
            and current["stabilization"]["gates"][gate]["status"] == "not_run"
        )
        assert current["stage2"]["status"] == "in_progress"
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("field", ["plan_hash", "candidate_hash", "run_id"])
def test_foreign_historical_packet_rejected(full_fixture, field):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed[field] = "historical"
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="historical/stale"):
            replay(full_fixture)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    "owner", ["foundation", "coverage", "frontend", "packages", "test-performance"]
)
def test_joint_resealing_does_not_replace_raw_owner_evidence(full_fixture, owner):
    from openpine.verification.execution_identity import hash_file

    _, _, evidence, packet = full_fixture
    replacements = {}

    def replace(path, value):
        replacements[path] = path.read_bytes()
        path.write_text(json.dumps(value))

    changed = copy.deepcopy(packet)
    if owner == "foundation":
        path = evidence / changed["gates"][owner]["environments"]["py"] / "stage1.json"
        receipt = read_json(path)
        receipt["source_pins"]["pine2ast"] = "0" * 40
        replace(path, reseal(receipt))
    elif owner == "coverage":
        folder = evidence / next(iter(changed["gates"][owner]["tasks"].values()))
        data = read_json(folder / "coverage.json")
        data["totals"]["percent_covered"] = 999
        replace(folder / "coverage.json", data)
        receipt = read_json(folder / "receipt.json")
        receipt["json"]["sha256"] = hash_file(folder / "coverage.json")
        replace(folder / "receipt.json", reseal(receipt))
    elif owner == "frontend":
        descriptor = changed["gates"][owner]["tests"]
        path = evidence / descriptor["path"]
        data = read_json(path)
        data["testResults"][0]["assertionResults"][0]["fullName"] = "different test"
        replace(path, data)
        descriptor["sha256"] = hash_file(path)
        command_descriptor = changed["gates"][owner]["commands"][-1]
        command_path = evidence / command_descriptor["path"]
        command = read_json(command_path)
        command["artifacts"]["tests"] = copy.deepcopy(descriptor)
        replace(command_path, reseal(command))
        command_descriptor["sha256"] = hash_file(command_path)
    elif owner == "packages":
        package = next(iter(changed["gates"][owner].values()))["normal"]
        path = evidence / package["probe"]["path"]
        data = read_json(path)
        data["components"]["openpine"]["origin"] = str(evidence / "plan.json")
        replace(path, data)
        package["probe"]["sha256"] = hash_file(path)
        command_path = path.parent / "command.json"
        command = read_json(command_path)
        command["files"]["stdout.log"] = hash_file(path)
        replace(command_path, reseal(command))
        for desc in package["commands"]:
            if desc["path"] == command_path.relative_to(evidence).as_posix():
                desc["sha256"] = hash_file(command_path)
    else:
        samples = changed["gates"][owner]["after"]
        samples[:] = [copy.deepcopy(samples[0]) for _ in range(5)]
    path = evidence / "stabilization-inputs.json"
    replace(path, changed)
    try:
        current = replay(full_fixture)
        assert not current["ok"], current
        assert current["stabilization"]["gates"][owner]["status"] == "blocked", current
    finally:
        for path, original in replacements.items():
            path.write_bytes(original)


def test_stale_source_and_resealed_plan_cannot_change_expected_identity(full_fixture):
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = full_fixture
    source = host / "test_minimal.py"
    original = source.read_bytes()
    try:
        source.write_bytes(original + b"\n# changed candidate\n")
        with pytest.raises(ValueError, match="candidate is stale"):
            replay(full_fixture)
        changed = copy.deepcopy(plan)
        from openpine.verification.execution_identity import source_snapshot

        changed["source"] = source_snapshot(
            {n: Path(p) for n, p in plan["roots"].items()}
        )
        with pytest.raises(ValueError, match="plan identity mismatch"):
            run_stabilization_gate(
                host,
                reseal(changed),
                evidence,
                expected_plan_hash=plan["content_hash"],
                run_id=packet["run_id"],
            )
    finally:
        source.write_bytes(original)


def test_false_full_stage2_flag_and_cli_reader_agreement(full_fixture, tmp_path):
    from openpine.verification.__main__ import main
    from openpine.verification.stage_gate import current_views

    host, plan, evidence, packet = full_fixture
    current = replay(full_fixture)
    forged = copy.deepcopy(current)
    forged["full_stage2_accepted"] = True
    with pytest.raises(ValueError, match="not full Stage 2"):
        current_views(reseal(forged))
    common = [
        "--host-root",
        str(host),
        "--plan",
        str(evidence / "plan.json"),
        "--expected-plan-hash",
        plan["content_hash"],
        "--evidence",
        str(evidence),
        "--run-id",
        packet["run_id"],
    ]
    for view in ("current", "progress", "remainder", "summary"):
        output = tmp_path / (view + ".json")
        assert (
            main(["test-current", *common, "--view", view, "--output", str(output)])
            == 0
        )
        data = read_json(output)
        assert data["candidate_hash"] == current["candidate_hash"]
        assert data["plan_hash"] == current["plan_hash"]
    saved = tmp_path / "forged.json"
    saved.write_text(json.dumps(reseal(forged)))
    with pytest.raises(ValueError, match="saved current verdict differs"):
        main(
            [
                "test-current",
                *common,
                "--saved-current",
                str(saved),
                "--output",
                str(tmp_path / "rejected.json"),
            ]
        )


@pytest.mark.parametrize("owner", ["foundation", "coverage", "packages"])
def test_missing_owner_interpreter_cannot_be_accepted(full_fixture, owner):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    if owner == "foundation":
        changed["gates"][owner]["environments"] = {}
    elif owner == "coverage":
        changed["gates"][owner]["tasks"].pop(
            next(iter(changed["gates"][owner]["tasks"]))
        )
    else:
        changed["gates"][owner] = {}
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert current["stabilization"]["gates"][owner]["status"] == "blocked"
        assert not current["ok"]
    finally:
        path.write_bytes(original)


def test_resealed_campaign_and_execution_command_interpreter_rejected(full_fixture):
    _, _, evidence, _ = full_fixture
    campaign = evidence / "run-0"
    run = read_json(campaign / "run.json")
    attempt = run["attempts"][0]
    execution = (
        campaign
        / attempt["task"]
        / attempt["shard"]
        / attempt["attempt_id"]
        / "execution.json"
    )
    original_run = (campaign / "run.json").read_bytes()
    original_execution = execution.read_bytes()
    try:
        attempt["argv"][0] = "/foreign/python"
        execution.write_text(json.dumps(attempt))
        (campaign / "run.json").write_text(json.dumps(reseal(run)))
        current = replay(full_fixture)
        assert not current["ok"]
        assert all(
            row["status"] == "blocked"
            for row in current["stabilization"]["gates"].values()
        )
        assert any(
            "interpreter/cwd" in error
            for row in current["stabilization"]["gates"].values()
            for error in row["errors"]
        )
    finally:
        execution.write_bytes(original_execution)
        (campaign / "run.json").write_bytes(original_run)


def test_tampered_installed_resource_is_rejected_against_frozen_wheel(full_fixture):
    _, _, evidence, packet = full_fixture
    package = next(iter(packet["gates"]["packages"].values()))["normal"]
    probe = read_json(evidence / package["probe"]["path"])
    component = probe["components"]["openpine"]
    relative = next(
        name for name in component["files"] if name.endswith("fixture-resource.json")
    )
    path = Path(probe["prefix"]) / relative
    original = path.read_bytes()
    try:
        path.write_text('{"value":999}')
        current = replay(full_fixture)
        assert not current["ok"]
        assert current["stabilization"]["gates"]["packages"]["status"] == "blocked"
    finally:
        path.write_bytes(original)
