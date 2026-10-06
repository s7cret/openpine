"""Actual small verifier campaigns; these fixtures do not qualify the product."""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from int05_tests.negative_sensitivity import compare_negative
from openpine.verification.execution_campaign import run_campaign
from openpine.verification.execution_identity import hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import make_plan
from openpine.verification.identity import canonical, read_json, seal
from rc6_tests.test_rc6_execution_platform import lock, tiny_plan


def actual_pair(folder, *, omitted=False, two_red=False, affected_only=False):
    folder.mkdir()
    seed, _ = tiny_plan(folder, shards=1)
    roots = {n: Path(p) for n, p in seed["roots"].items()}
    other = folder / "other"
    other.mkdir()
    roots["other"] = other
    (other / "test_other.py").write_text("def test_value():\n    assert True\n")
    if affected_only:
        (roots["tiny"] / "test_b.py").write_text(
            "import sys\ndef test_value():\n"
            '    assert "--verification-run-id=affected" not in sys.argv\n'
        )
    policy = {
        "components": {"tiny": {}, "other": {}},
        "required_gates": {"stage-full": ["foundation"]},
    }
    nodes = {"tiny": seed["tasks"][0]["nodeids"], "other": ["test_other.py::test_value"]}

    def execute(profile, label):
        source = source_snapshot(roots)
        inventories = {
            owner + "@py": {
                "nodeids": ids,
                "reviewed_lock": lock(ids),
                "deselected": 0,
                "source_hash": source["content_hash"],
                "environment_hash": seed["environments"]["py"]["identity"]["content_hash"],
            }
            for owner, ids in nodes.items()
        }
        plan = make_plan(
            profile=profile,
            policy=policy,
            roots=roots,
            source=source,
            environments=seed["environments"],
            inventories=inventories,
            changes=["tiny/test_a.py"] if profile == "affected" else [],
            shard_count=1,
        )
        path = folder / (label + ".json")
        write_once_json(path, plan)
        evidence = folder / label
        run_campaign(plan, path, evidence, jobs=1, run_id=label)
        return plan, evidence

    control, control_evidence = execute("stage-full", "control")
    if not omitted or two_red:
        (roots["tiny"] / "test_a.py").write_text("def test_value():\n    assert False\n")
    if omitted:
        (other / "test_other.py").write_text("def test_value():\n    assert False\n")
    full, full_evidence = execute("stage-full", "full")
    affected, affected_evidence = execute("affected", "affected")
    return {
        "policy": policy,
        "control": control,
        "affected": affected,
        "full": full,
        "control_evidence": control_evidence,
        "full_evidence": full_evidence,
        "affected_evidence": affected_evidence,
        "control_hash": control["content_hash"],
        "full_hash": full["content_hash"],
        "affected_hash": affected["content_hash"],
        "control_run": "control",
        "full_run": "full",
        "affected_run": "affected",
        "expected_owners": ["tiny"],
        "oracle_owner": "other" if omitted else "tiny",
        "oracle_node": "test_other.py::test_value" if omitted else "test_a.py::test_value",
    }


@pytest.fixture(scope="module")
def red_pair(tmp_path_factory):
    return actual_pair(tmp_path_factory.mktemp("negative-base") / "candidate")


def assess(data):
    data = dict(data)
    policy, control, affected, full = (
        data.pop(n) for n in ("policy", "control", "affected", "full")
    )
    return compare_negative(policy, control, affected, full, **data)


def mutable_evidence(data, tmp_path, side="full"):
    result = copy.deepcopy(data)
    destination = tmp_path / side
    shutil.copytree(data[side + "_evidence"], destination)
    result[side + "_evidence"] = destination
    return result


def alter(data, mutator, *, side="full", artifact=None):
    """Mutate copied evidence and reseal checksums, so admission is tested."""
    evidence = data[side + "_evidence"]
    run = read_json(evidence / "run.json")
    attempt = next(a for a in run["attempts"] if a["task"] == "tiny@py")
    if artifact:
        path = evidence / attempt["artifacts"][artifact]["path"]
        if artifact == "phases":
            value = read_json(path)
            mutator(value)
            path.write_bytes(
                canonical(seal({k: v for k, v in value.items() if k != "content_hash"}))
            )
        else:
            mutator(path)
        attempt["artifacts"][artifact]["sha256"] = hash_file(path)
    else:
        mutator(attempt)
    path = evidence / f"{attempt['task']}/{attempt['shard']}/a001/execution.json"
    path.write_bytes(canonical(attempt))
    (evidence / "run.json").write_bytes(
        canonical(seal({k: v for k, v in run.items() if k != "content_hash"}))
    )


def test_actual_red_pair_confirms_sensitivity_without_acceptance(red_pair):
    result = assess(red_pair)
    assert result["verdict"] == "SENSITIVITY_CONFIRMED", result
    assert result["full_failures"] == result["affected_failures"]
    assert (
        not result["full"]["pytest_scope_passed"] and not result["affected"]["pytest_scope_passed"]
    )
    assert not result["full_acceptance"]


@pytest.mark.parametrize("two_red", [False, True])
def test_full_extra_omitted_failure_blocks_even_two_red(tmp_path, two_red):
    result = assess(actual_pair(tmp_path / "candidate", omitted=True, two_red=two_red))
    assert result["verdict"] == "FALSE_NEGATIVE", result
    assert any(row[0] == "other@py" for row in result["missing_affected_failures"])
    assert result["affected"]["pytest_scope_passed"] is (not two_red)


