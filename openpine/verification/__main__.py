"""Read-only verification commands. Reports never modify sources or user datasets."""

from __future__ import annotations

import argparse
from pathlib import Path

from openpine.verification.identity import read_json, write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openpine.verification")
    commands = parser.add_subparsers(dest="command", required=True)
    arch = commands.add_parser("architecture")
    arch.add_argument("--stack-root", type=Path, required=True)
    arch.add_argument("--output", type=Path, required=True)
    graph = commands.add_parser("capabilities")
    graph.add_argument("--mode", choices=("interactive", "bulk_backtest"), default="interactive")
    graph.add_argument("--output", type=Path, required=True)
    builtins = commands.add_parser("builtin-surface")
    builtins.add_argument("--output", type=Path, required=True)
    builtin_expected = commands.add_parser("builtin-expected")
    builtin_expected.add_argument("--corpus", type=Path, required=True)
    builtin_expected.add_argument("--observations", type=Path, required=True)
    builtin_expected.add_argument("--assignments", type=Path, required=True)
    builtin_expected.add_argument("--expected-hash", required=True)
    builtin_expected.add_argument("--output", type=Path, required=True)
    index = commands.add_parser("builtin-index")
    index.add_argument("--host-root", type=Path, required=True)
    index.add_argument("--evidence", type=Path, required=True, action="append")
    index.add_argument("--surface-lock", type=Path, required=True)
    index.add_argument("--plan", type=Path, required=True)
    index.add_argument("--expected-plan-hash", required=True)
    index.add_argument("--output", type=Path, required=True)
    remaining = commands.add_parser("stage2-remaining")
    remaining.add_argument("--host-root", type=Path, required=True)
    remaining.add_argument("--builtin-index", type=Path, required=True)
    remaining.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--corpus", type=Path, required=True)
    compare.add_argument("--observations", type=Path, required=True)
    compare.add_argument("--expected-hash", required=True)
    compare.add_argument("--output", type=Path, required=True)
    stage = commands.add_parser("stage1")
    stage.add_argument("--host-root", type=Path, required=True)
    stage.add_argument("--stack-root", type=Path, required=True)
    stage.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "stage1":
        from openpine.verification.stage_gate import run_stage_gate

        run_stage_gate(args.host_root, args.stack_root, args.evidence)
        return 0
    if args.command == "stage2-remaining":
        from openpine.verification.stage2_remaining import build_stage2_remaining

        report = build_stage2_remaining(args.host_root, read_json(args.builtin_index))
    elif args.command == "builtin-index":
        from openpine.verification.builtins import build_builtin_surface
        from openpine.verification.evidence_index import build_evidence_index
        from openpine.verification.identity import verify

        plan = read_json(args.plan)
        verify(plan, "openpine.builtin_evidence_plan.v1")
        if plan["content_hash"] != args.expected_plan_hash:
            raise ValueError("evidence plan changed: review the required groups explicitly")
        report = build_evidence_index(
            build_builtin_surface(),
            read_json(args.surface_lock),
            plan,
            host_root=args.host_root,
            evidence_roots=args.evidence,
            source_pins=read_json(args.host_root / "docs/RC6_LIFECYCLE_SOURCES.json"),
        )
    elif args.command == "architecture":
        from openpine.verification.architecture import check_architecture

        report = check_architecture(args.stack_root)
    elif args.command == "capabilities":
        from openpine.verification.capabilities import build_capability_graph

        report = build_capability_graph(args.mode)
    elif args.command in {"builtin-surface", "builtin-expected"}:
        from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report

        report = build_builtin_surface()
        if args.command == "builtin-expected":
            report = builtin_evidence_report(
                report,
                args.corpus,
                read_json(args.observations),
                corpus_hash=args.expected_hash,
                assignments=read_json(args.assignments),
            )
    else:
        from openpine.verification.conformance import compare_corpus

        report = compare_corpus(
            args.corpus, read_json(args.observations), expected_corpus_hash=args.expected_hash
        )
    write_json(args.output, report)
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
