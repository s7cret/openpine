"""Read-only negative sensitivity over the existing campaign/primary owners.

A confirmed intentional regression never grants pytest, stage or release PASS.
All three executions are real, independently anchored, complete campaign runs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from int05_tests.check_full_comparison import instrumentation_contract
from openpine.verification.execution_binding import validate_binding
from openpine.verification.execution_campaign import aggregate_campaign, compiler_commit_environment
from openpine.verification.execution_identity import (
    ensure_external_output,
    evidence_path,
    read_artifact,
    write_once_json,
)
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import digest, read_json


def obligation(task, node):
    return (task["id"], node, task["variant"], task["execution_path"], task["mode"])


def read_calls(plan, evidence, *, expected_hash, run_id):
    """Delegate raw admission; explain every standard aggregate error exactly."""
    from openpine.verification.execution_campaign import admit_failed_call_attempt

    validate_plan(plan, expected_hash=expected_hash)
    report = aggregate_campaign(
        plan, evidence, expected_plan_hash=expected_hash, expected_run_id=run_id
    )
    run = read_json(evidence_path(evidence, "run.json"))
    if run.get("merged_fragments") is not None or run.get("is_fragment") is not False:
        raise ValueError("negative sensitivity requires a complete actual campaign")
    binding = read_artifact(evidence, run["binding"]) if run.get("binding") else None
    if binding:
        validate_binding(plan, binding)
    expected = {(t["id"], s["id"]): (t, s) for t in plan["tasks"] for s in t["shards"]}
    observed = [(a.get("task"), a.get("shard")) for a in run["attempts"]]
    if len(set(observed)) != len(observed) or set(observed) != set(expected):
        raise ValueError("missing/duplicate/unexpected negative campaign shard")
    failures, explained = set(), []
    admitted_nodes = 0
    for attempt in run["attempts"]:
        key = (attempt["task"], attempt["shard"])
        task, shard = expected[key]
        if attempt.get("status") == "completed" and attempt.get("returncode") == 0:
            # The standard aggregator authenticates every green shard, including
            # its source/environment bindings, all phases, markers and JUnit.
            continue
        phases = admit_failed_call_attempt(
            plan, evidence, task, shard, attempt, expected_run_id=run_id, binding=binding
        )
        failures.update(
            obligation(task, node)
            for node in shard["nodeids"]
            if phases["reports"][node][1]["outcome"] == "failed"
        )
        admitted_nodes += len(shard["nodeids"])
        explained.append(
            key[0] + "/" + key[1] + ": shard failed/cancelled/crashed/timed out or never ran"
        )
    if failures:
        explained.append("executed obligation union differs from required plan")
    if report["errors"] != explained:
        raise ValueError(
            "aggregate errors are not exactly admitted semantic failures: " + repr(report["errors"])
        )
    if (
        report["is_fragment"]
        or report["observed_shards"] != len(expected)
        or report["executed_obligations"] + admitted_nodes != report["required_obligations"]
    ):
        raise ValueError("incomplete negative obligation union")
    return report, failures


def _compare(
    policy,
    control,
    affected,
    full,
    *,
    control_evidence,
    affected_evidence,
    full_evidence,
    control_hash,
    affected_hash,
    full_hash,
    control_run,
    affected_run,
    full_run,
    expected_owners,
    oracle_owner,
    oracle_node,
    control_policy=None,
):
    for plan, anchor in ((control, control_hash), (affected, affected_hash), (full, full_hash)):
        validate_plan(plan, expected_hash=anchor)
        selected_policy = (
            (policy if control_policy is None else control_policy) if plan is control else policy
        )
        if plan["policy_hash"] != digest(selected_policy):
            raise ValueError("negative plan differs from independently frozen policy")
        if plan["required_gates"] != selected_policy.get("required_gates", {}).get(
            plan["profile"], []
        ):
            raise ValueError("negative plan dropped an owner gate")
    if (
        control["profile"] != "stage-full"
        or full["profile"] != "stage-full"
        or affected["profile"] != "affected"
    ):
        raise ValueError("negative sensitivity needs control-full and actual mutant full/affected")
    if control["required_gates"] != full["required_gates"]:
        raise ValueError("mutant full changed the independently frozen control owner gates")
    for plan in (control, full):
        if {t["component"] for t in plan["tasks"]} != set(policy["components"]):
            raise ValueError("negative full boundary omitted a required owner")
    for field in ("source", "source_commits", "environments", "policy_hash"):
        if affected.get(field) != full.get(field):
            raise ValueError("negative pair identity drift: " + field)
    if control["source"] == full["source"]:
        raise ValueError("negative postimage is the unchanged green control")
    if "source_commits" in control or "source_commits" in full:
        for plan in (control, full):
            commits = plan.get("source_commits", {})
            if set(commits) != set(plan["source"]["components"]) or any(
                not isinstance(sha, str) or re.fullmatch("[0-9a-f]{40}", sha) is None
                for sha in commits.values()
            ):
                raise ValueError("incomplete exact negative producer attestation")
        for name, component in full["source"]["components"].items():
            if component != control["source"]["components"].get(name) and full["source_commits"][
                name
            ] == control["source_commits"].get(name):
                raise ValueError("baseline producer commit stamped onto mutant source: " + name)
    for plan in (control, full):
        if any(t["component"] == "openpine" for t in plan["tasks"]):
            compiler_commit_environment(plan.get("source_commits", {}))
    if sorted(t["component"] for t in affected["tasks"]) != sorted(expected_owners):
        raise ValueError("negative scope differs from independent expectation")
    control_tasks = {t["id"]: t for t in control["tasks"]}
    full_tasks = {t["id"]: t for t in full["tasks"]}
    if set(control_tasks) != set(full_tasks) or control["environments"] != full["environments"]:
        raise ValueError("negative control matrix/interpreter drift")
    for tasks, references in ((full["tasks"], control_tasks), (affected["tasks"], full_tasks)):
        for task in tasks:
            reference = references.get(task["id"])
            if reference is None or any(
                task[k] != reference[k]
                for k in (
                    "nodeids",
                    "nodeids_hash",
                    "full_inventory_hash",
                    "reviewed_lock_hash",
                    "deselected",
                    "variant",
                    "execution_path",
                    "mode",
                )
            ):
                raise ValueError("negative obligation inventory drift")
            if instrumentation_contract(task) != instrumentation_contract(reference):
                raise ValueError("negative instrumentation drift")
    expected_oracles = {
        obligation(t, oracle_node)
        for t in full["tasks"]
        if t["component"] == oracle_owner and oracle_node in t["nodeids"]
    }
    if not expected_oracles:
        raise ValueError("independent oracle is outside the full reviewed inventory")
    control_report = aggregate_campaign(
        control,
        Path(control_evidence),
        expected_plan_hash=control_hash,
        expected_run_id=control_run,
    )
    if not control_report["pytest_scope_passed"] or control_report["is_fragment"]:
        raise ValueError("complete control execution is not green")
    full_report, full_failures = read_calls(
        full, Path(full_evidence), expected_hash=full_hash, run_id=full_run
    )
    affected_report, affected_failures = read_calls(
        affected, Path(affected_evidence), expected_hash=affected_hash, run_id=affected_run
    )
    missing = full_failures - affected_failures
    extra = affected_failures - full_failures
    if not expected_oracles.issubset(full_failures):
        verdict = "INCONCLUSIVE"
        errors = ["independent oracle did not genuinely fail in full"]
    elif missing:
        verdict, errors = "FALSE_NEGATIVE", ["full failures were omitted or passed in affected"]
    elif extra:
        verdict, errors = "INCONCLUSIVE", ["unexpected affected-only failures"]
    else:
        verdict, errors = "SENSITIVITY_CONFIRMED", []
    return {
        "verdict": verdict,
        "negative_sensitivity_confirmed": verdict == "SENSITIVITY_CONFIRMED",
        "errors": errors,
        "full_failures": sorted(full_failures),
        "affected_failures": sorted(affected_failures),
        "missing_affected_failures": sorted(missing),
        "unexpected_affected_only_failures": sorted(extra),
        "control": control_report,
        "full": full_report,
        "affected": affected_report,
        "control_plan_hash": control_hash,
        "full_plan_hash": full_hash,
        "affected_plan_hash": affected_hash,
        "mutant_source_hash": full["source"]["content_hash"],
        "expected_owners": sorted(expected_owners),
        "independent_oracles": sorted(expected_oracles),
        "actual_affected_execution_required": True,
        "full_acceptance": False,
        "scope": "intentional product failure sensitivity; failed pytest runs remain failed",
    }


def compare_negative(*args, **kwargs):
    try:
        return _compare(*args, **kwargs)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {
            "verdict": "INCONCLUSIVE",
            "negative_sensitivity_confirmed": False,
            "errors": [str(error)],
            "full_acceptance": False,
            "scope": "raw admission or independently frozen contract rejected",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--control-policy", type=Path)
    for side in ("control", "affected", "full"):
        parser.add_argument("--" + side + "-plan", type=Path, required=True)
        parser.add_argument("--" + side + "-evidence", type=Path, required=True)
        parser.add_argument("--" + side + "-hash", required=True)
        parser.add_argument("--" + side + "-run", required=True)
    parser.add_argument("--expected-owner", action="append", required=True)
    parser.add_argument("--oracle-owner", required=True)
    parser.add_argument("--oracle-node", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    kw = {
        name: getattr(args, name)
        for name in vars(args)
        if name
        not in {
            "policy",
            "control_policy",
            "control_plan",
            "affected_plan",
            "full_plan",
            "output",
            "expected_owner",
        }
    }
    kw["control_policy"] = read_json(args.control_policy) if args.control_policy else None
    kw["expected_owners"] = args.expected_owner
    result = compare_negative(
        read_json(args.policy),
        read_json(args.control_plan),
        read_json(args.affected_plan),
        read_json(args.full_plan),
        **kw,
    )
    for path in (args.control_plan, args.full_plan, args.affected_plan):
        ensure_external_output(
            args.output, {n: Path(p) for n, p in read_json(path)["roots"].items()}
        )
    write_once_json(args.output, result)
    print(result["verdict"])
    return 0 if result["negative_sensitivity_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
