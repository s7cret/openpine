"""Real temporary campaigns and CLI fail-closed evidence checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
import sys

import pytest

from openpine.verification import execution_campaign, execution_cli
from openpine.verification.execution_identity import (
    environment_snapshot,
    source_snapshot,
    write_once_json,
)
from openpine.verification.execution_plan import make_plan
from openpine.verification.identity import read_json, verify
from openpine.verification.pytest_gate import collection_hash


HOST = Path(__file__).resolve().parents[1]


def test_zombie_only_process_group_is_quiescent_without_live_members() -> None:
    from openpine.verification.execution_campaign import _remaining_group_members, _wait_for_process_group_exit
    import psutil

    holder_script = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True, "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "print(p.pid, flush=True); sys.stdin.buffer.read(1); p.wait()"
    )
    holder = subprocess.Popen(  # noqa: S603 -- fixed interpreter and script
        [sys.executable, '-c', holder_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert holder.stdout is not None
        pgid = int(holder.stdout.readline())
        deadline = time.monotonic() + 3
        while psutil.Process(pgid).status() != psutil.STATUS_ZOMBIE:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert [m['status'] for m in _remaining_group_members(pgid)] == [psutil.STATUS_ZOMBIE]
        os.killpg(pgid, 0)  # A zombie still makes the group's existence check succeed.
        assert _wait_for_process_group_exit(pgid, 0.2)
    finally:
        if holder.stdin is not None:
            try:
                holder.stdin.write(b'x')
                holder.stdin.flush()
            except BrokenPipeError:
                pass
            holder.stdin.close()
        try:
            holder.wait(timeout=3)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder.wait(timeout=3)
        if holder.stdout is not None:
            holder.stdout.close()


def test_completed_pytest_group_gets_bounded_quiescence_not_a_false_orphan() -> None:
    from openpine.verification.execution_campaign import _remaining_group_members, _wait_for_process_group_exit

    child_script = (
        "import sys,time; time.sleep(float(sys.argv[1]))"
    )
    leader_script = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )
    for child_seconds, grace_seconds, expected in ((0.35, 2.0, True), (5.0, 0.15, False)):
        # The leader exits successfully before its same-session child does.
        leader = subprocess.Popen(  # noqa: S603 -- fixed interpreter and in-test scripts
            [sys.executable, "-c", leader_script, str(child_seconds), child_script],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert leader.wait(timeout=3) == 0
            assert _wait_for_process_group_exit(leader.pid, grace_seconds) is expected
            if not expected:
                members = _remaining_group_members(leader.pid)
                assert members and any(row["status"] not in {"zombie", "dead"} for row in members)
                assert all(set(row) == {"pid", "name", "status"} for row in members)
        finally:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            leader.wait(timeout=3)


def _tiny_optimizer(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "optimizer"
    package = root / "optimizer"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "optimizer"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (package / "__init__.py").write_text(
        "def multiply(left: int, right: int) -> int:\n    return left * right\n",
        encoding="utf-8",
    )
    nodeid = "tests/test_receipt.py::test_independent_product"
    (tests / "test_receipt.py").write_text(
        "from optimizer import multiply\n\n\n"
        "def test_independent_product() -> None:\n"
        "    observed = {\"left\": 6, \"right\": 7, \"product\": 42}\n"
        "    assert multiply(observed[\"left\"], observed[\"right\"]) == observed[\"product\"]\n",
        encoding="utf-8",
    )
    return root, nodeid


def _real_plan(tmp_path: Path) -> tuple[dict, Path]:
    optimizer, nodeid = _tiny_optimizer(tmp_path)
    roots = {"openpine": HOST, "optimizer": optimizer}
    source = source_snapshot(roots)
    environment = environment_snapshot()
    lane = f"py{sys.version_info.major}{sys.version_info.minor}"
    policy = {
        "components": {
            "optimizer": {
                "dependencies": [],
                "pythons": [f"{sys.version_info.major}.{sys.version_info.minor}"],
                "timeout_seconds": 60,
                "memory_mib": 64,
            }
        }
    }
    plan = make_plan(
        profile="component",
        policy=policy,
        roots=roots,
        source=source,
        inventories={
            "optimizer@" + lane: {
                "nodeids": [nodeid],
                "node_markers": {nodeid: []},
                "reviewed_lock": {
                    "count": 1,
                    "deselected": 0,
                    "sha256": collection_hash([nodeid]),
                },
                "deselected": 0,
                "source_hash": source["content_hash"],
                "environment_hash": environment["content_hash"],
            }
        },
        environments={lane: {"identity": environment, "executable": sys.executable}},
        requested=["optimizer"],
        shard_count=1,
    )
    path = tmp_path / "frozen-plan.json"
    write_once_json(path, plan)
    return plan, path


def _all_component_args(tmp_path: Path) -> SimpleNamespace:
    stack = tmp_path / "stack"
    for name in execution_cli.COMPONENTS:
        if name == "openpine":
            continue
        root = stack / name
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "' + name + '"\nversion = "0.0.0"\n', encoding="utf-8"
        )
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "components": {
                    "optimizer": {
                        "selectors": ["tests"],
                        "imports": [],
                        "pythons": [f"{sys.version_info.major}.{sys.version_info.minor}"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        host_root=HOST,
        stack_root=stack,
        policy=policy,
        component=["optimizer"],
        interpreters=["lane=" + sys.executable],
        output=tmp_path / "preflight-full.json",
        level="full",
    )


def test_real_campaign_archives_receipts_then_rejects_tampered_artifact(tmp_path: Path) -> None:
    plan, plan_path = _real_plan(tmp_path)
    evidence = tmp_path / "campaign"
    run = execution_campaign.run_campaign(
        plan, plan_path, evidence, jobs=1, run_id="receipt-real-001"
    )
    verify(run, execution_campaign.RUN_SCHEMA)
    assert run["attempts"][0]["status"] == "completed"
    assert run["attempts"][0]["returncode"] == 0

    aggregate = execution_campaign.aggregate_campaign(
        plan,
        evidence,
        expected_plan_hash=plan["content_hash"],
        expected_run_id="receipt-real-001",
    )
    assert aggregate["ok"] is True and aggregate["pytest_scope_passed"] is True
    junit = evidence / run["attempts"][0]["artifacts"]["junit"]["path"]
    assert execution_campaign.validate_junit(
        junit, plan["tasks"][0]["shards"][0]["nodeids"]
    )["tests"] == 1

    stdout = evidence / run["attempts"][0]["artifacts"]["stdout"]["path"]
    stdout.write_bytes(stdout.read_bytes() + b"tampered after archived receipt\n")
    rejected = execution_campaign.aggregate_campaign(
        plan,
        evidence,
        expected_plan_hash=plan["content_hash"],
        expected_run_id="receipt-real-001",
    )
    assert rejected["ok"] is False
    assert any("attempt artifact checksum mismatch" in error for error in rejected["errors"])


def test_cli_full_preflight_records_real_nonacceptance_and_aggregate_dispatches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _all_component_args(tmp_path)
    preflight = execution_cli.preflight(args)
    verify(preflight, "openpine.test_preflight.v1")
    assert preflight["scope"] == "full"
    assert preflight["ok"] is False
    assert preflight["full_stage_accepted"] is False
    assert any("protected-worker owner verification" in error for error in preflight["errors"])
    assert read_json(args.output) == preflight

    roots = execution_cli._roots(args)
    direct = execution_cli.probe_environment(
        roots,
        {"components": {"optimizer": {"imports": []}}},
        ["optimizer"],
        "full",
    )
    assert direct["identity"]["python"].startswith(f"{sys.version_info.major}.")
    assert any(row["requirement"] == "pytest>=8.2" for row in direct["requirements"])
    failed_import = execution_cli.probe_environment(
        roots,
        {"components": {"optimizer": {"imports": ["no_such_verification_module"]}}},
        ["optimizer"],
        "pytest",
    )
    assert failed_import["ok"] is False
    assert any("no_such_verification_module" in error for error in failed_import["errors"])
    probe_failure = execution_cli._probe(
        str(tmp_path / "missing-python"), roots, {"components": {}}, [], "pytest", tmp_path / "bad-probe"
    )
    assert probe_failure["ok"] is False and probe_failure["errors"]

    plan, plan_path = _real_plan(tmp_path)
    evidence = tmp_path / "aggregate-evidence"
    execution_campaign.run_campaign(plan, plan_path, evidence, jobs=1, run_id="cli-aggregate-001")
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_args = SimpleNamespace(
        command="test-aggregate",
        plan=plan_path,
        expected_plan_hash=plan["content_hash"],
        evidence=evidence,
        run_id="cli-aggregate-001",
        output=aggregate_path,
    )
    assert execution_cli.run_command(aggregate_args) == 0
    receipt = read_json(aggregate_path)
    assert receipt["ok"] is True and receipt["full_stage_accepted"] is False
    assert '"pytest_scope_passed": true' in capsys.readouterr().out

    lane = "py" + "".join(map(str, sys.version_info[:2]))
    binding_path = tmp_path / "binding.json"
    bind_args = SimpleNamespace(
        command="test-bind",
        plan=plan_path,
        expected_plan_hash=plan["content_hash"],
        root=["openpine=" + plan["roots"]["openpine"], "optimizer=" + plan["roots"]["optimizer"]],
        python=[lane + "=" + sys.executable],
        output=binding_path,
    )
    assert execution_cli.run_command(bind_args) == 0
    binding = read_json(binding_path)
    assert binding["plan_hash"] == plan["content_hash"]

    run_args = SimpleNamespace(
        command="test-run",
        plan=plan_path,
        expected_plan_hash=plan["content_hash"],
        output=tmp_path / "cli-run-evidence",
        jobs=1,
        run_id="cli-run-001",
        binding=binding_path,
        task=[],
        shard=[],
        memory_mib=None,
    )
    assert execution_cli.run_command(run_args) == 0
    assert read_json(run_args.output / "aggregate.json")["ok"] is True


def test_campaign_and_junit_refuse_invalid_inputs_before_execution(tmp_path: Path) -> None:
    plan, plan_path = _real_plan(tmp_path)
    task = plan["tasks"][0]
    with pytest.raises(ValueError, match="jobs must be"):
        execution_campaign.run_campaign(plan, plan_path, tmp_path / "never-jobs", jobs=0)
    with pytest.raises(ValueError, match="invalid run ID"):
        execution_campaign.run_campaign(plan, plan_path, tmp_path / "never-id", jobs=1, run_id="bad id")
    with pytest.raises(ValueError, match="empty/oversized/DTD JUnit"):
        unsafe = tmp_path / "unsafe.xml"
        unsafe.write_text('<!DOCTYPE x [<!ENTITY x "boom">]><testsuite/>', encoding="utf-8")
        execution_campaign.validate_junit(unsafe, task["nodeids"])
