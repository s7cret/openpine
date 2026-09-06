"""Frozen-denominator comparison with independent expected-data provenance.

No Pine is interpreted here. Existing compiler/worker/broker runners produce
observations. Manual regression fixtures and external TV evidence remain separate.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from openpine.verification.identity import read_json, seal, verify

CORPUS = "openpine.conformance_corpus.v1"
CASE_FIELDS = {
    "id",
    "pine_version",
    "layer",
    "weight",
    "critical",
    "source",
    "data",
    "settings",
    "expected",
    "oracle",
    "tolerance",
}
LAYERS = {"compile", "runtime", "broker", "visual"}


def _file(root: Path, descriptor: dict) -> bytes:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise ValueError("invalid corpus file descriptor")
    name = descriptor["path"]
    if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("corpus path escapes its root")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("corpus file is missing or escapes its root")
    with path.open("rb") as stream:
        value = stream.read(32 * 1024 * 1024 + 1)
    if len(value) > 32 * 1024 * 1024:
        raise ValueError("corpus file exceeds size limit")
    if hashlib.sha256(value).hexdigest() != descriptor["sha256"]:
        raise ValueError("corpus file checksum mismatch")
    return value


def load_corpus(path: Path) -> dict:
    manifest = read_json(path)
    body = verify(manifest, CORPUS)
    if set(body) != {"schema_id", "revision", "profile", "cases"}:
        raise ValueError("corpus fields mismatch")
    if body["profile"] not in {"engineering", "tradingview"}:
        raise ValueError("unknown conformance profile")
    cases = body["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("empty corpus cannot pass")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_FIELDS:
            raise ValueError("case fields mismatch")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise ValueError("duplicate or empty case ID")
        seen.add(case["id"])
        if type(case["pine_version"]) is not int or not 1 <= case["pine_version"] <= 6:
            raise ValueError("case needs exact Pine version")
        if (
            case["layer"] not in LAYERS
            or type(case["weight"]) is not int
            or case["weight"] <= 0
            or type(case["critical"]) is not bool
        ):
            raise ValueError("invalid case classification")
        oracle = case["oracle"]
        if (
            not isinstance(oracle, dict)
            or set(oracle) != {"kind", "provenance"}
            or oracle["kind"] not in {"manual_fixture", "tradingview_export", "missing"}
        ):
            raise ValueError("invalid oracle descriptor")
        if oracle["kind"] != "missing" and (
            not isinstance(oracle["provenance"], str) or not oracle["provenance"].strip()
        ):
            raise ValueError("expected data requires provenance")
        tolerance = case["tolerance"]
        if (
            not isinstance(tolerance, dict)
            or set(tolerance) != {"absolute", "relative"}
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in tolerance.values()
            )
        ):
            raise ValueError("tolerance must be explicit finite nonnegative numbers")
        for key in ("source", "data", "settings"):
            _file(path.parent, case[key])
        if case["expected"] is not None:
            _file(path.parent, case["expected"])
            expected = read_json(path.parent / case["expected"]["path"])
            if (
                not isinstance(expected, dict)
                or set(expected) != {"compile", "events"}
                or type(expected["compile"]) is not bool
                or not isinstance(expected["events"], list)
            ):
                raise ValueError("expected trace shape is invalid")
    return manifest


def first_difference(expected: Any, actual: Any, tolerance: dict, path: str = "$") -> dict | None:
    if type(expected) in (int, float) and type(actual) in (int, float):
        if not math.isfinite(expected) or not math.isfinite(actual):
            equal = False
        else:
            equal = math.isclose(
                expected, actual, rel_tol=tolerance["relative"], abs_tol=tolerance["absolute"]
            )
        return None if equal else {"path": path, "expected": expected, "actual": actual}
    if type(expected) is not type(actual):
        return {"path": path, "expected": expected, "actual": actual}
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                return {
                    "path": path + "." + key,
                    "expected_present": key in expected,
                    "actual_present": key in actual,
                }
            result = first_difference(expected[key], actual[key], tolerance, path + "." + key)
            if result is not None:
                return result
        return None
    if isinstance(expected, list):
        for index, (left, right) in enumerate(zip(expected, actual)):
            result = first_difference(left, right, tolerance, f"{path}[{index}]")
            if result is not None:
                if isinstance(left, dict):
                    result["location"] = {
                        key: left[key] for key in ("bar", "phase", "source_span") if key in left
                    }
                return result
        if len(expected) != len(actual):
            return {"path": path + ".length", "expected": len(expected), "actual": len(actual)}
        return None
    return None if expected == actual else {"path": path, "expected": expected, "actual": actual}


def compare_corpus(path: Path, observations: dict, *, expected_corpus_hash: str) -> dict:
    manifest = load_corpus(path)
    if manifest["content_hash"] != expected_corpus_hash:
        raise ValueError("frozen corpus changed: explicit re-baselining is required")
    if not isinstance(observations, dict):
        raise ValueError("observations must map exact case IDs")
    cases = manifest["cases"]
    if set(observations) - {case["id"] for case in cases}:
        raise ValueError("observation contains unknown case IDs")
    results, earned, denominator, critical_ok = [], 0, 0, True
    for case in cases:
        denominator += case["weight"]
        result = {
            "id": case["id"],
            "pine_version": case["pine_version"],
            "status": "PASS",
            "first_divergence": None,
        }
        observed = observations.get(case["id"])
        if case["expected"] is None or case["oracle"]["kind"] == "missing":
            result["status"] = "ORACLE_MISSING"
        elif (
            manifest["profile"] == "tradingview" and case["oracle"]["kind"] != "tradingview_export"
        ):
            result["status"] = "ORACLE_NOT_EXTERNAL"
        elif observed is None:
            result["status"] = "NOT_RUN"
        elif not isinstance(observed, dict) or observed.get("status") != "completed":
            result["status"] = "EXECUTION_NOT_COMPLETED"
        else:
            for key, code in (
                ("source", "SOURCE_MISMATCH"),
                ("data", "DATA_MISMATCH"),
                ("settings", "CONFIG_MISMATCH"),
            ):
                if observed.get(key + "_sha256") != case[key]["sha256"]:
                    result["status"] = code
                    break
            if result["status"] == "PASS":
                expected = read_json(path.parent / case["expected"]["path"])
                difference = first_difference(
                    expected,
                    {"compile": observed.get("compile"), "events": observed.get("events")},
                    case["tolerance"],
                )
                if difference is not None:
                    result["status"] = (
                        "COMPILE_MISMATCH"
                        if expected["compile"] != observed.get("compile")
                        else case["layer"].upper() + "_MISMATCH"
                    )
                    result["first_divergence"] = difference
        if result["status"] == "PASS":
            earned += case["weight"]
        elif case["critical"]:
            critical_ok = False
        results.append(result)
    return seal(
        {
            "schema_id": "openpine.conformance_report.v1",
            "corpus_hash": manifest["content_hash"],
            "profile": manifest["profile"],
            "denominator": denominator,
            "earned": earned,
            "critical_gate": critical_ok,
            "ok": earned == denominator and critical_ok,
            "tradingview_verified": manifest["profile"] == "tradingview"
            and earned == denominator
            and critical_ok,
            "results": results,
        }
    )
