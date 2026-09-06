"""Aggregate Stage 1 evidence. No unobserved execution can satisfy this gate."""

from __future__ import annotations

from pathlib import Path
import re

from openpine.verification.architecture import COMPONENTS, check_architecture
from openpine.verification.capabilities import build_capability_graph
from openpine.verification.conformance import compare_corpus, load_corpus
from openpine.verification.identity import read_json, seal, write_json
from openpine.verification.pytest_gate import validate_inventory


def validate_stages(plan: dict, ledger: dict) -> None:
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
        if any(type(n) is not int or not 1 <= n < stage["id"] for n in stage["depends_on"]):
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
            raise ValueError("required capability chain is incomplete: " + str(required))


def run_stage_gate(host: Path, stack: Path, evidence: Path) -> dict:
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
        if receipt.get("suite") != name or not receipt.get("ok") or receipt.get("collect_only"):
            raise ValueError("missing or unsuccessful mandatory test suite: " + name)
        validate_inventory(receipt["nodeids"], inventory_lock[name], receipt["deselected"])
        receipts[name] = {k: receipt[k] for k in ("count", "sha256", "deselected")}
    architecture = check_architecture(stack, host_root=host)
    write_json(evidence / "architecture.json", architecture)
    if not architecture["ok"]:
        raise ValueError("component ownership violations")
    graphs = {}
    policy = read_json(host / "verification/capability-policy.json")
    for mode in ("interactive", "bulk_backtest"):
        graph = build_capability_graph(mode)
        write_json(evidence / ("capabilities-" + mode + ".json"), graph)
        validate_capabilities(graph, policy)
        graphs[mode] = {"hash": graph["content_hash"], "counts": graph["counts"]}
    corpus = host / "verification/corpus-v1/manifest.json"
    cases = load_corpus(corpus)["cases"]
    observations = {
        c["id"]: read_json(evidence / "observations" / (c["id"] + ".json")) for c in cases
    }
    result = compare_corpus(
        corpus,
        observations,
        expected_corpus_hash=read_json(host / "verification/corpus-lock.json")["content_hash"],
    )
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
    write_json(evidence / "stage1.json", report)
    return report
