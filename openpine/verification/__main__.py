"""Read-only verification commands. Reports never modify sources or user datasets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

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
    index.add_argument("--diagnostic-provisional", action="store_true")
    remaining = commands.add_parser("stage2-remaining")
    remaining.add_argument("--host-root", type=Path, required=True)
    remaining.add_argument("--builtin-index", type=Path, required=True)
    remaining.add_argument("--output", type=Path, required=True)
    remaining.add_argument("--diagnostic-provisional", action="store_true")
    stage21_catalog = commands.add_parser("stage2-1-catalog")
    stage21_catalog.add_argument("--authority", type=Path, required=True)
    stage21_catalog.add_argument("--output", type=Path, required=True)
    stage21_lock = commands.add_parser("stage2-1-lock")
    stage21_lock.add_argument("--stack-root", type=Path, required=True)
    stage21_lock.add_argument("--authority", type=Path, required=True)
    stage21_lock.add_argument("--catalog-output", type=Path, required=True)
    stage21_lock.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--corpus", type=Path, required=True)
    compare.add_argument("--observations", type=Path, required=True)
    compare.add_argument("--expected-hash", required=True)
    compare.add_argument("--output", type=Path, required=True)
    stage = commands.add_parser("stage1")
    stage.add_argument("--host-root", type=Path, required=True)
    stage.add_argument("--stack-root", type=Path, required=True)
    stage.add_argument("--evidence", type=Path, required=True)
    from openpine.verification.execution_cli import add_commands, run_command

    add_commands(commands)
    args = parser.parse_args(argv)
    if args.command.startswith("test-"):
        return cast(int, run_command(args))
    if args.command == "stage1":
        from openpine.verification.stage_gate import run_stage_gate

        run_stage_gate(args.host_root, args.stack_root, args.evidence)
        return 0
    if args.command == "stage2-1-lock":
        from openpine.verification.stage2_catalog import (
            build_source_lock,
            build_version_exact_catalog,
        )
        from openpine.verification.execution_identity import ensure_external_outputs

        roots = {
            name: args.stack_root / name
            for name in (
                "openpine-contracts",
                "pine2ast",
                "ast2python",
                "pinelib",
                "backtest_engine",
                "marketdata-provider",
                "optimizer",
                "openpine",
            )
        }
        ensure_external_outputs((args.catalog_output, args.output), roots)
        matrix = build_version_exact_catalog(read_json(args.authority))
        write_json(args.catalog_output, matrix)
        report = build_source_lock(args.stack_root, matrix)
    elif args.command == "stage2-1-catalog":
        from openpine.verification.stage2_catalog import build_version_exact_catalog

        report = build_version_exact_catalog(read_json(args.authority))
    elif args.command == "stage2-remaining":
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
        surface_lock = read_json(args.surface_lock)
        if args.surface_lock.name == "stage2-callable-current-lock.json":
            from openpine.verification.callable_migration import reviewed_surface_lock
            reviewed = reviewed_surface_lock(args.host_root)
            if surface_lock != reviewed:
                raise ValueError("current surface lock differs from the reviewed migration")
        report = build_evidence_index(
            build_builtin_surface(),
            surface_lock,
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
    # Strict commands report semantic failure. Diagnostic provisional reporting
    # is explicit and never changes the sealed semantic result.
    provisional = (
        getattr(args, "diagnostic_provisional", False)
        and args.command in {"builtin-index", "stage2-remaining"}
        and report.get("diagnostic_provisional_ok") is True
        and report.get("ok") is False
        and report.get("full_stage2_accepted") is False
        and report.get("tradingview_verified") is False
    )
    # Inspection commands have no semantic verdict; strict commands must
    # supply an explicit successful owner result.
    inspection = args.command in {"capabilities", "builtin-surface", "stage2-1-lock"}
    return 0 if inspection or report.get("ok") is True or provisional else 1


if __name__ == "__main__":
    raise SystemExit(main())
