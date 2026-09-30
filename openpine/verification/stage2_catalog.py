"""Stage 2.1 version-exact catalog and source-lock evidence.

The matrix covers the complete local Pine v1-v6 catalog. A frozen incoming
archive is used only as a drift baseline; it is not treated as independent
official authority. Stage 2.1 inventory integrity is distinct from acceptance: every public
name/form/contract dimension stays represented, and UNVERIFIED dimensions
block full acceptance rather than being silently treated as complete. Independent builtin behaviour
and TradingView 1:1 oracle evidence remain Stage 2.9 concerns. Runtime binding
never implies TradingView conformance.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from collections.abc import Mapping
from importlib.resources import files
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from pine2ast.catalog import CatalogRepository
from pinelib.abi import load_target_manifest

from openpine.verification.identity import canonical, digest, seal, verify
from openpine.verification.execution_identity import source_snapshot

VERSIONS = tuple(range(1, 7))
SECTIONS = (
    "annotations",
    "constants",
    "declarations",
    "enum_values",
    "functions",
    "keywords",
    "methods",
    "namespaces",
    "operators",
    "types",
    "variables",
)
COMPONENTS = (
    "openpine-contracts",
    "pine2ast",
    "ast2python",
    "pinelib",
    "backtest_engine",
    "marketdata-provider",
    "optimizer",
    "openpine",
)


# The modern TradingView name indexes are the public v5/v6 denominator.
# Migrated registry rows outside that denominator must be classified instead of
# silently becoming public Pine syntax.
_MODERN_FAIL_CLOSED_FUNCTION_EXTRAS = {
    "array.new",
    "array.percentile",
    "request.news",
    "strategy.closedtrades",
    "strategy.opentrades",
    "ta.obv",
}
_MODERN_LEGACY_FUNCTION_EXTRAS = {"barssince", "highest", "lowest", "valuewhen"}
_MODERN_FAIL_CLOSED_VARIABLE_EXTRAS = {
    "strategy.risk.cash",
    "strategy.risk.fixed",
    "strategy.risk.percent_of_equity",
}


def _modern_reference_index(version: int) -> Mapping[str, Any]:
    path = files("pine2ast.reference_catalog").joinpath(
        f"official_pine_v{version}_reference_index.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _modern_extra_role(version: int, section: str, name: str, pack: Mapping[str, Any]) -> str:
    if section == "variables" and name in pack["sections"].get("constants", {}):
        return "MIRRORED_CONSTANT_COMPATIBILITY"
    if section == "variables" and name in _MODERN_FAIL_CLOSED_VARIABLE_EXTRAS:
        return "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED"
    if section == "types":
        return "INTERNAL_TYPE_PROJECTION"
    if section == "functions":
        if name in _MODERN_LEGACY_FUNCTION_EXTRAS:
            return "LEGACY_SPELLING_REJECTED"
        if (
            name.startswith("array.new<")
            or name.startswith("map.new<")
            or name.startswith("matrix.new<")
        ):
            return "INTERNAL_GENERIC_SPECIALIZATION"
        if name in _MODERN_FAIL_CLOSED_FUNCTION_EXTRAS or name.startswith("array.from<"):
            return "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED"
    if section == "keywords" and version == 6 and name == "once":
        return "OPENPINE_STAGE2_EXTENSION"
    return "UNCLASSIFIED_LOCAL_EXTRA"


def _public_surface_role(version: int, section: str, name: str, pack: Mapping[str, Any]) -> str:
    if version <= 4:
        return "HISTORICAL_DENOMINATOR"
    index = _modern_reference_index(version)
    categories = index.get("categories", {})
    if section not in categories:
        return "LOCAL_STRUCTURAL_DENOMINATOR"
    if name in set(categories.get(section, [])):
        return "OFFICIAL_PUBLIC"
    return _modern_extra_role(version, section, name, pack)


REQUIRED_CONTRACT_DIMENSIONS = (
    "parameters",
    "overload_identity",
    "defaults",
    "return_type",
    "qualifiers",
)


def _contract_dimensions(
    definition: Mapping[str, Any], candidate: Mapping[str, Any], form_id: str
) -> dict[str, str]:
    """Validate independent required dimensions, including each parameter's shape.

    Presence of a list is not proof that its members specify types/defaults.
    Explicit generic rules count as represented contracts, not independent oracles.
    """
    parameters = candidate.get("parameters", definition.get("parameters"))
    valid_list = isinstance(parameters, list) and all(isinstance(p, Mapping) for p in parameters)
    rows = parameters if valid_list else []
    parameter_ok = (
        valid_list
        and all(
            isinstance(p.get("name"), str)
            and p["name"]
            and isinstance(p.get("type"), str)
            and p["type"] not in {"", "unknown"}
            and type(p.get("required")) is bool
            for p in rows
        )
        and len({p.get("name") for p in rows}) == len(rows)
    )
    defaults_ok = valid_list and all(
        p.get("required") is True
        or p.get("variadic") is True
        or "default" in p
        or bool(p.get("default_rule_id"))
        for p in rows
    )
    return_value = candidate.get("returns", definition.get("returns"))
    return_ok = (isinstance(return_value, str) and return_value not in {"", "unknown"}) or bool(
        candidate.get("return_rule_id", definition.get("return_rule_id"))
    )
    qualifiers = {"const", "input", "simple", "series"}
    qualifier_ok = (
        valid_list
        and all(p.get("qualifier_max") in qualifiers for p in rows)
        and (candidate.get("return_qualifier", definition.get("return_qualifier")) in qualifiers)
    )
    symbol = definition.get("symbol_id")
    overload_ok = (
        isinstance(symbol, str)
        and symbol.startswith("pine:")
        and (
            form_id == "canonical"
            or (isinstance(form_id, str) and form_id.startswith(symbol + "#"))
        )
    )
    return {
        key: "REPRESENTED" if ok else "UNVERIFIED"
        for key, ok in (
            ("parameters", parameter_ok),
            ("overload_identity", overload_ok),
            ("defaults", defaults_ok),
            ("return_type", return_ok),
            ("qualifiers", qualifier_ok),
        )
    }


def _contract_denominator(
    packs: Mapping[int, Mapping[str, Any]], authority: Mapping[str, Any]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    silent_omissions: list[dict[str, Any]] = []
    locks = authority.get("input_signature_locks", {})
    locks = locks if isinstance(locks, Mapping) else {}

    for version in VERSIONS:
        pack = packs[version]
        for section in ("functions", "methods"):
            for name, definition in sorted(pack["sections"].get(section, {}).items()):
                role = _public_surface_role(version, section, name, pack)
                if version >= 5 and role != "OFFICIAL_PUBLIC":
                    continue
                forms: list[tuple[str, Mapping[str, Any]]] = [("canonical", definition)]
                forms.extend(
                    (str(item.get("overload_id")), item)
                    for item in definition.get("overloads", [])
                    if isinstance(item, Mapping)
                )
                for form_id, candidate in forms:
                    dimension_status = _contract_dimensions(definition, candidate, form_id)
                    # The expected set is independent of the produced mapping. A
                    # missing key cannot disappear merely by not iterating over it.
                    for key in REQUIRED_CONTRACT_DIMENSIONS:
                        if key not in dimension_status:
                            silent_omissions.append(
                                {
                                    "pine_version": version,
                                    "section": section,
                                    "name": name,
                                    "form_id": form_id,
                                    "dimension": key,
                                }
                            )
                        status_counts[dimension_status.get(key, "UNVERIFIED")] += 1
                    lock_key = f"{version}|{name}|{form_id}"
                    rows.append(
                        {
                            "pine_version": version,
                            "section": section,
                            "name": name,
                            "symbol_id": definition.get("symbol_id"),
                            "form_id": form_id,
                            "public_surface_role": role,
                            "authority_status": (
                                "INDEPENDENT_INPUT_LOCK"
                                if lock_key in locks
                                else "EXPLICIT_LOCAL_OR_UNVERIFIED"
                            ),
                            "dimensions": dimension_status,
                        }
                    )
    body = {
        "schema_id": "openpine.stage2_1_contract_denominator.v1",
        "policy": (
            "Every public callable form has explicit parameter/overload/default/return/qualifier "
            "dimension cells. Missing independent proof remains UNVERIFIED and stays in the denominator; "
            "Stage 2.9 owns independent builtin behavior/oracle closure."
        ),
        "required_dimensions": list(REQUIRED_CONTRACT_DIMENSIONS),
        "form_count": len(rows),
        "dimension_cell_count": len(rows) * 5,
        "dimension_status_counts": dict(sorted(status_counts.items())),
        "silent_dimension_omissions": silent_omissions,
        "rows": rows,
    }
    return seal(body)


def _canonical_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in definition.items() if key not in {"pine_version"}}


def _execution_owner(name: str, section: str) -> str:
    if name == "security" or name.startswith("request."):
        return "openpine/marketdata-provider"
    if name.startswith("strategy."):
        return "openpine/backtest_engine"
    if name.startswith(("plot", "hline", "fill", "line.", "label.", "box.", "table.", "alert")):
        return "openpine/frontend"
    if section in {"keywords", "operators", "declarations", "types", "annotations"}:
        return "pine2ast/ast2python"
    return "pinelib"


def _target_index(raw: Mapping[str, Any]) -> dict[str, list[Mapping[str, Any]]]:
    result: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw.get("rows", []):
        if not isinstance(row, Mapping):
            continue
        symbols = set()
        for value in row.get("source_symbol_ids", []):
            if isinstance(value, str) and value.startswith("pine:"):
                symbols.add(value.split("#", 1)[0])
        symbol = row.get("symbol_id")
        if isinstance(symbol, str) and symbol.startswith("pine:"):
            symbols.add(symbol.split("#", 1)[0])
        for symbol in symbols:
            result[symbol].append(row)
    return result


def _execution_cell(
    symbol_id: str,
    name: str,
    section: str,
    version: int,
    definition: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    active_rows = [row for row in rows if version in row.get("version_availability", [])]
    dispositions = sorted({str(row.get("disposition")) for row in active_rows})
    owners = sorted(
        {
            str(row.get("delegation", {}).get("owner"))
            for row in active_rows
            if isinstance(row.get("delegation"), Mapping) and row.get("delegation", {}).get("owner")
        }
    )
    if "TARGET_DIRECT" in dispositions:
        owners.append("pinelib")
    if not owners:
        owners.append(_execution_owner(name, section))
    producer_ids = set()
    diagnostics = set()
    for row in active_rows:
        producer_ids.update(
            value for value in row.get("producer_overload_ids", []) if isinstance(value, str)
        )
        if row.get("diagnostic"):
            diagnostics.add(str(row["diagnostic"]))
    expected_ids: set[str] = set()
    if section in {"functions", "methods"}:
        expected_ids.add(symbol_id + "#canonical")
        expected_ids.update(
            str(row.get("overload_id"))
            for row in definition.get("overloads", [])
            if isinstance(row, Mapping) and isinstance(row.get("overload_id"), str)
        )
    missing = sorted(expected_ids - producer_ids) if active_rows else sorted(expected_ids)
    direct_rows = [row for row in active_rows if row.get("disposition") == "TARGET_DIRECT"]
    direct_closed = (
        bool(direct_rows)
        and not missing
        and all(
            not row.get("diagnostic")
            and all(
                binding.get("binding") != "UNBOUND_FAIL_CLOSED"
                for binding in row.get("parameter_bindings", [])
                if isinstance(binding, Mapping)
            )
            for row in direct_rows
        )
    )
    delegated_rows = [row for row in active_rows if row.get("disposition") == "TARGET_DELEGATED"]
    delegated_closed = (
        bool(delegated_rows)
        and not missing
        and all(
            isinstance(row.get("delegation"), Mapping)
            and row.get("delegation", {}).get("owner")
            and not row.get("diagnostic")
            for row in delegated_rows
        )
    )
    flags: list[str] = []
    if direct_closed:
        flags.append("RUNTIME_DIRECT")
    if delegated_closed:
        flags.append("HOST_DELEGATED")
    if (
        not flags
        or "UNSUPPORTED_FAIL_CLOSED" in dispositions
        or ("TARGET_DIRECT" in dispositions and not direct_closed)
        or ("TARGET_DELEGATED" in dispositions and not delegated_closed)
    ):
        flags.append("UNVERIFIED")
    return {
        "flags": sorted(set(flags)),
        "dispositions": dispositions,
        "owners": sorted(set(owners)),
        "producer_overload_ids": sorted(producer_ids),
        "unmapped_overloads": missing,
        "diagnostics": sorted(diagnostics),
    }


def _input_forms(pack: Mapping[str, Any]) -> set[str]:
    forms: set[str] = set()
    for name, row in pack["sections"]["functions"].items():
        if not (name == "input" or name.startswith("input.")):
            continue
        forms.add(name + "|canonical")
        forms.update(
            name + "|" + str(item["overload_id"])
            for item in row.get("overloads", [])
            if isinstance(item, Mapping) and isinstance(item.get("overload_id"), str)
        )
    return forms


def _validate_input_authority(
    packs: Mapping[int, Mapping[str, Any]], authority: Mapping[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    locks = authority.get("input_signature_locks", {})
    if not isinstance(locks, Mapping) or not locks:
        return ["input authority contains no signature locks"], {
            "locked": [],
            "missing": [],
            "unexpected": [],
        }
    locked_forms: dict[int, set[str]] = defaultdict(set)
    for key, expected in locks.items():
        try:
            version_text, name, form = key.split("|", 2)
            version = int(version_text)
        except (AttributeError, ValueError):
            errors.append(f"invalid authority key: {key!r}")
            continue
        if version not in {5, 6}:
            errors.append(f"input authority may lock only audited v5/v6 forms: {key}")
            continue
        locked_forms[version].add(name + "|" + form)
        row = packs[version]["sections"]["functions"].get(name)
        if not isinstance(row, Mapping):
            errors.append(f"missing {name} in Pine v{version}")
            continue
        candidate = (
            row
            if form == "canonical"
            else next(
                (item for item in row.get("overloads", []) if item.get("overload_id") == form),
                None,
            )
        )
        if not isinstance(candidate, Mapping):
            errors.append(f"missing {name} form {form} in Pine v{version}")
            continue
        actual = {
            "parameters": [
                {
                    "name": parameter.get("name"),
                    "type": parameter.get("type"),
                    "qualifier_max": parameter.get("qualifier_max"),
                    "required": bool(parameter.get("required")),
                    "default": parameter.get("default"),
                }
                for parameter in candidate.get("parameters", [])
            ],
            "returns": candidate.get("returns", row.get("returns")),
            "return_qualifier": candidate.get("return_qualifier", row.get("return_qualifier")),
            "allow_extra_positional": bool(
                candidate.get("allow_extra_positional", row.get("allow_extra_positional", False))
            ),
        }
        expected_rules = expected.get("rules", {}) if isinstance(expected, Mapping) else {}
        if expected_rules:
            actual["rules"] = {
                rule: candidate.get(rule, row.get(rule)) for rule in sorted(expected_rules)
            }
        if actual != expected:
            errors.append(
                f"authority mismatch for v{version} {name} {form}: "
                f"expected={expected!r} actual={actual!r}"
            )
    expected_forms = {version: _input_forms(packs[version]) for version in (5, 6)}
    missing = {
        str(version): sorted(expected_forms[version] - locked_forms[version])
        for version in (5, 6)
        if expected_forms[version] - locked_forms[version]
    }
    unexpected = {
        str(version): sorted(locked_forms[version] - expected_forms[version])
        for version in (5, 6)
        if locked_forms[version] - expected_forms[version]
    }
    if missing:
        errors.append("authority does not lock every installed v5/v6 input form")
    if unexpected:
        errors.append("authority contains forms absent from the installed catalog")
    return errors, {
        "locked": {str(version): sorted(locked_forms[version]) for version in (5, 6)},
        "missing": missing,
        "unexpected": unexpected,
        "locked_count": sum(len(value) for value in locked_forms.values()),
    }


def _catalog_key_rows(pack: Mapping[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for section, values in sorted(pack["sections"].items()):
        for name, definition in sorted(values.items()):
            rows.append([section, name, definition.get("symbol_id")])
    return rows


def _catalog_definition_rows(
    pack: Mapping[str, Any], excluded: set[tuple[str, str]]
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for section, values in sorted(pack["sections"].items()):
        for name, definition in sorted(values.items()):
            if (section, name) in excluded:
                continue
            rows.append([section, name, definition])
    return rows


def _resource_sha256(resource: Any) -> str:
    return "sha256:" + hashlib.sha256(resource.read_bytes()).hexdigest()


def _rc6_catalog_source_check(packs: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Bind RC6 to reviewed release pack bytes, not the older ZIP baseline."""
    try:
        resource = files("openpine.verification").joinpath("rc6_catalog_source_pin.json")
        pins = json.loads(resource.read_text(encoding="utf-8"))
        verify(pins, "openpine.rc6_catalog_source_pin.v1")
        from openpine.stack_lock import load_stack_lock

        # The packaged coordinated lock is independent of the catalog resource
        # and works in wheels as well as checkouts; a resealed stale pin is not
        # current authority merely because its pack bytes are unchanged.
        expected_ref = next(
            row['commit'] for row in load_stack_lock()['components']
            if row['name'] == 'pine2ast'
        )
        if pins['pine2ast_ref'] != expected_ref:
            raise ValueError('RC6 catalog source ref differs from coordinated lifecycle pin')
        if set(pins["packs"]) != {str(version) for version in VERSIONS}:
            raise ValueError("RC6 source pin has an incomplete version set")
        changed = []
        for version in VERSIONS:
            expected = pins["packs"][str(version)]
            path = files("pine2ast.catalog_data").joinpath("packs", f"pine_v{version}.pack.json")
            if (
                _resource_sha256(path) != expected["resource_sha256"]
                or packs[version]["content_hash"] != expected["content_hash"]
            ):
                changed.append(str(version))
        return {
            "ok": not changed,
            "changed_versions": changed,
            "pine2ast_ref": pins["pine2ast_ref"],
            "pin_hash": pins["content_hash"],
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "changed_versions": [str(v) for v in VERSIONS], "error": str(exc)}


