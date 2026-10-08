"""Closed public projection; raw qualification primaries are never copied."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

SHA = re.compile(r"[0-9a-f]{40}\Z")
COMPONENTS = {"openpine", "openpine-contracts", "pine2ast", "ast2python",
              "pinelib", "backtest_engine", "marketdata-provider", "optimizer"}
TOP = {"schema_id", "candidate_sha", "source_commits", "allowlist_sha256", "cases",
       "protected_matrix_passed", "owner_checks_passed", "raw_primaries_durable", "full_qualification_accepted",
       "stage", "error", "ok"}
CASE_BOOLEANS = {"ok", "automatic_cleanup", "neighbour_survived", "neighbour_completed"}
CASE_STAGES = {"setup", "fault", "automatic-observation", "family-receipt",
               "neighbour-release", "neighbour-wait", "native-result", "complete"}
CASE_ERRORS = {"none", "failed", "timeout", "interrupted", "nonzero-exit",
               "missing-result", "invalid-result", "disposal-failed"}
COMPLETION_STATES = {"not-reached", "unverified", "observed-false", "observed-true"}
CASE_DIAGNOSTICS = {"case_stage": CASE_STAGES, "case_error": CASE_ERRORS,
                    "neighbour_completion_state": COMPLETION_STATES}
CASE = {"placement", "mode", "fault"} | CASE_BOOLEANS | CASE_DIAGNOSTICS.keys()
STAGES = {"identity", "prepare", "candidate-wheels", "candidate-manifest", "test-namespace",
          "restore-A", "matrix-A", "int04-A", "int05-A", "protected-A",
          "restore-B", "matrix-B", "int04-B", "int05-B", "protected-B",
          "outcome", "complete", "publication", "workflow-driver"}
ERRORS = {"none", "incomplete", "identity-mismatch", "unsupported-runtime", "disk-reserve",
          "missing-input", "invalid-input", "command-failed", "missing-output", "timeout",
          "io-failed", "interrupted", "unexpected-error", "projection-invalid"}


def project(candidate_sha: str | None, commits: dict[str, str], rows: list[dict[str, Any]],
            allowlist: Path, *, owner_checks_passed: bool = False,
            stage: str = "complete", error: str = "none") -> dict[str, Any]:
    policy = json.loads(allowlist.read_bytes())
    if (policy["schema_id"] != "openpine.protected_qualification.public_allowlist.v2"
            or set(policy["top_fields"]) != TOP or set(policy["case_fields"]) != CASE
            or policy["files"] != ["projection.json", "projection.sha256"]
            or policy["max_complete_upload_bytes"] != 67108864
            or policy["retention_days"] != 1
            or set(policy["permitted_placements"]) != {"A", "B"}
            or set(policy["permitted_modes"]) != {"interactive", "bulk_backtest"}
            or set(policy["permitted_faults"]) != {"timeout", "sigint", "controller-sigkill"}
            or set(policy["permitted_stages"]) != STAGES
            or set(policy["permitted_errors"]) != ERRORS
            or set(policy["permitted_case_stages"]) != CASE_STAGES
            or set(policy["permitted_case_errors"]) != CASE_ERRORS
            or set(policy["permitted_neighbour_completion_states"]) != COMPLETION_STATES
            or policy["unknown_identity_on_failure"] != {"candidate_sha": None, "source_commits": {}}):
        raise ValueError("public projection policy differs from its closed contract")
    if not isinstance(stage, str) or not isinstance(error, str) or stage not in STAGES or error not in ERRORS:
        raise ValueError("public diagnostic is outside its closed vocabulary")
    if not isinstance(commits, dict) or not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("public identities and cases must be objects")
    if (candidate_sha is not None and (not isinstance(candidate_sha, str) or not SHA.fullmatch(candidate_sha))
            or candidate_sha is None and error == "none"
            or commits and (set(commits) != COMPONENTS or commits["openpine"] != candidate_sha
                or any(not isinstance(value, str) or not SHA.fullmatch(value) for value in commits.values()))
            or not commits and error == "none"):
        raise ValueError("public candidate identity is invalid")
    if type(owner_checks_passed) is not bool:
        raise ValueError("public owner outcome must be a boolean")
    cases, identities = [], set()
    for row in rows:
        identity = tuple(row.get(k) for k in ("placement", "mode", "fault"))
        for index, key in enumerate(("placement", "mode", "fault")):
            if not isinstance(identity[index], str) or identity[index] not in policy["permitted_" + key + "s"]:
                raise ValueError("public case identity is outside the allowlist")
        if identity in identities:
            raise ValueError("duplicate public fault case")
        identities.add(identity)
        case = {k: row[k] for k in ("placement", "mode", "fault")}
        for key in CASE_BOOLEANS:
            value = row.get(key, False)
            if type(value) is not bool:
                raise ValueError("public case outcome must be a boolean")
            case[key] = value
        for key, permitted in CASE_DIAGNOSTICS.items():
            value = row.get(key)
            if not isinstance(value, str) or value not in permitted:
                raise ValueError("public case diagnostic is outside its closed vocabulary")
            case[key] = value
        completion = case["neighbour_completion_state"]
        if case["neighbour_completed"] != (completion == "observed-true"):
            raise ValueError("neighbour completion contradicts its explicit observation")
        if ((completion == "not-reached" and case["case_stage"] not in {
                "setup", "fault", "automatic-observation", "family-receipt", "neighbour-release"})
                or (completion in {"unverified", "observed-false"}
                    and case["case_stage"] not in {"neighbour-wait", "native-result"})
                or (completion == "observed-true"
                    and case["case_stage"] not in {"native-result", "complete"})
                or (case["case_error"] == "nonzero-exit"
                    and (case["case_stage"], completion) != ("neighbour-wait", "observed-false"))
                or (case["case_error"] in {"missing-result", "invalid-result"}
                    and (case["case_stage"], completion) != ("native-result", "observed-false"))):
            raise ValueError("public case diagnostic contradicts its reached stage")
        if case["ok"] and not all(case[k] for k in ("automatic_cleanup", "neighbour_survived", "neighbour_completed")):
            raise ValueError("successful fault contradicts its required observations")
        if case["ok"] and (case["case_stage"], case["case_error"], completion) != (
                "complete", "none", "observed-true"):
            raise ValueError("successful fault contradicts its case diagnostics")
        if case["case_stage"] == "complete" and (case["case_error"] == "none") != case["ok"]:
            raise ValueError("completed fault contradicts its closed error outcome")
        cases.append(case)
    if len(cases) > 12:
        raise ValueError("public case inventory exceeds the reviewed matrix")
    matrix_passed = len(cases) == 12 and all(c["ok"] for c in cases)
    return {"schema_id": "openpine.protected_qualification.public_projection.v2",
            "candidate_sha": candidate_sha, "source_commits": commits,
            "allowlist_sha256": "sha256:" + hashlib.sha256(allowlist.read_bytes()).hexdigest(),
            "cases": sorted(cases, key=lambda r: (r["placement"], r["mode"], r["fault"])),
            "protected_matrix_passed": matrix_passed,
            "owner_checks_passed": owner_checks_passed,
            "stage": stage, "error": error,
            "ok": stage == "complete" and error == "none" and matrix_passed and owner_checks_passed,
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


def replace_projection(folder: Path, value: dict[str, Any]) -> None:
    """Refresh only this attempt's two metadata files; primaries stay immutable."""
    if not folder.exists():
        write_projection(folder, value)
        return
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("unsafe public projection directory")
    if any(p.name not in {"projection.json", "projection.sha256"} or p.is_symlink()
           or not p.is_file() for p in folder.iterdir()):
        raise ValueError("foreign public projection content")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > 1024 * 1024:
        raise ValueError("public projection exceeds its bounded metadata budget")
    for name, raw in (("projection.json", payload),
                      ("projection.sha256", (hashlib.sha256(payload).hexdigest() + "\n").encode())):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".projection-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, folder / name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def validate_projection(folder: Path, allowlist: Path) -> dict[str, Any]:
    if folder.is_symlink():
        raise ValueError("unsafe projection directory")
    names = set(p.name for p in folder.iterdir())
    if names < {"projection.json", "projection.sha256"}:
        raise FileNotFoundError("missing projection output")
    if names != {"projection.json", "projection.sha256"}:
        raise ValueError("missing or foreign projection outputs")
    files = [folder / "projection.json", folder / "projection.sha256"]
    if any(p.is_symlink() or not p.is_file() for p in files) or files[0].stat().st_size > 1024 * 1024 or files[1].stat().st_size > 65:
        raise ValueError("unsafe projection output")
    raw = files[0].read_bytes()
    if files[1].read_text() != hashlib.sha256(raw).hexdigest() + "\n":
        raise ValueError("projection digest mismatch")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != TOP or not isinstance(value["cases"], list)
            or any(not isinstance(row, dict) or set(row) != CASE for row in value["cases"])):
        raise ValueError("projection field set differs from its closed contract")
    expected = project(value["candidate_sha"], value["source_commits"], value["cases"], allowlist,
                       owner_checks_passed=value["owner_checks_passed"], stage=value["stage"], error=value["error"])
    if value != expected or raw != json.dumps(expected, sort_keys=True, separators=(",", ":")).encode():
        raise ValueError("projection outcome differs from its validated observations")
    return value


