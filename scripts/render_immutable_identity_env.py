#!/usr/bin/env python3
"""Render fail-closed producer identities for an immutable systemd deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "openpine.stack-candidate.v2"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_COMPONENTS = (
    "openpine",
    "pine2ast",
    "ast2python",
    "pinelib",
    "openpine-contracts",
)
COMPILER_COMPONENTS = (
    "pine2ast",
    "ast2python",
    "pinelib",
    "openpine-contracts",
)


class IdentityRenderError(RuntimeError):
    """The candidate cannot provide an exact immutable producer identity."""


def candidate_manifest_hash(candidate: Mapping[str, Any]) -> str:
    unsigned = dict(candidate)
    unsigned.pop("manifest_hash", None)
    encoded = json.dumps(
        {"domain": SCHEMA, "payload": unsigned},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _component_sha(components: Mapping[str, Any], name: str) -> str:
    row = components.get(name)
    if row is None:
        raise IdentityRenderError(f"required component missing: {name}")
    if not isinstance(row, Mapping):
        raise IdentityRenderError(f"invalid component row: {name}")
    sha = row.get("sha")
    if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
        raise IdentityRenderError(f"component sha must be 40 lowercase hex: {name}")
    wheel = row.get("wheel")
    if not isinstance(wheel, Mapping):
        raise IdentityRenderError(f"component wheel identity missing: {name}")
    filename = wheel.get("filename")
    wheel_sha = wheel.get("sha256")
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise IdentityRenderError(f"component wheel filename invalid: {name}")
    if not isinstance(wheel_sha, str) or HASH.fullmatch(wheel_sha) is None:
        raise IdentityRenderError(f"component wheel sha256 invalid: {name}")
    return sha


def render_identity_env(candidate: Mapping[str, Any]) -> str:
    """Return the exact non-secret identity environment for *candidate*."""

    if candidate.get("schema") != SCHEMA:
        raise IdentityRenderError("materialized stack candidate required")
    if candidate.get("stage") != "wheel-bound":
        raise IdentityRenderError("wheel-bound candidate stage required")
    recorded_hash = candidate.get("manifest_hash")
    if not isinstance(recorded_hash, str) or HASH.fullmatch(recorded_hash) is None:
        raise IdentityRenderError("candidate manifest_hash invalid")
    if recorded_hash != candidate_manifest_hash(candidate):
        raise IdentityRenderError("candidate manifest_hash mismatch")
    components = candidate.get("components")
    if not isinstance(components, Mapping):
        raise IdentityRenderError("candidate components missing")

    shas = {name: _component_sha(components, name) for name in REQUIRED_COMPONENTS}
    compiler_commits = {name: shas[name] for name in COMPILER_COMPONENTS}
    openpine_sha = shas["openpine"]
    lines = (
        f"OPENPINE_BUILD_COMMIT={openpine_sha}",
        f"OPENPINE_DEPLOYMENT_COMMIT={openpine_sha}",
        f"OPENPINE_PRODUCER_COMMIT={shas['pine2ast']}",
        "OPENPINE_PRODUCER_COMMITS_JSON=" + json.dumps(compiler_commits, separators=(",", ":")),
    )
    return "\n".join(lines) + "\n"


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.candidate.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise IdentityRenderError("candidate must be a JSON object")
        rendered = render_identity_env(payload)
        _write_private(args.output, rendered)
    except (OSError, json.JSONDecodeError, IdentityRenderError) as exc:
        print(f"identity render failed: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