def _catalog_before_audit_repair(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Subtract a sealed reviewed delta before the immutable historical drift check."""
    delta = json.loads(
        files("pine2ast.reference_catalog")
        .joinpath("stage2_audit_catalog_delta.json")
        .read_text(encoding="utf-8")
    )
    if digest({k: v for k, v in delta.items() if k != "content_hash"}) != delta.get("content_hash"):
        raise ValueError("audit catalog delta hash mismatch")
    version = str(pack.get("pine_version", pack.get("version")))
    change = delta["versions"].get(version)
    restored = deepcopy(dict(pack))
    if change is None:
        return restored
    if digest(restored) != change["after_hash"]:
        raise ValueError("catalog differs from reviewed audit endpoint")
    for row in change["changes"]:
        section, name = row["section"], row["name"]
        if restored["sections"][section][name] != row["after"]:
            raise ValueError("unreviewed audit catalog row")
        restored["sections"][section][name] = deepcopy(row["before"])
    restored = {"sections": restored["sections"], **deepcopy(change["before_top"])}
    if digest(restored) != change["before_hash"]:
        raise ValueError("audit catalog historical endpoint mismatch")
    return restored


def _independent_reference_check(
    packs: Mapping[int, Mapping[str, Any]], authority: Mapping[str, Any]
) -> dict[str, Any]:
    """Check drift baseline and genuinely independent official-source authority.

    ``full_catalog_reference`` is deliberately only a frozen baseline from the
    incoming delivery. It proves that the repaired candidate changed only
    reviewed rows, but it cannot establish Pine completeness by itself.
    """
    full = authority.get("full_catalog_reference")
    full_errors: list[str] = []
    full_checked: dict[str, Any] = {}
    if not isinstance(full, Mapping):
        full_errors.append("full_catalog_reference is missing")
    else:
        body = {key: value for key, value in full.items() if key != "content_hash"}
        if full.get("schema_id") != "openpine.stage2_1_full_catalog_reference.v1":
            full_errors.append("full catalog baseline schema mismatch")
        if full.get("content_hash") != digest(body):
            full_errors.append("full catalog baseline content hash mismatch")
        references = full.get("versions", {})
        if not isinstance(references, Mapping):
            full_errors.append("full catalog baseline has no version map")
            references = {}
        for version in VERSIONS:
            expected = references.get(str(version))
            if not isinstance(expected, Mapping):
                full_errors.append(f"missing frozen baseline for Pine v{version}")
                continue
            try:
                pack = _catalog_before_audit_repair(packs[version])
            except (KeyError, ValueError, TypeError) as exc:
                full_errors.append(f"Pine v{version} audit delta invalid: {exc}")
                pack = packs[version]
            allowed_rows = expected.get("allowed_changes", [])
            if not isinstance(allowed_rows, list):
                full_errors.append(f"invalid allowed change list for Pine v{version}")
                allowed_rows = []
            allowed: set[tuple[str, str]] = set()
            change_errors: list[str] = []
            for change in allowed_rows:
                if not isinstance(change, Mapping):
                    change_errors.append("non-object change record")
                    continue
                section, name = change.get("section"), change.get("name")
                if not isinstance(section, str) or not isinstance(name, str):
                    change_errors.append("change record lacks section/name")
                    continue
                allowed.add((section, name))
                actual = pack["sections"].get(section, {}).get(name)
                if actual != change.get("after"):
                    change_errors.append(f"{section}.{name} does not match reviewed after-contract")
            key_hash = digest(_catalog_key_rows(pack))
            unaffected_hash = digest(_catalog_definition_rows(pack, allowed))
            section_counts = {
                section: len(pack["sections"].get(section, {}))
                for section in sorted(pack["sections"])
            }
            top_level_errors: list[str] = []
            changed_top = expected.get("changed_top_level_fields", [])
            expected_top = expected.get("expected_top_level", {})
            for field in changed_top if isinstance(changed_top, list) else []:
                if pack.get(field) != expected_top.get(field):
                    top_level_errors.append(f"{field} differs from reviewed expected value")
            checks = {
                "content_hash": pack.get("content_hash") == expected.get("expected_content_hash"),
                "section_counts": section_counts == expected.get("section_counts"),
                "key_set_hash": key_hash == expected.get("key_set_hash"),
                "unaffected_definition_hash": unaffected_hash
                == expected.get("unaffected_definition_hash"),
                "reviewed_changes": not change_errors,
                "reviewed_top_level": not top_level_errors,
            }
            failed = [name for name, ok in checks.items() if not ok]
            failed.extend(change_errors)
            failed.extend(top_level_errors)
            if failed:
                full_errors.append(f"Pine v{version} frozen baseline mismatch: {failed}")
            full_checked[str(version)] = {
                "checks": checks,
                "allowed_changes": [
                    f"{change.get('section')}.{change.get('name')}"
                    for change in allowed_rows
                    if isinstance(change, Mapping)
                ],
                "actual_content_hash": pack.get("content_hash"),
                "expected_content_hash": expected.get("expected_content_hash"),
                "section_counts": section_counts,
            }

    # Independent TradingView reference indexes define the public modern name
    # denominator.  Extra migrated rows are allowed only when explicitly
    # classified as internal/compatibility/extension/fail-closed.
    modern_checked: dict[str, Any] = {}
    modern_unclassified: list[dict[str, Any]] = []
    for version in (5, 6):
        path = files("pine2ast.reference_catalog").joinpath(
            f"official_pine_v{version}_reference_index.json"
        )
        index = json.loads(path.read_text(encoding="utf-8"))
        categories: dict[str, Any] = {}
        all_required_present = True
        for section, expected_names in index.get("categories", {}).items():
            expected_names_set = set(expected_names)
            actual = set(packs[version]["sections"].get(section, {}))
            missing = sorted(expected_names_set - actual)
            extras = sorted(actual - expected_names_set)
            roles = {
                name: _modern_extra_role(version, section, name, packs[version]) for name in extras
            }
            for name, role in roles.items():
                if role == "UNCLASSIFIED_LOCAL_EXTRA":
                    modern_unclassified.append(
                        {"pine_version": version, "section": section, "name": name}
                    )
            all_required_present = all_required_present and not missing
            categories[section] = {
                "required_count": len(expected_names_set),
                "installed_count": len(actual),
                "missing_required": missing,
                "extra_installed": extras,
                "extra_roles": roles,
            }
        modern_checked[str(version)] = {
            "source": index.get("source"),
            "counts": index.get("counts"),
            "all_required_names_present": all_required_present,
            "all_extras_classified": not any(
                row["pine_version"] == version for row in modern_unclassified
            ),
            "categories": categories,
            "resource_sha256": _resource_sha256(path),
        }
    modern_ok = (
        all(row["all_required_names_present"] for row in modern_checked.values())
        and not modern_unclassified
    )

    # Validate the pinned official-source provenance.  Stage 2.1 does not
    # require every historical callable dimension to be independently known;
    # it requires a complete denominator with unknown dimensions explicitly
    # retained as UNVERIFIED.  The independent behavior oracle is Stage 2.9.
    official = authority.get("official_catalog_authority")
    official_errors: list[str] = []
    version_status: dict[str, str] = {}
    evidence_checks: dict[str, Any] = {}
    historical_chain_ok = False
    if not isinstance(official, Mapping):
        official_errors.append("official_catalog_authority is missing")
    else:
        if official.get("schema_id") != "openpine.stage2_1_official_catalog_authority.v2":
            official_errors.append("official catalog authority schema mismatch")
        if official.get("acceptance_mode") != "EXHAUSTIVE_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED":
            official_errors.append("official catalog authority acceptance mode mismatch")
        source_manifest = files("pine2ast.catalog.sources").joinpath("source_manifest.json")
        source_expected = official.get("source_manifest", {})
        source_actual = _resource_sha256(source_manifest)
        source_ok = (
            isinstance(source_expected, Mapping) and source_expected.get("sha256") == source_actual
        )
        manifest_data = json.loads(source_manifest.read_text(encoding="utf-8"))
        evidence_checks["source_manifest"] = {
            "ok": source_ok,
            "actual_sha256": source_actual,
            "expected_sha256": source_expected.get("sha256")
            if isinstance(source_expected, Mapping)
            else None,
        }
        if not source_ok:
            official_errors.append("official source manifest hash mismatch")

        source_rows = {
            str(row.get("source_id")): row
            for row in manifest_data.get("sources", [])
            if isinstance(row, Mapping) and row.get("source_id")
        }
        migration_expectations = {
            "tv.pine.migration.to_v2": [1, 2],
            "tv.pine.migration.to_v3": [2, 3],
            "tv.pine.migration.to_v4": [3, 4],
            "tv.pine.migration.to_v5": [4, 5],
            "tv.pine.migration.to_v6": [5, 6],
        }
        migration_checks = {}
        for source_id, versions in migration_expectations.items():
            row = source_rows.get(source_id, {})
            ok = (
                isinstance(row, Mapping)
                and row.get("source_kind") == "MIGRATION_GUIDE"
                and row.get("status") == "RETRIEVED"
                and row.get("review_status") == "VERIFIED"
                and row.get("pine_versions") == versions
                and str(row.get("url", "")).startswith("https://www.tradingview.com/")
            )
            migration_checks[source_id] = ok
        language_checks = {}
        for source_id in (
            "tv.pine.language.script_structure",
            "tv.pine.language.type_system",
        ):
            row = source_rows.get(source_id, {})
            ok = (
                isinstance(row, Mapping)
                and row.get("status") == "RETRIEVED"
                and row.get("review_status") == "VERIFIED"
                and row.get("pine_versions") == [1, 2, 3, 4, 5, 6]
                and str(row.get("url", "")).startswith("https://www.tradingview.com/")
            )
            language_checks[source_id] = ok
        historical_chain_ok = all(migration_checks.values()) and all(language_checks.values())
        evidence_checks["historical_migration_chain"] = {
            "ok": historical_chain_ok,
            "migration_guides": migration_checks,
            "language_sources": language_checks,
        }
        if not historical_chain_ok:
            official_errors.append("official historical migration/source provenance is incomplete")

        declared_indexes = official.get("modern_reference_indexes", {})
        for version in (5, 6):
            expected = (
                declared_indexes.get(str(version), {})
                if isinstance(declared_indexes, Mapping)
                else {}
            )
            actual = modern_checked[str(version)]["resource_sha256"]
            ok = isinstance(expected, Mapping) and expected.get("sha256") == actual
            evidence_checks[f"reference_index_v{version}"] = {
                "ok": ok,
                "actual_sha256": actual,
                "expected_sha256": expected.get("sha256")
                if isinstance(expected, Mapping)
                else None,
            }
            if not ok:
                official_errors.append(f"official Pine v{version} reference-index hash mismatch")
        raw_status = official.get("version_contract_status", {})
        if isinstance(raw_status, Mapping):
            version_status = {str(v): str(raw_status.get(str(v), "MISSING")) for v in VERSIONS}
        else:
            version_status = {str(v): "MISSING" for v in VERSIONS}
            official_errors.append("official authority has no version_contract_status map")

        accepted_status = {
            "1": "MIGRATION_CHAIN_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED",
            "2": "MIGRATION_CHAIN_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED",
            "3": "MIGRATION_CHAIN_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED",
            "4": "MIGRATION_CHAIN_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED",
            "5": "OFFICIAL_NAME_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED_CONTRACTS",
            "6": "OFFICIAL_NAME_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED_CONTRACTS",
        }
        for version, expected_status in accepted_status.items():
            if version_status.get(version) != expected_status:
                official_errors.append(
                    f"Pine v{version} denominator status mismatch: "
                    f"{version_status.get(version)!r} != {expected_status!r}"
                )

    if modern_unclassified:
        official_errors.append(f"unclassified modern catalog extras remain: {modern_unclassified}")

    baseline_ok = not full_errors and set(full_checked) == {str(version) for version in VERSIONS}
    rc6_source = _rc6_catalog_source_check(packs)
    official_ok = not official_errors and modern_ok and historical_chain_ok
    return {
        "versions_checked": list(VERSIONS),
        "contract_depth": (
            "complete local contract denominator with explicit UNVERIFIED dimensions; "
            "frozen baseline remains drift-only evidence"
        ),
        "frozen_baseline_reference": {
            "ok": baseline_ok,
            "errors": full_errors,
            "reference_hash": full.get("content_hash") if isinstance(full, Mapping) else None,
            "checks": full_checked,
            "acceptance_role": "DRIFT_GUARD_ONLY",
        },
        "rc6_source_reference": rc6_source,
        # Compatibility alias for older evidence readers; never an acceptance authority.
        "frozen_full_catalog_reference": {
            "ok": baseline_ok,
            "errors": full_errors,
            "reference_hash": full.get("content_hash") if isinstance(full, Mapping) else None,
            "checks": full_checked,
            "acceptance_role": "DRIFT_GUARD_ONLY",
        },
        "official_modern_name_reference": {
            "ok": modern_ok,
            "checks": modern_checked,
            "unclassified_extras": modern_unclassified,
        },
        "official_catalog_authority": {
            "ok": official_ok,
            "acceptance_mode": "EXHAUSTIVE_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED",
            "exhaustive_contract_authority": False,
            "version_contract_status": version_status,
            "evidence_checks": evidence_checks,
            "errors": official_errors,
        },
        "modern_required_names_present": all(
            row["all_required_names_present"] for row in modern_checked.values()
        ),
        "all_modern_extras_classified": not modern_unclassified,
        "external_tradingview_exhaustiveness": False,
        "complete_for_stage2_1_acceptance": rc6_source["ok"] and official_ok,
    }


def build_version_exact_catalog(authority: Mapping[str, Any]) -> dict[str, Any]:
    repo = CatalogRepository.default()
    packs = {version: repo.pack(version) for version in VERSIONS}
    target = load_target_manifest()
    target_rows = _target_index(target)
    by_symbol: dict[str, dict[int, tuple[str, str, Mapping[str, Any]]]] = defaultdict(dict)
    names: dict[str, set[str]] = defaultdict(set)
    sections: dict[str, set[str]] = defaultdict(set)
    for version, pack in packs.items():
        for section in SECTIONS:
            for name, definition in pack["sections"].get(section, {}).items():
                symbol_id = definition.get("symbol_id")
                if not isinstance(symbol_id, str) or not symbol_id.startswith("pine:"):
                    raise ValueError(
                        f"catalog row lacks exact symbol identity: v{version} {section}.{name}"
                    )
                if version in by_symbol[symbol_id]:
                    raise ValueError(f"duplicate symbol identity in v{version}: {symbol_id}")
                by_symbol[symbol_id][version] = (section, name, definition)
                names[symbol_id].add(name)
                sections[symbol_id].add(section)

    rows = []
    status_counts: Counter[str] = Counter()
    unexplained = []
    for symbol_id in sorted(by_symbol):
        cells = []
        prior_hash = None
        for version in VERSIONS:
            item = by_symbol[symbol_id].get(version)
            if item is None:
                cell = {
                    "pine_version": version,
                    "availability": "UNAVAILABLE",
                    "status_flags": ["UNAVAILABLE"],
                    "owner": "pine2ast/catalog",
                    "definition_hash": None,
                    "signature": None,
                    "execution": None,
                    "explanation": "symbol is absent from this version pack",
                }
                status_counts["UNAVAILABLE"] += 1
                cells.append(cell)
                continue
            section, name, definition = item
            projection = _canonical_definition(definition)
            definition_hash = digest(projection)
            flags = ["AVAILABLE"]
            if definition.get("deprecated") or definition.get("status") == "deprecated":
                flags.append("DEPRECATED")
            if prior_hash is not None and definition_hash != prior_hash:
                flags.append("CHANGED_SEMANTICS")
            prior_hash = definition_hash
            execution = _execution_cell(
                symbol_id, name, section, version, definition, target_rows.get(symbol_id, [])
            )
            public_surface_role = _public_surface_role(version, section, name, packs[version])
            execution["public_surface_role"] = public_surface_role
            if public_surface_role in {
                "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED",
                "LEGACY_SPELLING_REJECTED",
            }:
                execution["public_admission"] = "FAIL_CLOSED"
                execution["flags"] = [
                    flag
                    for flag in execution["flags"]
                    if flag not in {"RUNTIME_DIRECT", "HOST_DELEGATED"}
                ]
                execution["flags"].append("UNVERIFIED")
                execution["flags"] = sorted(set(execution["flags"]))
            elif public_surface_role in {
                "INTERNAL_GENERIC_SPECIALIZATION",
                "INTERNAL_TYPE_PROJECTION",
                "MIRRORED_CONSTANT_COMPATIBILITY",
            }:
                execution["public_admission"] = "INTERNAL_ONLY"
            elif public_surface_role == "OPENPINE_STAGE2_EXTENSION":
                execution["public_admission"] = "EXPLICIT_EXTENSION"
            else:
                execution["public_admission"] = "PUBLIC_DENOMINATOR"
            flags.extend(execution["flags"])
            for flag in set(flags):
                status_counts[flag] += 1
            owner = ",".join(execution["owners"])
            if not owner:
                unexplained.append({"symbol_id": symbol_id, "pine_version": version})
            cell = {
                "pine_version": version,
                "availability": "AVAILABLE",
                "status_flags": sorted(set(flags)),
                "owner": owner,
                "definition_hash": definition_hash,
                "signature": projection,
                "public_surface_role": public_surface_role,
                "execution": execution,
                "explanation": (
                    "callable is admitted by an executable/delegated target"
                    if not execution["unmapped_overloads"]
                    and "UNVERIFIED" not in execution["flags"]
                    else "catalog row is retained with an explicit runtime owner or unresolved target"
                ),
            }
            cells.append(cell)
        rows.append(
            {
                "symbol_id": symbol_id,
                "names": sorted(names[symbol_id]),
                "sections": sorted(sections[symbol_id]),
                "versions": cells,
            }
        )
    authority_errors, authority_coverage = _validate_input_authority(packs, authority)
    independent_reference = _independent_reference_check(packs, authority)
    contract_denominator = _contract_denominator(packs, authority)
    denominator_ok = not contract_denominator["silent_dimension_omissions"]
    internal_consistency_ok = not unexplained and not authority_errors and denominator_ok
    contract_completeness_ok = denominator_ok and not contract_denominator[
        "dimension_status_counts"
    ].get("UNVERIFIED", 0)
    authority_coverage_ok = bool(
        independent_reference["official_catalog_authority"].get(
            "exhaustive_contract_authority", False
        )
    )
    semantic_surface_ok = not status_counts.get("UNVERIFIED", 0)
    complete_for_stage2_1 = (
        independent_reference["complete_for_stage2_1_acceptance"]
        and contract_completeness_ok
        and authority_coverage_ok
        and semantic_surface_ok
    )
    independent_reference["name_inventory_complete"] = independent_reference[
        "complete_for_stage2_1_acceptance"
    ]
    independent_reference["complete_for_stage2_1_acceptance"] = complete_for_stage2_1
    body = {
        "schema_id": "openpine.stage2_1_version_exact_catalog.v1",
        "versions": list(VERSIONS),
        "scope": "version_exact_catalog_with_exhaustive_denominator_and_explicit_unverified",
        "official_exhaustiveness": (
            "name inventory is distinct from complete version-exact contract acceptance"
        ),
        "authority": authority,
        "pack_hashes": {str(version): packs[version]["content_hash"] for version in VERSIONS},
        "target_manifest_hash": target["content_hash"],
        "symbol_count": len(rows),
        "cell_count": len(rows) * len(VERSIONS),
        "status_counts": dict(sorted(status_counts.items())),
        "unexplained": unexplained,
        "authority_errors": authority_errors,
        "authority_coverage": authority_coverage,
        "contract_denominator": contract_denominator,
        "independent_reference": independent_reference,
        "internal_consistency_ok": internal_consistency_ok,
        "catalog_integrity_ok": internal_consistency_ok
        and independent_reference["rc6_source_reference"]["ok"],
        "contract_completeness_ok": contract_completeness_ok,
        "authority_coverage_ok": authority_coverage_ok,
        "semantic_surface_ok": semantic_surface_ok,
        "stage2_1_acceptance_ok": internal_consistency_ok and complete_for_stage2_1,
        "acceptance_blockers": (
            []
            if internal_consistency_ok and complete_for_stage2_1
            else list(authority_errors)
            + (
                ["RC6 catalog source pin mismatch"]
                if not independent_reference["rc6_source_reference"]["ok"]
                else []
            )
            + list(independent_reference["official_catalog_authority"]["errors"])
            + (
                ["contract denominator contains silent dimension omissions"]
                if not denominator_ok
                else []
            )
            + (
                ["required callable contracts contain UNVERIFIED dimensions"]
                if not contract_completeness_ok
                else []
            )
            + (
                ["independent historical/full contract authority is incomplete"]
                if not authority_coverage_ok
                else []
            )
            + (
                ["required versioned surface contains UNVERIFIED cells"]
                if not semantic_surface_ok
                else []
            )
        ),
        "ok": internal_consistency_ok and complete_for_stage2_1,
        "rows": rows,
    }
    return seal(body)


def _git_head(root: Path) -> str | None:
    try:
        git = shutil.which("git")
        if git is None:
            return None
        return subprocess.check_output(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_source_lock(stack_root: Path, matrix: Mapping[str, Any]) -> dict[str, Any]:
    roots = {name: stack_root / name for name in COMPONENTS}
    missing = [name for name, root in roots.items() if not root.is_dir()]
    if missing:
        raise ValueError(f"missing Stage 2.1 component: {missing[0]}")
    snapshot = source_snapshot(roots)
    components = {}
    for name, root in roots.items():
        component = snapshot["components"][name]
        files = component["files"]
        components[name] = {
            "base_commit": _git_head(root),
            "file_count": component["file_count"],
            "content_tree_hash": digest(files),
            "files": files,
        }
    return seal(
        {
            "schema_id": "openpine.stage2_1_source_lock.v2",
            "identity_policy": snapshot["policy"],
            "execution_source_hash": snapshot["content_hash"],
            "components": components,
            "catalog_matrix_hash": matrix["content_hash"],
            "catalog_pack_hashes": matrix["pack_hashes"],
            "target_manifest_hash": matrix["target_manifest_hash"],
        }
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")
