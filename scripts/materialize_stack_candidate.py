#!/usr/bin/env python3
"""Materialize a self-contained immutable stack candidate outside source Git.

A repository commit cannot contain its own commit SHA without becoming
self-referential.  The committed template therefore omits only OpenPine's SHA;
CI injects the checked-out 40-hex identity and emits the resulting manifest as
an artifact.  The materialized bytes bind every component and provenance via a
verified manifest hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

TEMPLATE_SCHEMA = "openpine.stack-candidate-template.v1"
MATERIALIZED_SCHEMA = "openpine.stack-candidate.v2"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a|b|rc)[0-9]+$")
ADMISSION_LISTS = (
    "capabilities",
    "semantic_profiles",
    "finality_policies",
    "warmup_policies",
    "score_policies",
)


class CandidateMaterializationError(RuntimeError):
    """The template cannot produce an immutable candidate identity."""


def candidate_manifest_hash(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("manifest_hash", None)
    encoded = json.dumps(
        {"domain": MATERIALIZED_SCHEMA, "payload": unsigned},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA40.fullmatch(value) is None:
        raise CandidateMaterializationError(f"{label} must be 40 lowercase hex")
    return value


def _require_nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateMaterializationError(f"{label} must be a non-empty string")
    return value


def _materialize_admission(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise CandidateMaterializationError("candidate admission policy missing")
    if set(value) != set(ADMISSION_LISTS):
        raise CandidateMaterializationError(
            f"candidate admission policy must contain {list(ADMISSION_LISTS)!r}"
        )
    output: dict[str, list[str]] = {}
    for field in ADMISSION_LISTS:
        items = value.get(field)
        if (
            not isinstance(items, list)
            or not items
            or any(not isinstance(item, str) or not item for item in items)
            or len(set(items)) != len(items)
        ):
            raise CandidateMaterializationError(
                f"candidate admission {field} must be a unique non-empty string list"
            )
        output[field] = list(items)
    return output


def materialize_candidate(
    template: Mapping[str, Any],
    *,
    openpine_sha: str,
    created_at_utc: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    if template.get("schema") != TEMPLATE_SCHEMA:
        raise CandidateMaterializationError("unsupported candidate template schema")
    if template.get("not_a_release") is not True:
        raise CandidateMaterializationError("candidate template must be not_a_release")
    candidate_id = _require_nonempty_string(template.get("id"), label="candidate id")
    if RFC3339_UTC.fullmatch(created_at_utc) is None:
        raise CandidateMaterializationError("created_at_utc must be RFC3339 UTC seconds")
    if not isinstance(provenance, Mapping) or not provenance:
        raise CandidateMaterializationError("provenance must be a non-empty object")
    provenance_row = dict(provenance)
    for required in ("builder", "run_id"):
        _require_nonempty_string(provenance_row.get(required), label=f"provenance.{required}")

    raw_components = template.get("components")
    if not isinstance(raw_components, Mapping) or not raw_components:
        raise CandidateMaterializationError("candidate components missing")
    if "openpine" not in raw_components:
        raise CandidateMaterializationError("openpine component missing")
    admission = _materialize_admission(template.get("admission"))

    components: dict[str, dict[str, str]] = {}
    for name, raw_row in raw_components.items():
        if not isinstance(name, str) or COMPONENT.fullmatch(name) is None:
            raise CandidateMaterializationError(f"invalid component name: {name!r}")
        if not isinstance(raw_row, Mapping):
            raise CandidateMaterializationError(f"invalid component row: {name}")
        repo = raw_row.get("repo")
        if not isinstance(repo, str) or REPOSITORY.fullmatch(repo) is None:
            raise CandidateMaterializationError(f"invalid repository for {name}: {repo!r}")
        ref = _require_nonempty_string(raw_row.get("ref"), label=f"{name}.ref")
        version = _require_nonempty_string(
            raw_row.get("version"), label=f"{name}.version"
        )
        if VERSION.fullmatch(version) is None:
            raise CandidateMaterializationError(f"{name}.version must be a candidate version")
        sha = (
            _require_sha(openpine_sha, label="openpine sha")
            if name == "openpine"
            else _require_sha(raw_row.get("sha"), label=f"{name} sha")
        )
        components[name] = {
            "repo": repo,
            "ref": ref,
            "sha": sha,
            "version": version,
        }

    payload: dict[str, Any] = {
        "schema": MATERIALIZED_SCHEMA,
        "stage": "source",
        "id": candidate_id,
        "not_a_release": True,
        "created_at_utc": created_at_utc,
        "provenance": provenance_row,
        "admission": admission,
        "components": components,
    }
    worker_policy = template.get("worker_policy")
    if worker_policy is not None:
        if not isinstance(worker_policy, Mapping):
            raise CandidateMaterializationError("worker_policy must be an object")
        payload["worker_policy"] = dict(worker_policy)
    payload["manifest_hash"] = candidate_manifest_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--openpine-sha", required=True)
    parser.add_argument("--created-at-utc", required=True)
    parser.add_argument("--builder", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-uri")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateMaterializationError(f"invalid candidate template: {exc}") from exc
    provenance = {"builder": args.builder, "run_id": args.run_id}
    if args.source_uri:
        provenance["source_uri"] = args.source_uri
    payload = materialize_candidate(
        template,
        openpine_sha=args.openpine_sha,
        created_at_utc=args.created_at_utc,
        provenance=provenance,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    print(payload["manifest_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
