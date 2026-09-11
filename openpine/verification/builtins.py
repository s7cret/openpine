"""Versioned builtin evidence inventory; never an implementation of Pine semantics.

The frontend owns callable shapes and the compiler owns exact target projection.
This report retains missing bindings and missing independent observations in its
denominator. A callable or a passing example never implies complete conformance.
"""

from __future__ import annotations

from collections import Counter
from importlib import import_module
from pathlib import Path

from ast2python.lowering import (
    audit_pinelib_call_binding,
    audit_pinelib_qualifier_binding,
    audit_pinelib_value_binding,
    load_pinelib_target_manifest,
)
from ast2python.lowering.target import TargetCallBinding, TargetValueBinding
from pine2ast.catalog import CatalogRepository
from pine2ast.semantic.signatures import SignatureResolver
from pine2ast.versioning import PineVersionResolver

from openpine.verification.conformance import compare_corpus, load_corpus
from openpine.verification.identity import digest, seal


def callable_exists(path: object) -> bool:
    if not isinstance(path, str) or not path.startswith("pinelib."):
        return False
    module, separator, name = path.rpartition(".")
    if not separator:
        return False
    try:
        return callable(getattr(import_module(module), name, None))
    except ImportError:
        return False


def binding_reasons(binding, version: int, *, source_parameters=None) -> list[str]:
    if binding is None:
        return ["COMPILER_BINDING_MISSING"]
    reasons = []
    if version not in binding.supported_pine_versions:
        reasons.append("COMPILER_VERSION_UNAVAILABLE")
    if any(item.get("binding") == "UNBOUND_FAIL_CLOSED" for item in binding.parameter_bindings):
        reasons.append("ABI_PARAMETER_UNBOUND")
    if binding.disposition == "TARGET_DIRECT" and not callable_exists(
        f"{binding.python_module}.{binding.python_name}"
    ):
        reasons.append("RUNTIME_CALLABLE_MISSING")
    if isinstance(binding, TargetCallBinding):
        reasons.extend(audit_pinelib_call_binding(binding, source_parameters, pine_version=version))
    elif isinstance(binding, TargetValueBinding):
        reasons.extend(audit_pinelib_value_binding(binding, pine_version=version))
    else:
        reasons.append("COMPILER_BINDING_TYPE_UNAVAILABLE")
    return sorted(set(reasons))



def qualifier_binding_evidence(binding, version: int, source_parameters) -> dict:
    """Qualifier-domain evidence, not whole-type compatibility or a runtime oracle.

    Structural binding status is intentionally unchanged. A callable can handle
    a constant while failing inclusion for the producer's complete series domain.
    Keeping this separate avoids turning a passing example into full acceptance.
    """
    if not isinstance(binding, TargetCallBinding):
        return {"status": "UNVERIFIED", "reasons": ["COMPILER_BINDING_MISSING"]}
    reasons = list(audit_pinelib_qualifier_binding(
        binding, source_parameters, pine_version=version,
    ))
    if not reasons:
        status = "COMPATIBLE"
    elif all(code.endswith("UNVERIFIED") for code in reasons):
        status = "UNVERIFIED"
    else:
        status = "INCOMPATIBLE"
    return {"status": status, "reasons": reasons}

def build_builtin_surface(*, target=None) -> dict:
    """Enumerate every installed producer signature, without symbol-only fallbacks."""
    target = target or load_pinelib_target_manifest()
    if target.release_acceptance != "EXACT_PINELIB_TARGET_MANIFEST_V2":
        raise ValueError("builtin evidence requires the production target")
    repo = CatalogRepository.default()
    rows = {}
    for version in range(1, 7):
        context = (
            PineVersionResolver(repo.identity_tuple).resolve(f"//@version={version}\n").context
        )
        resolver = SignatureResolver(version_context=context)
        catalog = repo.view(version)
        for category in ("functions", "methods"):
            for spelling, entry in catalog.get(category, {}).items():
                form = (
                    "METHOD"
                    if category == "methods"
                    else ("NAMESPACE_FUNCTION" if "." in spelling else "FUNCTION")
                )
                for candidate in resolver.candidate_entries(entry):
                    frontend_available = resolver.candidate_is_active(candidate)
                    symbol = entry["symbol_id"]
                    overload = candidate["__overload_id"]
                    key = (version, symbol, overload, form)
                    contract = {
                        "parameters": candidate.get("parameters", []),
                        "returns": candidate.get("returns"),
                        "return_qualifier": candidate.get("return_qualifier"),
                        "receiver_type": candidate.get("receiver_type"),
                    }
                    binding = target.call_bindings.get((symbol, overload, form))
                    reasons = binding_reasons(
                        binding, version, source_parameters=contract["parameters"]
                    )
                    if not frontend_available:
                        reasons = sorted(set(reasons) | {"FRONTEND_VERSION_UNAVAILABLE"})
                    status = (
                        (
                            "UNVERIFIED"
                            if all(r.endswith("_UNVERIFIED") for r in reasons)
                            else "UNAVAILABLE"
                        )
                        if reasons
                        else (
                            "HOST_DELEGATED"
                            if binding.disposition == "TARGET_DELEGATED"
                            else "RUNTIME_DIRECT"
                        )
                    )
                    if key in rows:
                        if rows[key]["contract"] != contract:
                            raise ValueError("conflicting producer builtin identity")
                        if spelling not in rows[key]["spellings"]:
                            rows[key]["spellings"].append(spelling)
                        # Disjoint version intervals may describe the same
                        # overload shape. Admission of any producer candidate
                        # admits this exact identity; inactive alternatives stay
                        # in the denominator without hiding the active one.
                        if frontend_available and not rows[key]["frontend_available"]:
                            rows[key].update(
                                frontend_available=True, status=status, reasons=reasons
                            )
                        continue
                    rows[key] = {
                        "pine_version": version,
                        "symbol_id": symbol,
                        "overload_id": overload,
                        "call_form": form,
                        "spellings": [spelling],
                        "contract": contract,
                        "contract_hash": digest(contract),
                        "frontend_available": frontend_available,
                        "status": status,
                        "reasons": reasons,
                        "target_binding": binding.to_dict() if binding else None,
                        "qualifier_contract": qualifier_binding_evidence(
                            binding, version, contract["parameters"],
                        ),
                        "oracle": "missing",
                    }
    values = [rows[key] for key in sorted(rows)]
    return seal(
        {
            "schema_id": "openpine.builtin_surface.v1",
            "catalogs": {str(v): repo.identity(v).catalog_hash for v in range(1, 7)},
            "target_manifest_hash": target.content_hash,
            "denominator_kind": "all_installed_frontend_callable_signatures",
            "rows": values,
            "counts": dict(Counter(row["status"] for row in values)),
            "qualifier_counts": dict(Counter(row["qualifier_contract"]["status"] for row in values)),
            "qualifier_evidence_scope": "domain_inclusion_not_full_types_defaults_or_numerical_semantics",
            "full_catalog_verified": False,
            "tradingview_verified": False,
        }
    )


