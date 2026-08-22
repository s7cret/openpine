#!/usr/bin/env python3
"""Build wheels for the 5.0 candidate. Paths come from CLI, never hardcoded."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


class CandidateError(RuntimeError):
    """Candidate checkout does not match the manifest."""


SHA40 = re.compile(r"^[0-9a-f]{40}$")


def load_candidate(path: Path) -> dict:
    resolver_path = Path(__file__).with_name("resolve_stack_candidate.py")
    spec = importlib.util.spec_from_file_location("resolve_stack_candidate", resolver_path)
    if spec is None or spec.loader is None:
        raise CandidateError("candidate resolver unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.load_candidate(path)
    except module.CandidateSelectionError as exc:
        raise CandidateError(str(exc)) from exc


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise CandidateError("git executable not found")
    return executable


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(  # noqa: S603
        [_git_executable(), *args], cwd=path, text=True
    ).strip()


def parse_checkouts(values: list[str]) -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for item in values:
        name, _, raw = item.partition("=")
        if not name or not raw:
            raise CandidateError(f"checkout must be NAME=PATH, got {item!r}")
        rows[name] = Path(raw).resolve()
    return rows


def verify_checkouts(candidate: dict, checkouts: dict[str, Path]) -> dict[str, str]:
    components = candidate["components"]
    resolved: dict[str, str] = {}
    missing = set(components) - set(checkouts)
    if missing:
        raise CandidateError(f"missing checkouts: {sorted(missing)}")
    extra = set(checkouts) - set(components)
    if extra:
        raise CandidateError(f"unknown checkouts: {sorted(extra)}")
    for name, spec in components.items():
        path = checkouts[name]
        if not (path / ".git").exists() and not (path / ".git").is_file():
            raise CandidateError(f"{name} is not a git checkout: {path}")
        dirty = _git(path, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise CandidateError(f"{name} dirty")
        head = _git(path, "rev-parse", "HEAD")
        expected = spec["sha"]
        if not isinstance(expected, str) or SHA40.fullmatch(expected) is None:
            raise CandidateError(f"{name} sha must be 40 lowercase hex")
        if head != expected:
            raise CandidateError(f"{name} sha mismatch: {head} != {expected}")
        resolved[name] = head
    return resolved


def build_wheel(src: Path, outdir: Path) -> None:
    subprocess.check_call(  # noqa: S603
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(outdir),
            str(src),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--checkout", action="append", default=[], required=True)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    candidate = load_candidate(args.candidate)
    checkouts = parse_checkouts(args.checkout)
    resolved = verify_checkouts(candidate, checkouts)
    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "resolved-shas.json").write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.build:
        for name, path in checkouts.items():
            print("building", name)
            build_wheel(path, args.outdir)
    print("ok", len(resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
