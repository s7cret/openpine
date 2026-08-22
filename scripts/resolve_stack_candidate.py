#!/usr/bin/env python3
"""Resolve exactly one active stack candidate and emit safe CI outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "openpine.stack-candidate.v2"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
CANDIDATE_FILENAME = re.compile(r"^stack-candidate-[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a|b|rc)[0-9]+$")


class CandidateSelectionError(RuntimeError):
    """Candidate manifests are missing required identity or are ambiguous."""


def candidate_manifest_hash(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("manifest_hash", None)
    encoded = json.dumps(
        {"domain": SCHEMA, "payload": unsigned},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    stage = payload.get("stage")
    if stage not in {"source", "wheel-bound"}:
        raise CandidateSelectionError(f"candidate stage invalid: {path}")
    candidate_id = payload.get("id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise CandidateSelectionError(f"candidate id missing: {path}")
    created_at = payload.get("created_at_utc")
    if not isinstance(created_at, str) or RFC3339_UTC.fullmatch(created_at) is None:
        raise CandidateSelectionError(f"candidate created_at_utc invalid: {path}")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise CandidateSelectionError(f"candidate provenance missing: {path}")
    for field in ("builder", "run_id"):
        value = provenance.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CandidateSelectionError(f"candidate provenance.{field} missing: {path}")
    recorded_hash = payload.get("manifest_hash")
    if not isinstance(recorded_hash, str) or HASH.fullmatch(recorded_hash) is None:
        raise CandidateSelectionError(f"candidate manifest_hash invalid: {path}")
    if recorded_hash != candidate_manifest_hash(payload):
        raise CandidateSelectionError(f"candidate manifest_hash mismatch: {path}")
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
        ref = row.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise CandidateSelectionError(f"invalid ref for {name}: {ref!r}")
        version = row.get("version")
        if not isinstance(version, str) or VERSION.fullmatch(version) is None:
            raise CandidateSelectionError(f"invalid version for {name}: {version!r}")
        sha = row.get("sha")
        if not isinstance(sha, str) or not SHA40.fullmatch(sha):
            raise CandidateSelectionError(f"invalid sha for {name}: {sha!r}")
        if stage == "wheel-bound":
            wheel = row.get("wheel")
            if not isinstance(wheel, dict):
                raise CandidateSelectionError(f"wheel identity missing for {name}")
            filename = wheel.get("filename")
            digest = wheel.get("sha256")
            if not isinstance(filename, str) or not filename.endswith(".whl"):
                raise CandidateSelectionError(f"wheel filename invalid for {name}")
            if not isinstance(digest, str) or HASH.fullmatch(digest) is None:
                raise CandidateSelectionError(f"wheel sha256 invalid for {name}")
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
    parser.add_argument("--require-stage", choices=("source", "wheel-bound"))
    args = parser.parse_args()

    candidate = resolve_candidate(args.root)
    payload = None
    if candidate is not None:
        payload = load_candidate(candidate)
    if args.require_stage is not None:
        if payload is None:
            raise CandidateSelectionError(
                f"candidate stage {args.require_stage!r} required but no manifest found"
            )
        if payload.get("stage") != args.require_stage:
            raise CandidateSelectionError(
                f"candidate stage {args.require_stage!r} required, got {payload.get('stage')!r}"
            )
    if args.github_output is not None:
        write_github_outputs(args.github_output, github_outputs(candidate))
    print(candidate.name if candidate is not None else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
