"""Reconcile independent observations against one exact installed callable surface.

This module does not execute Pine or derive expected values. The existing corpus
owner verifies frozen inputs and compares traces. This owner only joins those
results without silently losing cases, stale evidence, failed runs or versions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from openpine.verification.builtins import builtin_evidence_report
from openpine.verification.conformance import load_corpus
from openpine.verification.identity import digest, read_json, seal, verify

KEY_FIELDS = ("pine_version", "symbol_id", "overload_id", "call_form")
EXECUTION_PATHS = (
    "abi",
    "compiled_historical",
    "compiled_realtime",
    "compiled_rollback",
    "compiled_checkpoint",
)
SOURCE_COMPONENTS = ("pine2ast", "ast2python", "pinelib")
TEMPORARY_TONUMBER = {
    "manual-tonumber-7-v5": "1_000",
    "manual-tonumber-8-v5": "1e2",
    "manual-tonumber-9-v5": " 1 ",
}
TEMPORARY_AUTHORITY_SHA256 = "e21bf08a0f41341edcc47c8c5d70ab4c8ac9ef73150d3969c91d7aaea3010f6b"


def _temporary_tonumber_gaps(
    host_root: Path, corpus: dict, mismatches: list[dict], observations: dict,
    source_pins: dict[str, str], variant: str,
) -> list[str]:
    """Defer only the three documented unknown grammars, never an assigned result."""
    if {row["id"] for row in mismatches} != set(TEMPORARY_TONUMBER) or len(mismatches) != 3:
        return []
    authority_path = _under(host_root, "verification/builtin-string-operations-v1/string-v5-case-authority-independent-review.json")
    if hashlib.sha256(authority_path.read_bytes()).hexdigest() != TEMPORARY_AUTHORITY_SHA256:
        return []
    authority = {row["manual_id"]: row for row in read_json(authority_path)["rows"]}
    cases = {case["id"]: case for case in corpus["cases"]}
    for detail in mismatches:
        case_id = detail["id"]
        case = cases.get(case_id)
        observation = observations.get(case_id, {})
        row_id = case_id.removeprefix("manual-").removesuffix("-v5")
        row = authority.get(row_id, {})
        if not case or case["pine_version"] != 5 or detail.get("status") != "RUNTIME_MISMATCH" or detail.get("authority") != "UNVERIFIED" or detail.get("first_divergence") is None:
            return []
        settings = read_json(_under(host_root, str(Path("verification/builtin-string-operations-v1") / case["settings"]["path"])))
        if not (settings.get("unverified_numeric_disagreement") is True
                and settings.get("initial_v5_inference") is True
                and settings.get("id") == case_id
                and settings.get("row_id") == row_id
                and settings.get("argument") == {"kind": "value", "value": TEMPORARY_TONUMBER[case_id]}
                and settings.get("expected") == row.get("original_expected") == {"kind": "na"}
                and row.get("argument") == TEMPORARY_TONUMBER[case_id]
                and row.get("authority_status") == "UNVERIFIED"
                and row.get("assignment_authority_eligible") is False):
            return []
        claim = observation.get("semantic_authority", {})
        if not (claim.get("classification") == "UNVERIFIED"
                and claim.get("confirmed") is False
                and claim.get("independent_receipt_sha256") == TEMPORARY_AUTHORITY_SHA256
                and observation.get("transcript_mode") == variant
                and all(observation.get("source_identity", {}).get(name) == source_pins[name] for name in SOURCE_COMPONENTS)):
            return []
    return list(TEMPORARY_TONUMBER)


def key_of(row: dict) -> tuple:
    key = tuple(row[field] for field in KEY_FIELDS)
    if (
        type(key[0]) is not int
        or not 1 <= key[0] <= 6
        or any(type(value) is not str or not value for value in key[1:])
    ):
        raise ValueError("invalid versioned callable identity")
    return key


def _surface_rows(surface: dict) -> dict[tuple, dict]:
    verify(surface, "openpine.builtin_surface.v1")
    rows = surface.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("empty callable surface")
    indexed = {}
    for row in rows:
        key = key_of(row)
        if key in indexed or row["contract_hash"] != digest(row["contract"]):
            raise ValueError("duplicate callable identity or invalid contract hash")
        indexed[key] = row
    return indexed


def make_surface_lock(surface: dict) -> dict:
    """Explicit reviewed denominator, not an automatically shrinking baseline."""
    rows = _surface_rows(surface)
    return seal(
        {
            "schema_id": "openpine.callable_denominator.v1",
            "denominator_kind": surface["denominator_kind"],
            "catalogs": surface["catalogs"],
            "rows": [
                {**dict(zip(KEY_FIELDS, key)), "contract_hash": rows[key]["contract_hash"]}
                for key in sorted(rows)
            ],
        }
    )


def compare_surface_lock(surface: dict, locked: dict) -> dict:
    current = _surface_rows(surface)
    body = verify(locked, "openpine.callable_denominator.v1")
    old = {}
    if not isinstance(body.get("rows"), list) or not body["rows"]:
        raise ValueError("empty denominator lock")
    for row in body["rows"]:
        if set(row) != {*KEY_FIELDS, "contract_hash"}:
            raise ValueError("invalid locked callable row")
        key = key_of(row)
        if key in old:
            raise ValueError("duplicate locked callable")
        old[key] = row["contract_hash"]
    added, removed = set(current) - set(old), set(old) - set(current)
    changed = {key for key in set(old) & set(current) if old[key] != current[key]["contract_hash"]}
    catalogs_changed = body["catalogs"] != surface["catalogs"]
    kind_changed = body["denominator_kind"] != surface["denominator_kind"]
    return {
        "ok": not (added or removed or changed or catalogs_changed or kind_changed),
        "locked_count": len(old),
        "current_count": len(current),
        "added": [list(key) for key in sorted(added)],
        "removed": [list(key) for key in sorted(removed)],
        "contract_changed": [list(key) for key in sorted(changed)],
        "catalog_identities_changed": catalogs_changed,
        "denominator_kind_changed": kind_changed,
    }


def _under(root: Path, relative: str) -> Path:
    """Read only declared regular paths; no implicit glob, traversal or symlinks."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid relative evidence path")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("evidence path escapes its root")
    base = root.resolve(strict=True)
    result = base
    for part in path.parts:
        result = result / part
        if result.is_symlink():
            raise ValueError("symlink evidence path is not admitted")
    if not result.resolve().is_relative_to(base):
        raise ValueError("evidence path escapes its root")
    return result


