"""Closed public projection; raw qualification primaries are never copied."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

SHA = re.compile(r"[0-9a-f]{40}\Z")
COMPONENTS = {"openpine", "openpine-contracts", "pine2ast", "ast2python",
              "pinelib", "backtest_engine", "marketdata-provider", "optimizer"}
TOP = {"schema_id", "candidate_sha", "source_commits", "allowlist_sha256", "cases",
       "protected_matrix_passed", "owner_checks_passed", "raw_primaries_durable", "full_qualification_accepted"}
CASE = {"placement", "mode", "fault", "ok", "automatic_cleanup",
        "neighbour_survived", "neighbour_completed"}


def project(candidate_sha: str, commits: dict[str, str], rows: list[dict[str, Any]],
            allowlist: Path, *, owner_checks_passed: bool = False) -> dict[str, Any]:
    policy = json.loads(allowlist.read_bytes())
    if (set(policy["top_fields"]) != TOP or set(policy["case_fields"]) != CASE
            or policy["files"] != ["projection.json", "projection.sha256"]
            or policy["max_complete_upload_bytes"] != 67108864
            or policy["retention_days"] != 1):
        raise ValueError("public projection policy differs from its closed contract")
    if (not SHA.fullmatch(candidate_sha) or set(commits) != COMPONENTS
            or commits["openpine"] != candidate_sha
            or any(not isinstance(value, str) or not SHA.fullmatch(value) for value in commits.values())):
        raise ValueError("public candidate identity is invalid")
    if type(owner_checks_passed) is not bool:
        raise ValueError("public owner outcome must be a boolean")
    cases, identities = [], set()
    for row in rows:
        identity = tuple(row.get(k) for k in ("placement", "mode", "fault"))
        for index, key in enumerate(("placement", "mode", "fault")):
            if identity[index] not in policy["permitted_" + key + "s"]:
                raise ValueError("public case identity is outside the allowlist")
        if identity in identities:
            raise ValueError("duplicate public fault case")
        identities.add(identity)
        case = {k: row[k] for k in ("placement", "mode", "fault")}
        for key in CASE - set(case):
            value = row.get(key, False)
            if type(value) is not bool:
                raise ValueError("public case outcome must be a boolean")
            case[key] = value
        if case["ok"] and not all(case[k] for k in ("automatic_cleanup", "neighbour_survived", "neighbour_completed")):
            raise ValueError("successful fault contradicts its required observations")
        cases.append(case)
    if len(cases) > 12:
        raise ValueError("public case inventory exceeds the reviewed matrix")
    return {"schema_id": "openpine.protected_qualification.public_projection.v1",
            "candidate_sha": candidate_sha, "source_commits": commits,
            "allowlist_sha256": "sha256:" + hashlib.sha256(allowlist.read_bytes()).hexdigest(),
            "cases": sorted(cases, key=lambda r: (r["placement"], r["mode"], r["fault"])),
            "protected_matrix_passed": len(cases) == 12 and all(c["ok"] for c in cases),
            "owner_checks_passed": owner_checks_passed,
            # Ephemeral private primaries do not close archive/product obligations.
            "raw_primaries_durable": False, "full_qualification_accepted": False}


def write_projection(folder: Path, value: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > 1024 * 1024:
        raise ValueError("public projection exceeds its bounded metadata budget")
    with (folder / "projection.json").open("xb") as stream:
        stream.write(payload)
    with (folder / "projection.sha256").open("x") as stream:
        stream.write(hashlib.sha256(payload).hexdigest() + "\n")
    # Only two regular files; leave generous ZIP framing headroom below 64 MiB.
    if any(p.is_symlink() for p in folder.iterdir()) or sum(p.stat().st_size for p in folder.iterdir()) > 67108864 - 65536:
        raise ValueError("complete public upload budget exceeded")
