#!/usr/bin/env python3
"""Bind exact wheel bytes to a source-stage stack candidate.

The source-stage manifest is sufficient to check out immutable commits.  It is
not installable evidence until every expected distribution has exactly one
wheel, candidate-compatible metadata, and an exact internal dependency
closure.  This finalizer records those wheel identities and reseals the
manifest as ``stage=wheel-bound``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from email.parser import Parser
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

SCHEMA = "openpine.stack-candidate.v2"
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)(.*)$")


class CandidateFinalizationError(RuntimeError):
    """Wheel bytes or dependency metadata do not match the source candidate."""


def normalize_name(value: str) -> str:
    return value.replace("_", "-").replace(".", "-").lower()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def candidate_manifest_hash(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("manifest_hash", None)
    encoded = json.dumps(
        {"domain": SCHEMA, "payload": unsigned},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _wheel_metadata(path: Path) -> tuple[str, str, tuple[str, ...]]:
    try:
        with ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise CandidateFinalizationError(
                    f"wheel must contain one METADATA file: {path.name}"
                )
            parsed = Parser().parsestr(archive.read(names[0]).decode("utf-8"))
    except (BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise CandidateFinalizationError(f"invalid wheel: {path.name}") from exc
    name = parsed.get("Name")
    version = parsed.get("Version")
    if not name or not version:
        raise CandidateFinalizationError(f"wheel identity missing: {path.name}")
    return normalize_name(name), version, tuple(parsed.get_all("Requires-Dist", []) or ())


def _contract_schema_hashes(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    try:
        with ZipFile(path) as archive:
            names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("openpine_contracts/schemas/")
                and name.endswith(".json")
            )
            for name in names:
                content = archive.read(name)
                payload = json.loads(content)
                schema_id = payload.get("$id") if isinstance(payload, dict) else None
                if not isinstance(schema_id, str) or not schema_id:
                    raise CandidateFinalizationError(
                        f"contract schema identity missing: {name}"
                    )
                if schema_id in output:
                    raise CandidateFinalizationError(
                        f"duplicate contract schema identity: {schema_id}"
                    )
                output[schema_id] = "sha256:" + hashlib.sha256(content).hexdigest()
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateFinalizationError("invalid openpine-contracts schemas") from exc
    required = {"openpine.run.v2", "openpine.worker.protocol.v2"}
    if not required.issubset(output):
        raise CandidateFinalizationError(
            f"required contract schemas missing: {sorted(required - set(output))}"
        )
    return output


def _verify_internal_requirements(
    *,
    distribution: str,
    requirements: tuple[str, ...],
    versions: Mapping[str, str],
) -> None:
    for raw in requirements:
        body = raw.partition(";")[0].strip()
        match = REQUIREMENT.fullmatch(body)
        if match is None:
            continue
        dependency = normalize_name(match.group(1))
        if dependency not in versions:
            continue
        remainder = match.group(2).strip().replace(" ", "")
        if "@" in body or "git+" in body:
            raise CandidateFinalizationError(
                f"{distribution}: VCS stack dependency forbidden: {raw}"
            )
        wanted = f"=={versions[dependency]}"
        if remainder != wanted:
            raise CandidateFinalizationError(
                f"{distribution}: exact dependency required for {dependency}: "
                f"expected {wanted}, got {remainder or '<none>'}"
            )


def finalize_candidate(
    source_candidate: Mapping[str, Any], wheelhouse: Path
) -> dict[str, Any]:
    if source_candidate.get("schema") != SCHEMA or source_candidate.get("stage") != "source":
        raise CandidateFinalizationError("source-stage candidate required")
    if source_candidate.get("not_a_release") is not True:
        raise CandidateFinalizationError("candidate must be not_a_release")
    recorded_hash = source_candidate.get("manifest_hash")
    if not isinstance(recorded_hash, str) or HASH.fullmatch(recorded_hash) is None:
        raise CandidateFinalizationError("source manifest_hash invalid")
    if recorded_hash != candidate_manifest_hash(source_candidate):
        raise CandidateFinalizationError("source manifest_hash mismatch")
    raw_components = source_candidate.get("components")
    if not isinstance(raw_components, Mapping) or not raw_components:
        raise CandidateFinalizationError("candidate components missing")

    components_by_distribution: dict[str, tuple[str, Mapping[str, Any]]] = {}
    versions: dict[str, str] = {}
    for component, row in raw_components.items():
        if not isinstance(component, str) or not isinstance(row, Mapping):
            raise CandidateFinalizationError("invalid candidate component")
        distribution = normalize_name(component)
        version = row.get("version")
        if not isinstance(version, str) or not version:
            raise CandidateFinalizationError(f"component version missing: {component}")
        if distribution in components_by_distribution:
            raise CandidateFinalizationError(f"duplicate distribution: {distribution}")
        components_by_distribution[distribution] = (component, row)
        versions[distribution] = version

    wheels: dict[str, tuple[Path, str, tuple[str, ...]]] = {}
    for path in sorted(wheelhouse.glob("*.whl")):
        distribution, version, requirements = _wheel_metadata(path)
        if distribution in wheels:
            raise CandidateFinalizationError(f"duplicate wheel: {distribution}")
        wheels[distribution] = (path, version, requirements)
    expected = set(components_by_distribution)
    actual = set(wheels)
    if expected != actual:
        raise CandidateFinalizationError(
            f"wheel set mismatch: missing={sorted(expected - actual)} "
            f"extra={sorted(actual - expected)}"
        )

    payload = json.loads(json.dumps(source_candidate))
    payload["stage"] = "wheel-bound"
    for distribution, (component, _row) in components_by_distribution.items():
        wheel_path, wheel_version, requirements = wheels[distribution]
        expected_version = versions[distribution]
        if wheel_version != expected_version:
            raise CandidateFinalizationError(
                f"{distribution}: wheel version {wheel_version!r} != {expected_version!r}"
            )
        _verify_internal_requirements(
            distribution=distribution,
            requirements=requirements,
            versions=versions,
        )
        payload["components"][component]["wheel"] = {
            "filename": wheel_path.name,
            "sha256": file_sha256(wheel_path),
        }
    contracts = wheels.get("openpine-contracts")
    if contracts is None:
        raise CandidateFinalizationError("openpine-contracts wheel is required")
    payload["schema_hashes"] = _contract_schema_hashes(contracts[0])
    payload["manifest_hash"] = candidate_manifest_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        source = json.loads(args.candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateFinalizationError(f"invalid source candidate: {exc}") from exc
    payload = finalize_candidate(source, args.wheelhouse)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    print(payload["manifest_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