def _groups(plan: dict) -> list[dict]:
    body = verify(plan, "openpine.builtin_evidence_plan.v1")
    if set(body) != {"schema_id", "groups"} or not body["groups"]:
        raise ValueError("empty or malformed evidence plan")
    ids, reports = set(), set()
    for group in body["groups"]:
        if set(group) != {
            "id",
            "corpus",
            "corpus_hash",
            "report_root",
            "variants",
            "paths",
            "assignment_lock",
            "assignment_hash",
        }:
            raise ValueError("malformed evidence group")
        if not isinstance(group["id"], str) or not group["id"] or group["id"] in ids:
            raise ValueError("duplicate or invalid evidence group")
        ids.add(group["id"])
        if group["report_root"] in reports:
            raise ValueError("report directory belongs to more than one group")
        reports.add(group["report_root"])
        if group["paths"] != list(EXECUTION_PATHS):
            raise ValueError("all five execution paths must remain in the plan")
        if group["variants"] not in (["legacy"], ["full", "compact"]):
            raise ValueError("unknown or reduced transcript variants")
    return body["groups"]


def build_evidence_index(
    surface: dict,
    locked: dict,
    plan: dict,
    *,
    host_root: Path,
    evidence_roots: list[Path],
    source_pins: dict[str, str],
) -> dict[str, Any]:
    """Revalidate observations from explicit runs; never trust a report's PASS.

    Roots can hold separate completed runs or disjoint case groups. The join is
    commutative and idempotent for identical data. A failing repeated observation
    blocks that case/variant/path even when another run passed. Missing expected
    paths stay missing. Old target/catalog/source results receive no fresh credit.
    The plan itself is hash-locked separately, so deleting a group is observable.
    """
    rows = _surface_rows(surface)
    denominator = compare_surface_lock(surface, locked)
    groups = _groups(plan)
    if not evidence_roots or any(not path.is_dir() for path in evidence_roots):
        raise ValueError("at least one existing evidence directory is required")
    if any(name not in source_pins for name in SOURCE_COMPONENTS):
        raise ValueError("exact producer/compiler/runtime source pins are required")
    roots = sorted({path.resolve() for path in evidence_roots})
    # key, corpus hash, case id, variant, path -> nonduplicated outcome records.
    outcomes: dict[tuple, dict[str, dict]] = defaultdict(dict)
    group_results = []
    run_inputs = []
    for group in groups:
        corpus_path = _under(host_root, group["corpus"])
        corpus = load_corpus(corpus_path)
        if corpus["content_hash"] != group["corpus_hash"]:
            raise ValueError("declared corpus changed; review the evidence plan explicitly")
        assignment_lock = read_json(_under(host_root, group["assignment_lock"]))
        frozen = verify(assignment_lock, "openpine.builtin_assignment_lock.v1")
        if (
            assignment_lock["content_hash"] != group["assignment_hash"]
            or frozen["corpus_hash"] != group["corpus_hash"]
        ):
            raise ValueError("frozen assignment identity changed")
        frozen_assignments = frozen["assignments"]
        if not isinstance(frozen_assignments, list) or not frozen_assignments:
            raise ValueError("empty frozen assignment set")
        frozen_digests = {digest(row) for row in frozen_assignments}
        if len(frozen_digests) != len(frozen_assignments):
            raise ValueError("duplicate frozen assignment")
        cases = {case["id"]: case for case in corpus["cases"]}
        for item in frozen_assignments:
            if set(item) != {*KEY_FIELDS, "case_id", "contract_hash"}:
                raise ValueError("invalid frozen assignment fields")
            key = key_of(item)
            if (
                item["case_id"] not in cases
                or key not in rows
                or cases[item["case_id"]]["pine_version"] != key[0]
                or item["contract_hash"] != rows[key]["contract_hash"]
            ):
                raise ValueError("frozen assignment no longer matches corpus/surface")
        observation_scopes = {}
        for case in corpus["cases"]:
            settings = read_json(_under(corpus_path.parent, case["settings"]["path"]))
            observation_scopes[case["id"]] = {
                "scope": settings.get("scope", "bounded_example_not_whole_contract"),
                "observed_from_bar": settings.get("observed_from_bar", 0),
            }
        for variant in group["variants"]:
            for path in group["paths"]:
                suffix = (
                    f"{group['report_root']}/{path}"
                    if variant == "legacy"
                    else (f"{group['report_root']}/{variant}/{path}")
                )
                found = False
                any_valid = False
                failures = []
                unassigned_details = {}
                deferred_cases = set()
                for root in roots:
                    folder = _under(root, suffix)
                    files = {
                        name: folder / (name + ".json")
                        for name in ("report", "observations", "assignments")
                    }
                    if not any(file.exists() for file in files.values()):
                        continue
                    found = True
                    missing = [name for name, file in files.items() if not file.is_file()]
                    if missing:
                        failures.append("MISSING_" + "_".join(missing).upper())
                        continue
                    values = {
                        name: read_json(_under(root, suffix + f"/{name}.json")) for name in files
                    }
                    report = values["report"]
                    verify(report, "openpine.builtin_evidence.v1")
                    token = digest(values)
                    run_inputs.append(
                        {
                            "group": group["id"],
                            "variant": variant,
                            "path": path,
                            "input_hash": token,
                        }
                    )
                    if report["surface_hash"] != surface["content_hash"]:
                        failures.append("STALE_SURFACE")
                        continue
                    rebuilt = builtin_evidence_report(
                        surface,
                        corpus_path,
                        values["observations"],
                        corpus_hash=group["corpus_hash"],
                        assignments=values["assignments"],
                    )
                    if rebuilt != report:
                        failures.append("REPORT_REPLAY_MISMATCH")
                        # An alleged PASS does not overrule actually replayed FAIL.
                    assignments = values["assignments"]
                    if not assignments or any(
                        assignment["path"] != path for assignment in assignments
                    ):
                        failures.append("EMPTY_OR_WRONG_PATH_ASSIGNMENTS")
                        continue
                    actual_digests = {
                        digest({key: value for key, value in assignment.items() if key != "path"})
                        for assignment in assignments
                    }
                    assignment_ok = actual_digests == frozen_digests
                    if not assignment_ok:
                        failures.append("ASSIGNMENT_SET_MISMATCH")
                    assigned_ids = {item["case_id"] for item in frozen_assignments}
                    unassigned_mismatches = []
                    unassigned_nonpass = []
                    for result in rebuilt["trace_comparison"]["results"]:
                        if result["id"] not in assigned_ids:
                            observed = values["observations"].get(result["id"], {})
                            authority = observed.get("semantic_authority", {})
                            detail = {
                                **result,
                                "authority": authority.get("classification", "not_declared"),
                            }
                            unassigned_details[digest(detail)] = detail
                            if result["status"] != "PASS":
                                unassigned_nonpass.append(detail)
                            if result["status"] != "PASS" and detail["authority"] == "UNVERIFIED":
                                unassigned_mismatches.append(detail)
                    trace_ok = rebuilt["trace_comparison"]["ok"]
                    temporary = (
                        _temporary_tonumber_gaps(
                            host_root, corpus, unassigned_mismatches, values["observations"],
                            source_pins, variant,
                        )
                        if group["id"] == "builtin-string-operations" and assignment_ok
                        and len(unassigned_nonpass) == len(unassigned_mismatches)
                        and rebuilt["execution_evidence"]["all_assigned_passed"]
                        and rebuilt == report else []
                    )
                    if unassigned_mismatches and not temporary:
                        failures.append("UNVERIFIED_EXPECTATION_MISMATCH")
                    if temporary:
                        deferred_cases.update(temporary)
                    group_failed = (
                        not assignment_ok
                        or not rebuilt["execution_evidence"]["all_assigned_passed"]
                        or (not trace_ok and not temporary)
                    )
                    if group_failed:
                        failures.append("OBSERVATION_FAILED")
                    any_valid = any_valid or (not group_failed and rebuilt == report)
                    root_pins_path = _under(root, "source-pins.json")
                    root_pins = read_json(root_pins_path) if root_pins_path.is_file() else {}
                    for row in rebuilt["rows"]:
                        key = key_of(row)
                        for evidence in row["evidence"]:
                            case_id = evidence["case_id"]
                            observed = values["observations"].get(case_id, {})
                            identity = observed.get("source_identity", root_pins)
                            status = evidence["result"]["status"]
                            if not isinstance(identity, dict) or any(
                                identity.get(name) != source_pins[name]
                                for name in SOURCE_COMPONENTS
                            ):
                                status = "STALE_SOURCE"
                                failures.append(status)
                            if variant != "legacy" and observed.get("transcript_mode") != variant:
                                status = "TRANSCRIPT_MODE_MISMATCH"
                                failures.append(status)
                            if rebuilt != report and status == "PASS":
                                status = "REPORT_REPLAY_MISMATCH"
                            if not assignment_ok:
                                status = "ASSIGNMENT_SET_MISMATCH"
                            unit = (key, group["corpus_hash"], case_id, variant, path)
                            normalized = {
                                "status": status,
                                "oracle": evidence["oracle"]["kind"],
                                "observation_scope": observation_scopes[case_id],
                                "trace_hash": digest(
                                    {field: observed.get(field) for field in ("compile", "events")}
                                ),
                            }
                            outcomes[unit][digest(normalized)] = normalized
                group_results.append(
                    {
                        "group": group["id"],
                        "variant": variant,
                        "path": path,
                        "corpus_cases": len(cases),
                        "assigned_cases": len({item["case_id"] for item in frozen_assignments}),
                        "unassigned_cases": sorted(
                            set(cases) - {item["case_id"] for item in frozen_assignments}
                        ),
                        "unassigned_outcomes": sorted(unassigned_details.values(), key=digest),
                        "deferred_cases": sorted(deferred_cases),
                        "status": "NOT_RUN"
                        if not found
                        else "PASS"
                        if any_valid and not failures and not deferred_cases
                        else "TEMPORARY_UNVERIFIED"
                        if any_valid and not failures and deferred_cases
                        else "FAILED",
                        "reasons": sorted(set(failures)),
                    }
                )
    evidence_by_key = defaultdict(list)
    for (key, corpus_hash, case_id, variant, path), values in sorted(outcomes.items()):
        results = list(values.values())
        statuses = sorted({value["status"] for value in results})
        good = statuses == ["PASS"]
        # Two accepted (tolerated) float traces may differ: keep both hashes, but
        # do not treat them as two cases. Value equivalence is corpus-owned.
        evidence_by_key[key].append(
            {
                "corpus_hash": corpus_hash,
                "case_id": case_id,
                "variant": variant,
                "path": path,
                "status": "PASS" if good else "CONFLICT" if "PASS" in statuses else "FAILED",
                "observed_statuses": statuses,
                "oracles": sorted({value["oracle"] for value in results}),
                "trace_hashes": sorted({value["trace_hash"] for value in results}),
                "observation_scope": results[0]["observation_scope"],
            }
        )
    output_rows = []
    for key, row in sorted(rows.items()):
        evidence = evidence_by_key[key]
        passed_paths = sorted({item["path"] for item in evidence if item["status"] == "PASS"})
        problems = [item for item in evidence if item["status"] != "PASS"]
        covered = set(passed_paths) >= set(EXECUTION_PATHS) and not problems
        output_rows.append(
            {
                **{
                    field: row[field]
                    for field in (
                        *KEY_FIELDS,
                        "contract_hash",
                        "spellings",
                        "status",
                        "reasons",
                        "qualifier_contract",
                    )
                },
                "evidence_status": "FAILED"
                if problems
                else "EXAMPLES_ALL_PATHS"
                if covered
                else "PARTIAL_EXAMPLES"
                if evidence
                else "NO_EXAMPLES",
                "passing_paths": passed_paths,
                "unique_cases": len({(item["corpus_hash"], item["case_id"]) for item in evidence}),
                "evidence": evidence,
                "contract_fully_verified": False,
            }
        )
    counts = dict(Counter(row["evidence_status"] for row in output_rows))
    direct = [row for row in output_rows if row["status"] == "RUNTIME_DIRECT"]
    return seal(
        {
            "schema_id": "openpine.builtin_evidence_index.v1",
            "surface_hash": surface["content_hash"],
            "lock_hash": locked["content_hash"],
            "plan_hash": plan["content_hash"],
            "source_pins": {name: source_pins[name] for name in SOURCE_COMPONENTS},
            "denominator_review": denominator,
            "denominator": len(rows),
            "rows": output_rows,
            "counts": counts,
            "groups": group_results,
            "input_sets": sorted({digest(item): item for item in run_inputs}.values(), key=digest),
            "required_group_paths": len(group_results),
            "passed_group_paths": sum(row["status"] == "PASS" for row in group_results),
            "deferred_group_paths": sum(row["status"] == "TEMPORARY_UNVERIFIED" for row in group_results),
            "temporary_unverified_cases": sorted({case for group in group_results for case in group["deferred_cases"]}),
            "provisional_gate_ok": denominator["ok"] and bool(group_results)
            and all(row["status"] in {"PASS", "TEMPORARY_UNVERIFIED"} for row in group_results),
            "all_declared_runs_passed": bool(group_results)
            and all(row["status"] == "PASS" for row in group_results),
            "direct_signatures": len(direct),
            "direct_with_examples_all_paths": sum(
                row["evidence_status"] == "EXAMPLES_ALL_PATHS" for row in direct
            ),
            "scope": "bounded_independent_examples_not_whole_callable_contract_or_official_catalog_completeness",
            "ok": denominator["ok"]
            and bool(group_results)
            and all(row["status"] == "PASS" for row in group_results),
            "full_builtin_expected_accepted": False,
            "full_stage2_accepted": False,
            "tradingview_verified": False,
        }
    )
