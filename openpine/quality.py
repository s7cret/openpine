"""Dependency-free quality gates for OpenPine release checks."""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "openpine.egg-info",
    "openpine-ui/node_modules",
}


@dataclass(frozen=True)
class ArchitectureReport:
    max_lines: int
    oversized_count: int
    oversized: list[dict[str, object]]


@dataclass(frozen=True)
class DuplicateReport:
    duplicate_group_count: int
    duplicate_groups: list[dict[str, object]]


def _python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        rel_parts = path.relative_to(root).parts
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        if rel_parts and rel_parts[0] == "tests":
            continue
        files.append(path)
    return sorted(files)


def architecture_report(root: Path, *, max_lines: int) -> ArchitectureReport:
    oversized: list[dict[str, object]] = []
    for path in _python_files(root):
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        if line_count > max_lines:
            oversized.append({"path": path.relative_to(root).as_posix(), "lines": line_count})
    return ArchitectureReport(max_lines=max_lines, oversized_count=len(oversized), oversized=oversized)


def _function_fingerprints(path: Path) -> Iterable[tuple[str, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    fingerprints: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            normalized = ast.dump(node, include_attributes=False)
            if node.name != "__init__" and len(normalized) > 900:
                fingerprints.append((node.name, normalized))
    return fingerprints


def duplicate_report(root: Path) -> DuplicateReport:
    buckets: dict[str, list[str]] = defaultdict(list)
    for path in _python_files(root):
        rel = path.relative_to(root).as_posix()
        for name, fingerprint in _function_fingerprints(path):
            buckets[fingerprint].append(f"{rel}:{name}")
    groups = [
        {"locations": sorted(locations)}
        for locations in buckets.values()
        if len(set(locations)) > 1
    ]
    groups.sort(key=lambda item: item["locations"])
    return DuplicateReport(duplicate_group_count=len(groups), duplicate_groups=groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openpine.quality")
    sub = parser.add_subparsers(dest="command", required=True)
    p_arch = sub.add_parser("architecture")
    p_arch.add_argument("root", nargs="?", default=".")
    p_arch.add_argument("--max-lines", type=int, default=4000)
    p_dup = sub.add_parser("duplicates")
    p_dup.add_argument("root", nargs="?", default=".")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if args.command == "architecture":
        report = architecture_report(root, max_lines=args.max_lines)
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
        return 1 if report.oversized_count else 0
    if args.command == "duplicates":
        report = duplicate_report(root)
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
        return 1 if report.duplicate_group_count else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
