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
    validate_stage2_projection,
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

    if current.get("schema_id") == PRODUCT_CURRENT_SCHEMA:
        return _product_current_views(current)
    verify(current, CURRENT_SCHEMA)
    stage2 = current["stage2"]
    if (
        stage2["status"] != "in_progress"
        or stage2["full_stage2_accepted"] is not False
        or current["full_stage2_accepted"] is not False
        or current.get("full_release_accepted") is not False
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
    validate_stage2_projection(stage2)
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


PRODUCT_CURRENT_SCHEMA = "openpine.product_current_acceptance.v1"
PRODUCT_GATES = (
    "candidate-integrity", "static-build-quality", "functional-matrix",
    "language", "independent-oracle", "data-request", "lifecycle-resume",
    "broker", "optimizer", "frontend", "packages", "performance",
    "fault-sandbox", "delivery",
)


def product_requirements() -> dict[str, list[str]]:
    """Frozen §10.2 governance; every one of the 68 source IDs remains required."""
    def group(prefix: str, count: int) -> list[str]:
        return [f"{prefix}-{n:02}" for n in range(1, count + 1)]

    return {
        "candidate-integrity": ["INT-01", "REL-01", "REL-02", "REL-08"],
        "static-build-quality": ["INT-02", "REL-01"],
        "functional-matrix": ["INT-03", "INT-05", "LANG-11", "REL-01"],
        "language": group("LANG", 8) + ["LANG-10", "LANG-11", "LANG-12"],
        "independent-oracle": ["LANG-09", "LANG-10", "LANG-11", "LANG-12"],
        "data-request": group("DATA", 8),
        "lifecycle-resume": group("RUN", 8),
        "broker": group("BROKER", 8),
        "optimizer": group("OPT", 5),
        "frontend": group("UI", 7),
        "packages": ["REL-01", "REL-02", "REL-03"],
        "performance": ["INT-06", *group("PERF", 4)],
        "fault-sandbox": ["INT-04", "RUN-05", "RUN-06", "RUN-07", "RUN-08"],
        "delivery": ["INT-07", "INT-08", *group("REL", 8)[3:]],
    }


def _validate_product_policy(policy: dict, requirement_ids: set[str]) -> None:
    from openpine.verification.review_ledger import PYTHON_SUPPORT, SOURCE_SHA256
    from openpine.verification.execution_plan import _nodes

    required = product_requirements()
    if (
        not isinstance(policy, dict)
        or set(policy) != {"schema_id", "source_spec_sha256", "python_support", "domains"}
        or policy["schema_id"] != "openpine.product_domain_policy.v1"
        or policy["source_spec_sha256"] != SOURCE_SHA256
        or policy["python_support"] != PYTHON_SUPPORT
        or set(policy["domains"]) != set(PRODUCT_GATES)
        or set().union(*(set(ids) for ids in required.values())) != requirement_ids
    ):
        raise ValueError("product policy lost a required domain/source obligation")
    for gate, ids in required.items():
        row = policy["domains"][gate]
        if not isinstance(row, dict) or set(row) != {"requirements", "obligations"} or row["requirements"] != ids:
            raise ValueError("product domain requirement governance changed: " + gate)
        obligations = row["obligations"]
        if obligations is None:
            continue  # Unspecified full-scope owner remains not_run.
        if not isinstance(obligations, dict) or set(obligations) != set(ids):
            raise ValueError("missing substantive requirement obligations: " + gate)
        for requirement, spec in obligations.items():
            if not isinstance(spec, dict) or set(spec) != {"nodes", "commands"} or not spec["nodes"] or not spec["commands"]:
                raise ValueError("empty product requirement obligation: " + requirement)
            for component, nodes in spec["nodes"].items():
                if component not in COMPONENTS:
                    raise ValueError("unknown product requirement owner")
                _nodes(nodes)
            for command in spec["commands"]:
                if (
                    not isinstance(command, dict)
                    or not command.get("inputs")
                    or (gate != "static-build-quality" and command.get("expected_stdout") is None)
                ):
                    raise ValueError("product command needs frozen inputs and independent expected")


def _product_projection(stabilization: dict, gates: dict) -> tuple[list[dict], bool]:
    mapping = product_requirements()
    rows = stabilization["stage2"]["remaining_spec_binding"]["unclosed_requirements"]
    projected = []
    for row in rows:
        required = [gate for gate, ids in mapping.items() if row["id"] in ids]
        passed = bool(required) and all(gates[g]["status"] == "passed" for g in required)
        projected.append({**row, "required_domains": required,
                          "status": "qualified" if passed else "unclosed",
                          "remaining_reason": "" if passed else row["remaining_reason"]})
    accepted = stabilization["ok"] and all(gates[g]["status"] == "passed" for g in PRODUCT_GATES)
    accepted = accepted and not any(row["status"] != "qualified" for row in projected)
    return projected, accepted


def _checked_product_pending(plan: dict, policy: dict, locations: dict) -> dict:
    from openpine.verification.required_inventory import pending_required_inventories

    inventories = {task["component"] + "@" + task["environment"]:
                   {"nodeids": task["nodeids"], "deselected": task["deselected"]}
                   for task in plan["tasks"]}
    pending = pending_required_inventories(policy, {n:Path(p) for n,p in locations.items()},
        plan["source"], inventories, list(COMPONENTS))
    if plan.get("pending_required_inventories", {}) != pending:
        raise ValueError("saved external inventory differs from reviewed source obligations")
    for task in plan["tasks"]:
        count = task["deselected"]
        covered = len(pending.get(task["component"], {}).get("nodeids", []))
        if type(count) is not int or count != covered:
            raise ValueError("unaccounted mandatory deselected cases: " + task["component"])
    return pending


def run_product_gate(
    host: Path, plan: dict, evidence: Path, *, expected_plan_hash: str, run_id: str,
    binding: dict | None = None,
) -> dict:
    """Extend this same owner with all product domains; reopen native primaries.

    Source-reviewed obligations identify full owner cases and independently
    expected commands. Locator packets carry no status/expected authority.
    Until the full specifications/primaries exist, domains remain visibly open.
    """
    from openpine.verification import stabilization_evidence as raw
    from openpine.verification.execution_identity import evidence_path
    from openpine.verification.identity import digest
    from openpine.verification.execution_owner_launch import resolve_owner_policy
    from openpine.verification.execution_binding import checked_locations

    base = run_stabilization_gate(host, plan, evidence, expected_plan_hash=expected_plan_hash,
                                  run_id=run_id, binding=binding)
    policy = read_json(host / "verification/execution-policy.json")
    if policy.get("schema_id") == "openpine.execution_policy.v2":
        policy = resolve_owner_policy(policy, plan["owner_launch"])
    locations, _ = checked_locations(plan, binding)
    pending = _checked_product_pending(plan, policy, locations)
    specification = policy.get("product_acceptance")
    requirement_ids = {row["id"] for row in base["stage2"]["remaining_spec_binding"]["unclosed_requirements"]}
    if specification is not None:
        _validate_product_policy(specification, requirement_ids)
    path = evidence_path(evidence, "product-inputs.json", must_exist=False)
    packet = read_json(path) if path.is_file() else None
    if packet is not None and (
        set(packet) != {"schema_id", "plan_hash", "candidate_hash", "policy_hash", "run_id", "domains"}
        or packet["schema_id"] != "openpine.product_inputs.v1"
        or packet["plan_hash"] != plan["content_hash"]
        or packet["candidate_hash"] != plan["source"]["content_hash"]
        or packet["policy_hash"] != plan["policy_hash"]
        or packet["run_id"] != run_id
        or not isinstance(packet["domains"], dict)
        or set(packet["domains"]) - set(PRODUCT_GATES)
    ):
        raise ValueError("product locator packet is stale/foreign or carries authority")
    gates = {}
    for gate in PRODUCT_GATES:
        spec = specification["domains"][gate]["obligations"] if specification else None
        entry = packet["domains"].get(gate) if packet else None
        if spec is None or entry is None:
            gates[gate] = {"status": "not_run", "errors": ["missing reviewed full-scope obligations or raw owner inputs"]}
            continue
        try:
            if not base["ok"]:
                raise ValueError("required stabilization owners have not all passed")
            if gate in {"functional-matrix", "data-request"} and pending:
                raise ValueError("mandatory external provider cases remain NOT_EXECUTED")
            if not isinstance(entry, dict) or set(entry) != set(spec):
                raise ValueError("missing/extra product requirement primary inputs")
            results = {}
            for requirement, obligation in spec.items():
                for component, nodes in obligation["nodes"].items():
                    tasks = [task for task in plan["tasks"] if task["component"] == component]
                    if not tasks or any(not set(nodes).issubset(task["nodeids"]) for task in tasks):
                        raise ValueError("missing full requirement case/interpreter: " + requirement)
                results[requirement] = raw.command_set(evidence, entry[requirement], obligation["commands"])
                for receipt in results[requirement]:
                    if receipt.get("binding") != {"plan_hash": plan["content_hash"],
                            "candidate_hash": plan["source"]["content_hash"], "run_id": run_id}:
                        raise ValueError("copied/stale product command execution binding")
            gates[gate] = {"status": "passed", "raw_result_hash": digest(results), "errors": []}
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            gates[gate] = {"status": "blocked", "errors": [str(error)]}
    requirements, accepted = _product_projection(base, gates)
    return seal({
        "schema_id": PRODUCT_CURRENT_SCHEMA, "scope": "full_product_required_domains",
        **{key: base[key] for key in ("candidate_hash", "source_commits", "plan_hash", "policy_hash", "inventory_hash", "run_id")},
        "stabilization_current": base,
        "product": {"status": "accepted" if accepted else "blocked", "accepted": accepted,
                    "domain_policy_hash": digest(specification), "gates": gates,
                    "pending_required_inventories": pending,
                    "requirements": requirements, "requirement_count": len(requirements)},
        "ok": accepted, "full_stage2_accepted": accepted, "full_release_accepted": accepted,
    })


def _product_current_views(current: dict) -> dict:
    from openpine.verification.identity import digest

    verify(current, PRODUCT_CURRENT_SCHEMA)
    base = current["stabilization_current"]
    current_views(base)  # Preserve all historical accounting and seven-gate checks.
    for key in ("candidate_hash", "source_commits", "plan_hash", "policy_hash", "inventory_hash", "run_id"):
        if current[key] != base[key]:
            raise ValueError("product projection differs from the checked exact candidate")
    product = current["product"]
    gates = product["gates"]
    if set(gates) != set(PRODUCT_GATES):
        raise ValueError("product projection lost a mandatory domain")
    for row in gates.values():
        if row.get("status") not in {"passed", "not_run", "blocked"} or not isinstance(row.get("errors"), list):
            raise ValueError("invalid product domain status/errors")
        if row["status"] == "passed":
            if row["errors"] or re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("raw_result_hash", "")) is None:
                raise ValueError("passed product domain has no checked primary identity")
        elif not row["errors"]:
            raise ValueError("unclosed product domain needs a concrete reason")
    requirements, accepted = _product_projection(base, gates)
    if (
        product["requirements"] != requirements or product["requirement_count"] != 68
        or product["accepted"] is not accepted or current["ok"] is not accepted
        or current["full_stage2_accepted"] is not accepted or current["full_release_accepted"] is not accepted
        or (accepted and product.get("pending_required_inventories"))
        or product["status"] != ("accepted" if accepted else "blocked")
    ):
        raise ValueError("product verdict contradicts mandatory domains/requirements")
    common = {key: current[key] for key in ("candidate_hash", "plan_hash", "inventory_hash", "run_id")}
    common.update({"current_hash": current["content_hash"], "full_stage2_accepted": accepted,
                   "full_release_accepted": accepted})
    summary = {**common, "status": product["status"], "stabilization_status": base["stabilization"]["status"],
               "required_domains": len(PRODUCT_GATES), "passed_domains": sum(r["status"] == "passed" for r in gates.values()),
               "unclosed_requirements": sum(r["status"] != "qualified" for r in requirements)}
    return {
        "progress": {**common, "status": product["status"], "domains": gates},
        "remainder": {**common, "items": [r for r in requirements if r["status"] != "qualified"],
                      "pending_required_inventories": product.get("pending_required_inventories", {}),
                      "stage2_items": base["stage2"]["remaining"], "criteria": base["stage2"]["criteria"],
                      "remaining_spec_binding": base["stage2"]["remaining_spec_binding"]},
        "summary": summary, "api": {**summary, "domains": gates},
        "documentation": summary,
        "release": {**summary, "source_commits": current["source_commits"],
                    "domain_policy_hash": product["domain_policy_hash"], "requirements_hash": digest(requirements)},
    }