def builtin_evidence_report(
    surface: dict,
    corpus_path: Path,
    observations: dict,
    *,
    corpus_hash: str,
    assignments: list[dict],
) -> dict:
    """Join immutable independent cases to exact overloads and execution paths.

    Assignments are reviewed evidence metadata. Expected values and their hashes
    come exclusively from the existing frozen-corpus owner. Unassigned rows and
    unexecuted cases remain visible; a coverage score is not an acceptance claim.
    """
    from openpine.verification.identity import verify

    verify(surface, "openpine.builtin_surface.v1")
    corpus = load_corpus(corpus_path)
    cases = {case["id"]: case for case in corpus["cases"]}
    report = compare_corpus(corpus_path, observations, expected_corpus_hash=corpus_hash)
    results = {row["id"]: row for row in report["results"]}
    by_key = {
        (row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"]): row
        for row in surface["rows"]
    }
    attached = {key: [] for key in by_key}
    seen = set()
    for assignment in assignments:
        if set(assignment) != {
            "case_id",
            "pine_version",
            "symbol_id",
            "overload_id",
            "call_form",
            "path",
            "contract_hash",
        }:
            raise ValueError("invalid builtin evidence assignment")
        case_id = assignment["case_id"]
        key = tuple(
            assignment[k] for k in ("pine_version", "symbol_id", "overload_id", "call_form")
        )
        if case_id not in cases or key not in by_key:
            raise ValueError("evidence refers to an unknown case or overload")
        if cases[case_id]["pine_version"] != key[0]:
            raise ValueError("evidence language version mismatch")
        if assignment["contract_hash"] != by_key[key]["contract_hash"]:
            raise ValueError("evidence callable contract changed")
        if assignment["path"] not in {
            "abi",
            "compiled_historical",
            "compiled_realtime",
            "compiled_rollback",
            "compiled_checkpoint",
            "protected_worker",
        }:
            raise ValueError("unknown builtin execution path")
        identity = (key, case_id, assignment["path"])
        if identity in seen:
            raise ValueError("duplicate builtin evidence assignment")
        seen.add(identity)
        observed = observations.get(case_id)
        result = results[case_id]
        if result["status"] == "PASS" and (
            observed.get("execution_path") != assignment["path"]
            or list(key) not in observed.get("executed_bindings", [])
            or observed.get("target_manifest_hash") != surface["target_manifest_hash"]
            or observed.get("catalog_hash") != surface["catalogs"][str(key[0])]
        ):
            result = {**result, "status": "EXECUTION_PATH_OR_BINDING_MISMATCH"}
        attached[key].append(
            {
                "case_id": case_id,
                "path": assignment["path"],
                "oracle": cases[case_id]["oracle"],
                "result": result,
            }
        )
    rows = [{**by_key[key], "evidence": attached[key]} for key in sorted(by_key)]
    examples = [example for row in rows for example in row["evidence"]]
    passed = sum(example["result"]["status"] == "PASS" for example in examples)
    covered = sum(any(e["result"]["status"] == "PASS" for e in row["evidence"]) for row in rows)
    return seal(
        {
            "schema_id": "openpine.builtin_evidence.v1",
            "surface_hash": surface["content_hash"],
            "corpus_hash": corpus["content_hash"],
            "trace_comparison": report,
            "trace_comparison_scope": "independent_values_only_not_execution_identity",
            "execution_evidence": {
                "scope": "assigned_case_binding_paths",
                "assigned": len(examples),
                "passed": passed,
                "failed": len(examples) - passed,
                "all_assigned_passed": bool(examples) and passed == len(examples),
            },
            "rows": rows,
            "denominator": len(rows),
            "signatures_with_passing_examples": covered,
            "signatures_without_passing_examples": len(rows) - covered,
            "full_builtin_expected_accepted": False,
            "tradingview_verified": False,
        }
    )
