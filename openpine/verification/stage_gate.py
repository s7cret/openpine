"""Aggregate Stage 1 evidence. No unobserved execution can satisfy this gate."""

from __future__ import annotations

from pathlib import Path
import re

from openpine.verification.architecture import COMPONENTS, check_architecture
from openpine.verification.capabilities import build_capability_graph
from openpine.verification.conformance import compare_corpus, load_corpus
from openpine.verification.identity import read_json, seal, verify, write_json
from openpine.verification.pytest_gate import validate_inventory
from openpine.verification.review_ledger import (
    read_review_ledger, validate_remaining_projection, validate_review_ledger,
)


def validate_stages(plan: dict, ledger: dict) -> None:
    validate_review_ledger(ledger)
    if plan.get("schema_id") != "openpine.delivery_stages.v1":
        raise ValueError("invalid stage plan")
    if plan.get("source_spec_sha256") != ledger["source_spec_sha256"]:
        raise ValueError("stage plan is not bound to the original specification")
    if not re.fullmatch("[0-9a-f]{40}", plan.get("baseline", "")):
        raise ValueError("stage plan needs a source baseline")
    stages = plan.get("stages", [])
    if [s["id"] for s in stages] != list(range(1, 9)):
        raise ValueError("all eight stages must be present exactly once")
    tasks = list(plan.get("preserved_tasks", []))
    for stage in stages:
        if (
            not stage["title"]
            or not stage["exit_criteria"]
            or len(set(stage["exit_criteria"])) != len(stage["exit_criteria"])
        ):
            raise ValueError("stage needs distinct exit criteria")
        if any(
            type(n) is not int or not 1 <= n < stage["id"] for n in stage["depends_on"]
        ):
            raise ValueError("stage dependency is missing or cyclic")
        tasks.extend(stage["tasks"])
    expected = {r["id"] for r in ledger["tasks"]}
    if (
        set(tasks) != expected
        or len(tasks) != len(expected)
        or plan["preserved_tasks"] != ["OP-36"]
    ):
        raise ValueError("original task mapping is incomplete or duplicated")


def validate_capabilities(graph: dict, policy: dict) -> None:
    if policy.get("schema_id") != "openpine.required_capabilities.v1":
        raise ValueError("invalid capability policy")
    if len(graph["rows"]) < policy["minimum_rows"]:
        raise ValueError("installed capability denominator shrank unexpectedly")
    for required in policy["required"]:
        rows = [
            r
            for r in graph["rows"]
            if r["symbol_id"] == required["symbol_id"]
            and r["pine_version"] == required["pine_version"]
        ]
        if not rows or any(r["status"] != "BOUND" for r in rows):
            raise ValueError(
                "required capability chain is incomplete: " + str(required)
            )


def run_stage_gate(
    host: Path, stack: Path | dict[str, Path], evidence: Path, *, persist: bool = True
) -> dict:
    read_review_ledger(host)
    plan = read_json(host / "verification/stages.json")
    validate_stages(plan, read_json(host / "docs/RC6_REVIEW_36.json"))
    sources = read_json(host / "docs/RC6_LIFECYCLE_SOURCES.json")
    if sources != read_json(evidence / "source-pins.json"):
        raise ValueError("verification sources differ from the admitted stack")
    inventory_lock = read_json(host / "verification/inventory.json")
    if set(inventory_lock) != set(COMPONENTS):
        raise ValueError("test inventory must include all eight components")
    receipts = {}
    for name in sorted(COMPONENTS):
        receipt = read_json(evidence / (name + ".inventory.json"))
        if (
            receipt.get("suite") != name
            or not receipt.get("ok")
            or receipt.get("collect_only")
        ):
            raise ValueError("missing or unsuccessful mandatory test suite: " + name)
        validate_inventory(
            receipt["nodeids"], inventory_lock[name], receipt["deselected"]
        )
        receipts[name] = {k: receipt[k] for k in ("count", "sha256", "deselected")}
    architecture = check_architecture(stack, host_root=host)
    if persist:
        write_json(evidence / "architecture.json", architecture)
    if not architecture["ok"]:
        raise ValueError("component ownership violations")
    graphs = {}
    policy = read_json(host / "verification/capability-policy.json")
    for mode in ("interactive", "bulk_backtest"):
        graph = build_capability_graph(mode)
        if persist:
            write_json(evidence / ("capabilities-" + mode + ".json"), graph)
        validate_capabilities(graph, policy)
        graphs[mode] = {"hash": graph["content_hash"], "counts": graph["counts"]}
    corpus = host / "verification/corpus-v1/manifest.json"
    cases = load_corpus(corpus)["cases"]
    observations = {
        c["id"]: read_json(evidence / "observations" / (c["id"] + ".json"))
        for c in cases
    }
    result = compare_corpus(
        corpus,
        observations,
        expected_corpus_hash=read_json(host / "verification/corpus-lock.json")[
            "content_hash"
        ],
    )
    if persist:
        write_json(evidence / "conformance.json", result)
    if not result["ok"]:
        raise ValueError("critical manual corpus regression")
    report = seal(
        {
            "schema_id": "openpine.stage1_receipt.v1",
            "ok": True,
            "source_pins": sources,
            "test_inventory": receipts,
            "architecture_hash": architecture["content_hash"],
            "capability_graphs": graphs,
            "conformance_hash": result["content_hash"],
            "tradingview_verified": False,
            "scope": "stage1_foundation_not_full_OP03_12_15_32_35_acceptance",
        }
    )
    if persist:
        write_json(evidence / "stage1.json", report)
    return report


