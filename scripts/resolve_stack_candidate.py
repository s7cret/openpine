#!/usr/bin/env python3
"""Resolve exactly one active stack candidate and emit safe CI outputs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "openpine.stack-candidate.v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
CANDIDATE_FILENAME = re.compile(r"^stack-candidate-[A-Za-z0-9][A-Za-z0-9._-]*\.json$")


class CandidateSelectionError(RuntimeError):
    """Candidate manifests are missing required identity or are ambiguous."""


def resolve_candidate(root: Path) -> Path | None:
    candidates = sorted(root.resolve().glob("stack-candidate-*.json"))
    if len(candidates) > 1:
        names = [path.name for path in candidates]
        raise CandidateSelectionError(
            f"expected exactly one active candidate manifest, found {names}"
        )
    if not candidates:
        return None
    candidate = candidates[0]
    if CANDIDATE_FILENAME.fullmatch(candidate.name) is None:
        raise CandidateSelectionError(
            f"invalid candidate manifest filename: {candidate.name!r}"
        )
    return candidate


def load_candidate(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateSelectionError(f"invalid candidate manifest {path}: {exc}") from exc
    if payload.get("schema") != SCHEMA:
        raise CandidateSelectionError(f"unsupported candidate schema in {path}")
    if payload.get("not_a_release") is not True:
        raise CandidateSelectionError(f"candidate must be marked not_a_release: {path}")
    components = payload.get("components")
    if not isinstance(components, dict) or not components:
        raise CandidateSelectionError(f"candidate components missing: {path}")

    output_keys: set[str] = set()
    for name, row in components.items():
        if not isinstance(name, str) or not COMPONENT.fullmatch(name):
            raise CandidateSelectionError(f"invalid component name: {name!r}")
        output_key = name.replace("-", "_")
        if output_key in output_keys:
            raise CandidateSelectionError(f"duplicate component output key: {output_key}")
        output_keys.add(output_key)
        if not isinstance(row, dict):
            raise CandidateSelectionError(f"invalid component row: {name}")
        repository = row.get("repo")
        if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
            raise CandidateSelectionError(f"invalid repository for {name}: {repository!r}")
        sha = row.get("sha")
        if name == "openpine" and sha == "THIS_CHECKOUT":
            continue
        if not isinstance(sha, str) or not SHA40.fullmatch(sha):
            raise CandidateSelectionError(f"invalid sha for {name}: {sha!r}")
    return payload


def github_outputs(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return [("mode", "production"), ("candidate_path", "")]
    payload = load_candidate(path)
    rows = [("mode", "candidate"), ("candidate_path", path.name)]
    for name, component in sorted(payload["components"].items()):
        key = name.replace("-", "_")
        rows.append((f"{key}_repo", component["repo"]))
        rows.append((f"{key}_sha", component["sha"]))
    return rows


def write_github_outputs(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for key, value in rows:
            if "\n" in key or "\n" in value or "\r" in key or "\r" in value:
                raise CandidateSelectionError("multiline GitHub output is not allowed")
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    candidate = resolve_candidate(args.root)
    if candidate is not None:
        load_candidate(candidate)
    if args.github_output is not None:
        write_github_outputs(args.github_output, github_outputs(candidate))
    print(candidate.name if candidate is not None else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