def declared_commits(host: Path, candidate_sha: str | None) -> dict[str, str]:
    """Read only immutable declared pins; absence never becomes a guessed identity."""
    template = host / "candidates/stack-candidate-5.0.0-rc.6.template.json"
    if template.is_symlink() or template.stat().st_size > 1024 * 1024:
        raise ValueError("unsafe candidate template")
    components = json.loads(template.read_bytes())["components"]
    if not isinstance(components, dict) or set(components) != COMPONENTS:
        raise ValueError("invalid component inventory")
    commits: dict[str, str] = {}
    for name, row in components.items():
        if not isinstance(row, dict):
            raise ValueError("invalid declared component")
        sha = candidate_sha if name == "openpine" else row["sha"]
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError("invalid declared identity")
        commits[name] = sha
    return commits


def ensure_projection(host: Path, folder: Path, candidate_sha: str, driver_outcome: str) -> dict[str, Any]:
    """Independent pre-upload guard, including a driver that produced no output."""
    if driver_outcome not in {"success", "failure", "skipped", "cancelled"}:
        raise ValueError("invalid workflow outcome")
    allowlist = host / "verification/protected-qualification-public-allowlist.json"
    error = "projection-invalid"
    try:
        value = validate_projection(folder, allowlist)
        if value["candidate_sha"] != candidate_sha:
            raise ValueError("projection candidate differs from workflow identity")
        if driver_outcome == "success" or not value["ok"]:
            return value
        # A failed/cancelled driver cannot leave an accepted success checkpoint.
        # Keep its already validated case observations while failing the run.
        value = project(value["candidate_sha"], value["source_commits"], value["cases"], allowlist,
            owner_checks_passed=value["owner_checks_passed"], stage="workflow-driver", error="incomplete")
        replace_projection(folder, value)
        return validate_projection(folder, allowlist)
    except FileNotFoundError:
        error = "missing-output"
    except (OSError, ValueError, KeyError, TypeError):
        error = "projection-invalid"
    sha = candidate_sha if SHA.fullmatch(candidate_sha) else None
    try:
        commits = declared_commits(host, sha)
    except (OSError, ValueError, KeyError, TypeError):
        commits = {}
    value = project(sha, commits, [], allowlist, stage="workflow-driver", error=error)
    replace_projection(folder, value)
    return validate_projection(folder, allowlist)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--driver-outcome", choices=("success", "failure", "skipped", "cancelled"), required=True)
    args = parser.parse_args()
    try:
        value = ensure_projection(args.host, args.folder, args.candidate_sha, args.driver_outcome)
    except (Exception, KeyboardInterrupt, SystemExit):  # noqa: BLE001 -- final public boundary exports only closed constants, never exception text
        print(json.dumps({"stage": "publication", "error": "projection-invalid", "ok": False}))
        return 1
    print(json.dumps({key: value[key] for key in ("stage", "error", "ok")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
