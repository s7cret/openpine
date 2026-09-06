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
    if args.command == "architecture":
        from openpine.verification.architecture import check_architecture

        report = check_architecture(args.stack_root)
    elif args.command == "capabilities":
        from openpine.verification.capabilities import build_capability_graph

        report = build_capability_graph(args.mode)
    else:
        from openpine.verification.conformance import compare_corpus

        report = compare_corpus(
            args.corpus, read_json(args.observations), expected_corpus_hash=args.expected_hash
        )
    write_json(args.output, report)
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