STABILIZATION_GATES = (
    "branch-reconciliation",
    "foundation",
    "protected-workers",
    "coverage",
    "frontend",
    "packages",
    "test-performance",
)
CURRENT_SCHEMA = "openpine.rc6_current_acceptance.v1"


def _owner_evidence_root(
    plan: dict, evidence: Path, entry: dict, gate: str, *, replay_paths: dict | None = None
) -> tuple[Path, dict]:
    """Select an independently frozen owner namespace without rewriting receipts.

    The caller has already validated the plan and any execution binding. Only
    the optional locator is removed; all producer descriptors remain verbatim.
    """
    if "evidence_subroot" not in entry:
        return evidence, entry
    from openpine.verification import stabilization_evidence as raw

    relative = entry["evidence_subroot"]
    if (
        not isinstance(relative, str) or not relative
        or Path(relative).is_absolute() or "\\" in relative or ":" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("unsafe owner evidence subroot")
    slot = {"frontend": "attempt", "packages": "package_attempt"}[gate]
    frozen = plan.get("owner_launch", {}).get("paths", {}).get(slot)
    if not frozen:
        raise ValueError("owner evidence subroot requires frozen launch path: " + slot)
    folder = raw.evidence_folder(evidence, relative)
    if folder.resolve() != raw.replay_path(frozen, replay_paths):
        raise ValueError("owner evidence subroot differs from frozen launch path: " + slot)
    return folder, {key: value for key, value in entry.items() if key != "evidence_subroot"}


def run_stabilization_gate(
    host: Path, plan: dict, evidence: Path, *, expected_plan_hash: str, run_id: str,
    binding: dict | None = None
) -> dict:
    """Extend the foundation owner with exact, fail-closed RC6 stabilization.

    Evidence is a locator packet, not authority. Its gate entries are never
    accepted from saved booleans. Each reader re-opens its primary inputs.
    Stage 2 is intentionally separate and cannot be promoted by this scope.
    """
    from openpine.verification.execution_campaign import aggregate_campaign
    from openpine.verification.execution_coverage import verify_task_coverage
    from openpine.verification.execution_identity import evidence_path, source_snapshot
    from openpine.verification.execution_plan import validate_plan
    from openpine.verification.identity import digest
    from openpine.verification import stabilization_evidence as raw

    validate_plan(plan, expected_hash=expected_plan_hash)
    from openpine.verification.execution_binding import checked_locations
    replay_roots, _ = checked_locations(plan, binding)
    roots = {name: Path(value) for name, value in replay_roots.items()}
    if roots.get("openpine", Path()).resolve() != host.resolve():
        raise ValueError("current host differs from plan")
    if source_snapshot(roots) != plan["source"]:
        raise ValueError("current candidate is stale")
    policy = read_json(host / "verification/execution-policy.json")
    if digest(policy) != plan["policy_hash"]:
        raise ValueError("current execution policy differs from plan")
    if policy.get('schema_id') == 'openpine.execution_policy.v2':
        from openpine.verification.execution_owner_launch import resolve_owner_policy
        if 'owner_launch' not in plan:
            raise ValueError('portable policy has no frozen owner launch')
        policy = resolve_owner_policy(policy, plan['owner_launch'])
    if plan["profile"] != "stage-full" or set(plan["required_gates"]) != set(
        STABILIZATION_GATES
    ):
        raise ValueError("stabilization requires the full named seven-gate plan")
    inventory = read_json(host / "verification/inventory.json")
    commits = plan.get("source_commits", {})
    source_pins = read_json(host / "docs/RC6_LIFECYCLE_SOURCES.json")
    if set(commits) != set(COMPONENTS) or any(
        commits.get(name) != value for name, value in source_pins.items()
    ):
        raise ValueError(
            "exact candidate source commits differ from current lifecycle pins"
        )
    if set(inventory) != set(COMPONENTS) or set(roots) != set(COMPONENTS):
        raise ValueError("stabilization requires all eight candidate components")
    for name in COMPONENTS:
        tasks = [t for t in plan["tasks"] if t["component"] == name]
        versions = {
            ".".join(
                plan["environments"][t["environment"]]["identity"]["python"].split(".")[
                    :2
                ]
            )
            for t in tasks
        }
        if not set(policy["components"][name]["pythons"]).issubset(versions):
            raise ValueError("mandatory interpreter missing: " + name)
        for task in tasks:
            validate_inventory(task["nodeids"], inventory[name], task["deselected"])
    packet = read_json(evidence_path(evidence, "stabilization-inputs.json"))
    if (
        packet.get("schema_id") != "openpine.rc6_stabilization_inputs.v1"
        or packet.get("plan_hash") != plan["content_hash"]
        or packet.get("candidate_hash") != plan["source"]["content_hash"]
        or packet.get("run_id") != run_id
    ):
        raise ValueError("stabilization packet is historical/stale or foreign")
    campaign = raw.evidence_folder(evidence, packet["campaign"])
    aggregation = aggregate_campaign(
        plan, campaign, expected_plan_hash=expected_plan_hash, expected_run_id=run_id
    )
    specs = policy.get("stabilization", {})
    entries = packet.get("gates", {})
    if set(entries) - set(STABILIZATION_GATES):
        raise ValueError("unexpected stabilization owner")
    gates = {}
    for gate in STABILIZATION_GATES:
        if not aggregation["pytest_scope_passed"]:
            gates[gate] = {"status": "blocked", "errors": aggregation["errors"]}
            continue
        if gate not in entries or gate not in specs:
            gates[gate] = {
                "status": "not_run",
                "errors": ["missing owner evidence or reviewed raw-evidence policy"],
            }
            continue
        entry, spec = entries[gate], specs[gate]
        try:
            owner_evidence = evidence
            if gate in {"frontend", "packages"}:
                owner_evidence, entry = _owner_evidence_root(
                    plan, evidence, entry, gate,
                    replay_paths=(binding or {}).get("owner_paths"),
                )
                import copy
                spec = copy.deepcopy(spec)
                harness = specs.get("package_harness_inputs")
                if not isinstance(harness, dict) or not harness:
                    raise ValueError("missing frozen harness input provenance policy")
                owners = [spec] if gate == "frontend" else [installation for version in spec.values() for installation in version.values()]
                for owner in owners:
                    for command in owner["commands"]:
                        command["inputs"] = {**command.get("inputs", {}), **harness}
            if gate == "branch-reconciliation":
                result = raw.verify_reconciliation(plan, host, spec)
            elif gate == "foundation":
                environments = {
                    key
                    for key in plan["environments"]
                    if {
                        t["component"] for t in plan["tasks"] if t["environment"] == key
                    }
                    == set(COMPONENTS)
                }
                if not environments or set(entry["environments"]) != environments:
                    raise ValueError("missing foundation interpreter export")
                # checked_locations already admitted the exact complete mapping.
                # Neither a historical policy path nor the host parent may replace it.
                foundation_sources = roots
                if source_snapshot(foundation_sources) != plan["source"]:
                    raise ValueError(
                        "foundation reader source roots differ from exact candidate"
                    )
                result = {}
                for environment, relative in entry["environments"].items():
                    folder = raw.evidence_folder(evidence, relative)
                    rebuilt = run_stage_gate(
                        host, foundation_sources, folder, persist=False
                    )
                    if rebuilt != read_json(folder / "stage1.json"):
                        raise ValueError(
                            "saved foundation receipt differs from raw replay"
                        )
                    for name in COMPONENTS:
                        receipt = read_json(folder / (name + ".inventory.json"))
                        if (
                            receipt.get("plan_hash") != plan["content_hash"]
                            or receipt.get("source_candidate_hash")
                            != plan["source"]["content_hash"]
                            or receipt.get("run_id") != run_id
                            or receipt.get("environment_hash")
                            != plan["environments"][environment]["identity"][
                                "content_hash"
                            ]
                            or receipt.get("verified_aggregate_hash")
                            != aggregation["content_hash"]
                        ):
                            raise ValueError("historical foundation suite export")
                    result[environment] = rebuilt["content_hash"]
            elif gate == "protected-workers":
                result = raw.verify_workers(plan, campaign, run_id, spec)
            elif gate == "coverage":
                tasks = [t["id"] for t in plan["tasks"] if t.get("coverage")]
                if (
                    not tasks
                    or set(entry["tasks"]) != set(tasks)
                    or len(tasks) != len(plan["tasks"])
                ):
                    raise ValueError("missing required coverage owner/interpreter")
                result = {
                    task: verify_task_coverage(
                        plan,
                        campaign,
                        task,
                        raw.evidence_folder(evidence, entry["tasks"][task]),
                        run_id=run_id, source_roots=roots,
                    )["content_hash"]
                    for task in tasks
                }
            elif gate == "frontend":
                result = raw.verify_frontend(plan, owner_evidence, entry, spec, replay_paths=(binding or {}).get("owner_paths"))
            elif gate == "packages":
                mandatory_package_versions = set().union(
                    *(set(row["pythons"]) for row in policy["components"].values())
                )
                if set(entry) != set(spec) or not mandatory_package_versions.issubset(
                    spec
                ):
                    raise ValueError("missing package interpreter")
                result = {
                    version: raw.verify_packages(
                        plan, owner_evidence, entry[version], specification,
                        replay_paths=(binding or {}).get("owner_paths"), source_roots=roots
                    )
                    for version, specification in spec.items()
                }
            else:
                result = raw.verify_performance(plan, evidence, entry, spec)
            gates[gate] = {
                "status": "passed",
                "raw_result_hash": digest(result),
                "errors": [],
            }
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            gates[gate] = {"status": "blocked", "errors": [str(error)]}
    accepted = all(row["status"] == "passed" for row in gates.values())
    # Frozen remaining matrix is authority for the language debt, not summaries
    # of historical test counts. This gate has no full-language promotion path.
    matrix = read_json(host / "verification/stage2-remaining-matrix.json")
    verify(matrix, "openpine.stage2_remaining_matrix.v1")
    remaining_binding = read_review_ledger(host, matrix)
    if (
        matrix["content_hash"]
        != read_json(host / "verification/stage2-remaining-matrix-lock.json")[
            "content_hash"
        ]
    ):
        raise ValueError("unreviewed Stage 2 remainder")
    return seal(
        {
            "schema_id": CURRENT_SCHEMA,
            "scope": "rc6_stabilization_not_full_language",
            "candidate_hash": plan["source"]["content_hash"],
            "source_commits": plan.get("source_commits", {}),
            "plan_hash": plan["content_hash"],
            "policy_hash": plan["policy_hash"],
            "inventory_hash": digest(inventory),
            "interpreters": {
                key: value["identity"] for key, value in plan["environments"].items()
            },
            "run_id": run_id,
            "pytest_aggregate_hash": aggregation["content_hash"],
            "stabilization": {
                "status": "accepted" if accepted else "blocked",
                "accepted": accepted,
                "gates": gates,
            },
            "stage2": {
                "status": "in_progress",
                "full_stage2_accepted": False,
                "matrix_hash": matrix["content_hash"],
                "criteria": matrix["criteria"],
                "remaining": matrix["items"],
                "remaining_spec_binding": remaining_binding,
            },
            "ok": accepted,
            "full_stage2_accepted": False,
            "full_release_accepted": False,
        }
    )


def current_views(current: dict, *, allow_legacy: bool = False) -> dict:
    """One validated current contract feeds progress, remainder and reporting."""
    from openpine.verification.identity import verify

    verify(current, CURRENT_SCHEMA)
    stage2 = current["stage2"]
    if (
        stage2["status"] != "in_progress"
        or stage2["full_stage2_accepted"] is not False
        or current["full_stage2_accepted"] is not False
    ):
        raise ValueError("stabilization is not full Stage 2 acceptance")
    accepted = all(
        current["stabilization"]["gates"].get(g, {}).get("status") == "passed"
        for g in STABILIZATION_GATES
    )
    if (
        set(current["stabilization"]["gates"]) != set(STABILIZATION_GATES)
        or current["stabilization"]["accepted"] is not accepted
        or current["ok"] is not accepted
        or current["stabilization"]["status"] != ("accepted" if accepted else "blocked")
    ):
        raise ValueError("current verdict contradicts required owner gates")
    common = {
        key: current[key]
        for key in ("candidate_hash", "plan_hash", "inventory_hash", "run_id")
    }
    remaining_binding = stage2.get("remaining_spec_binding")
    if remaining_binding is None:
        if not allow_legacy:
            raise ValueError("missing remaining specification projection")
    else:
        validate_remaining_projection(remaining_binding)
    return {
        "progress": {
            **common,
            "status": stage2["status"],
            "stabilization": current["stabilization"],
        },
        "remainder": {
            **common,
            "criteria": stage2["criteria"],
            "items": stage2["remaining"],
            "remaining_spec_binding": remaining_binding,
            "remaining_spec_scope": (
                "legacy_without_remaining_spec" if remaining_binding is None else "source_bound_accounting"
            ),
            "full_stage2_accepted": False,
        },
        "summary": {
            **common,
            "stabilization_status": current["stabilization"]["status"],
            "stage2_status": stage2["status"],
            "full_stage2_accepted": False,
        },
    }