def test_unexpected_affected_only_failure_is_not_silent_equivalence(tmp_path):
    result = assess(actual_pair(tmp_path / "candidate", affected_only=True))
    assert result["verdict"] == "INCONCLUSIVE", result
    assert result["unexpected_affected_only_failures"]


@pytest.mark.parametrize(
    "kind",
    [
        "missing-phase",
        "skip",
        "xfail",
        "setup",
        "teardown",
        "source",
        "environment",
        "collection-error",
    ],
)
def test_coherently_resealed_bad_phase_is_inconclusive(red_pair, tmp_path, kind):
    data = mutable_evidence(red_pair, tmp_path)

    def change(value):
        rows = value["reports"]["test_a.py::test_value"]
        if kind == "missing-phase":
            rows.pop()
        elif kind == "skip":
            rows[1]["outcome"] = "skipped"
        elif kind == "xfail":
            rows[1]["xfail"] = True
        elif kind == "setup":
            rows[0]["outcome"] = "failed"
        elif kind == "teardown":
            rows[2]["outcome"] = "failed"
        elif kind == "source":
            value["source_after"] = "sha256:" + "0" * 64
        elif kind == "environment":
            value["environment_hash"] = "sha256:" + "0" * 64
        else:
            value["errors"].append("collection failed: unexpected.py")

    alter(data, change, artifact="phases")
    result = assess(data)
    assert result["verdict"] == "INCONCLUSIVE", result
    assert not result["negative_sensitivity_confirmed"]


@pytest.mark.parametrize("kind", ["crash", "wrong-run", "wrong-argv"])
def test_failed_attempt_boundary_is_inconclusive(red_pair, tmp_path, kind):
    data = mutable_evidence(red_pair, tmp_path)

    def change(value):
        if kind == "crash":
            value["returncode"] = -9
        elif kind == "wrong-run":
            value["run_id"] = "another-run"
        else:
            value["argv"][0] = "/untrusted/python"

    alter(data, change)
    assert assess(data)["verdict"] == "INCONCLUSIVE"


def test_failed_phase_with_green_junit_is_inconclusive(red_pair, tmp_path):
    data = mutable_evidence(red_pair, tmp_path)

    def change(path):
        tree = ET.parse(path)
        for case in tree.getroot().iter("testcase"):
            for failure in list(case.findall("failure")):
                case.remove(failure)
        for suite in tree.getroot().iter("testsuite"):
            suite.set("failures", "0")
        tree.write(path)

    alter(data, change, artifact="junit")
    assert assess(data)["verdict"] == "INCONCLUSIVE"


def test_missing_shard_and_full_receipt_cannot_stand_in_for_affected(red_pair, tmp_path):
    data = mutable_evidence(red_pair, tmp_path)
    path = data["full_evidence"] / "run.json"
    run = read_json(path)
    run["attempts"].pop()
    path.write_bytes(canonical(seal({k: v for k, v in run.items() if k != "content_hash"})))
    assert assess(data)["verdict"] == "INCONCLUSIVE"
    data = dict(red_pair)
    data["affected_evidence"] = data["full_evidence"]
    assert assess(data)["verdict"] == "INCONCLUSIVE"


def test_baseline_commits_cannot_be_stamped_onto_mutant_bytes(red_pair):
    data = copy.deepcopy(red_pair)
    commits = {
        name: char * 40
        for name, char in zip(sorted(data["control"]["source"]["components"]), "abc", strict=True)
    }
    for side in ("control", "full", "affected"):
        data[side]["source_commits"] = commits
        data[side] = seal({k: v for k, v in data[side].items() if k != "content_hash"})
        data[side + "_hash"] = data[side]["content_hash"]
    result = assess(data)
    assert result["verdict"] == "INCONCLUSIVE"
    assert "baseline producer commit stamped" in result["errors"][0]


def test_mutant_policy_cannot_drop_control_full_owner_gate(red_pair):
    from openpine.verification.identity import digest

    data = copy.deepcopy(red_pair)
    data["control_policy"] = copy.deepcopy(data["policy"])
    data["policy"]["required_gates"]["stage-full"] = ["packages"]
    for side in ("affected", "full"):
        data[side]["policy_hash"] = digest(data["policy"])
        if side == "full":
            data[side]["required_gates"] = ["packages"]
        data[side] = seal({k: v for k, v in data[side].items() if k != "content_hash"})
        data[side + "_hash"] = data[side]["content_hash"]
    result = assess(data)
    assert result["verdict"] == "INCONCLUSIVE"
    assert "control owner gates" in result["errors"][0]


@pytest.mark.parametrize("kind", ["scope", "producer", "plugins", "coverage-package"])
def test_independent_pair_contract_drift_is_inconclusive(red_pair, kind):
    data = copy.deepcopy(red_pair)
    if kind == "scope":
        data["expected_owners"] = ["tiny", "other"]
    elif kind == "producer":
        data["affected"]["source_commits"] = {"tiny": "a" * 40}
    elif kind == "plugins":
        data["affected"]["tasks"][0]["plugins"] = ["pytest_asyncio.plugin"]
    else:
        data["affected"]["tasks"][0]["coverage_package"] = "another_package"
    data["affected"] = seal({k: v for k, v in data["affected"].items() if k != "content_hash"})
    data["affected_hash"] = data["affected"]["content_hash"]
    assert assess(data)["verdict"] == "INCONCLUSIVE"
