"""Stage 2.1 catalog/input acceptance: exact installed surface, no silent gaps."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from openpine.verification.identity import read_json
from openpine.verification.stage2_catalog import (
    COMPONENTS,
    build_source_lock,
    build_version_exact_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = ROOT / "verification/stage2-1-authority.json"


def matrix():
    return build_version_exact_catalog(read_json(AUTHORITY))


def row(report, symbol_id):
    return next(item for item in report["rows"] if item["symbol_id"] == symbol_id)


def cell(report, symbol_id, version):
    return next(item for item in row(report, symbol_id)["versions"] if item["pine_version"] == version)


def parameter_names(value):
    return [item["name"] for item in value["signature"]["parameters"]]


def test_catalog_has_six_cells_per_symbol_and_no_unexplained_gap():
    report = matrix()
    assert report["internal_consistency_ok"], report["authority_errors"]
    assert report["catalog_integrity_ok"]
    assert report["ok"] is False
    assert report["stage2_1_acceptance_ok"] is False
    assert report["independent_reference"]["modern_required_names_present"]
    assert report["independent_reference"]["complete_for_stage2_1_acceptance"] is False
    assert report["independent_reference"]["versions_checked"] == [1, 2, 3, 4, 5, 6]
    assert report["independent_reference"]["frozen_baseline_reference"]["ok"]
    assert report["independent_reference"]["frozen_baseline_reference"]["acceptance_role"] == "DRIFT_GUARD_ONLY"
    assert report["independent_reference"]["official_catalog_authority"]["ok"] is True
    assert report["independent_reference"]["external_tradingview_exhaustiveness"] is False
    assert report["independent_reference"]["all_modern_extras_classified"] is True
    assert report["acceptance_blockers"]
    assert report["contract_completeness_ok"] is False
    assert report["authority_coverage_ok"] is False
    assert report["semantic_surface_ok"] is False
    assert any("UNVERIFIED" in value for value in report["acceptance_blockers"])
    assert report["contract_denominator"]["silent_dimension_omissions"] == []
    assert report["contract_denominator"]["form_count"] > 2000
    assert report["contract_denominator"]["dimension_status_counts"]["UNVERIFIED"] > 0
    assert report["cell_count"] == report["symbol_count"] * 6
    assert report["symbol_count"] == 1538
    assert report["unexplained"] == []
    assert report["authority_coverage"]["locked_count"] == 44
    assert report["authority_coverage"]["missing"] == {}
    assert report["authority_coverage"]["unexpected"] == {}
    assert all(len(item["versions"]) == 6 for item in report["rows"])


def test_catalog_status_vocabulary_is_explicit_and_preserves_unknowns():
    report = matrix()
    allowed = {
        "AVAILABLE", "UNAVAILABLE", "DEPRECATED", "CHANGED_SEMANTICS",
        "HOST_DELEGATED", "RUNTIME_DIRECT", "UNVERIFIED",
    }
    observed = {
        flag
        for item in report["rows"]
        for version in item["versions"]
        for flag in version["status_flags"]
    }
    assert observed <= allowed
    assert {"AVAILABLE", "UNAVAILABLE", "RUNTIME_DIRECT", "UNVERIFIED"} <= observed
    assert report["scope"] == (
        "version_exact_catalog_with_exhaustive_denominator_and_explicit_unverified"
    )
    assert report["official_exhaustiveness"] == (
        "name inventory is distinct from complete version-exact contract acceptance"
    )

    # Migrated v6 extras must never silently expand the public Pine surface.
    checks = report["independent_reference"]["official_modern_name_reference"]["checks"]["6"]
    function_roles = checks["categories"]["functions"]["extra_roles"]
    assert function_roles["array.percentile"] == "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED"
    assert function_roles["barssince"] == "LEGACY_SPELLING_REJECTED"
    assert function_roles["array.new<int>"] == "INTERNAL_GENERIC_SPECIALIZATION"
    assert checks["categories"]["keywords"]["extra_roles"]["once"] == "OPENPINE_STAGE2_EXTENSION"
    variable_roles = checks["categories"]["variables"]["extra_roles"]
    assert variable_roles["strategy.risk.cash"] == "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED"

    for item in report["rows"]:
        current = next(value for value in item["versions"] if value["pine_version"] == 6)
        if "array.percentile" in item["names"] and "functions" in item["sections"]:
            assert current["public_surface_role"] == "UNVERIFIED_LOCAL_EXTRA_FAIL_CLOSED"
            assert current["execution"]["public_admission"] == "FAIL_CLOSED"
            assert "RUNTIME_DIRECT" not in current["status_flags"]


@pytest.mark.parametrize("version", [5, 6])
def test_every_modern_input_form_is_authority_locked_and_runtime_mapped(version):
    report = matrix()
    for item in report["rows"]:
        if not any(name == "input" or name.startswith("input.") for name in item["names"]):
            continue
        current = next(value for value in item["versions"] if value["pine_version"] == version)
        if current["availability"] != "AVAILABLE" or "functions" not in item["sections"]:
            continue
        assert current["signature"]["input_contract_revision"] == 2
        assert current["signature"]["allow_extra_positional"] is False
        assert current["execution"]["unmapped_overloads"] == []
        assert "RUNTIME_DIRECT" in current["status_flags"]


def test_modern_input_functions_are_not_backported_as_functions():
    report = matrix()
    for name in (
        "input.bool", "input.color", "input.enum", "input.float", "input.int",
        "input.price", "input.session", "input.source", "input.string",
        "input.symbol", "input.text_area", "input.time", "input.timeframe",
    ):
        symbol = "pine:function:" + name
        assert [cell(report, symbol, version)["availability"] for version in range(1, 5)] == [
            "UNAVAILABLE"
        ] * 4
        assert [cell(report, symbol, version)["availability"] for version in (5, 6)] == [
            "AVAILABLE", "AVAILABLE"
        ]


def test_input_version_differences_and_qualifier_ceilings_are_exact():
    report = matrix()
    generic5 = cell(report, "pine:function:input", 5)
    generic6 = cell(report, "pine:function:input", 6)
    assert parameter_names(generic5) == ["defval", "title", "tooltip", "inline", "group"]
    assert parameter_names(generic6) == [
        "defval", "title", "tooltip", "inline", "group", "display", "active"
    ]
    assert generic6["signature"]["parameters"][-1]["qualifier_max"] == "input"
    assert generic6["signature"]["parameters"][-1]["default"] is True
    assert generic5["signature"]["return_qualifier"] == "series"

    source6 = cell(report, "pine:function:input.source", 6)
    assert source6["signature"]["parameters"][0]["qualifier_max"] == "series"
    assert source6["signature"]["return_qualifier"] == "series"
    assert parameter_names(source6) == [
        "defval", "title", "tooltip", "inline", "group", "display", "active", "confirm"
    ]


def test_numeric_options_bounds_and_defaults_are_disjoint():
    report = matrix()
    for version in (5, 6):
        for name, dtype in (("input.int", "int"), ("input.float", "float")):
            current = cell(report, "pine:function:" + name, version)["signature"]
            assert [p["name"] for p in current["parameters"]][:5] == [
                "defval", "title", "minval", "maxval", "step"
            ]
            assert current["parameters"][4]["default"] == 1
            overload = current["overloads"][0]
            assert [p["name"] for p in overload["parameters"]][:3] == [
                "defval", "title", "options"
            ]
            assert overload["parameters"][2]["type"] == f"array<{dtype}>"
            assert overload["parameters"][2]["required"] is True
            assert not {"minval", "maxval", "step"} & {
                p["name"] for p in overload["parameters"]
            }


def test_symbol_has_no_foreign_options_and_enum_keeps_nominal_rule():
    report = matrix()
    for version in (5, 6):
        symbol = cell(report, "pine:function:input.symbol", version)["signature"]
        assert "options" not in [item["name"] for item in symbol["parameters"]]
        enum = cell(report, "pine:function:input.enum", version)["signature"]
        assert enum["parameter_type_rule_id"] == "parameter.input.same_enum_type.v1"
        assert enum["return_rule_id"] == "return.input.enum_type.v1"
        assert enum["return_qualifier"] == "input"


def test_authority_tamper_and_missing_form_fail_closed():
    authority = read_json(AUTHORITY)
    tampered = copy.deepcopy(authority)
    tampered["input_signature_locks"]["6|input.int|canonical"]["parameters"][0][
        "qualifier_max"
    ] = "series"
    result = build_version_exact_catalog(tampered)
    assert not result["ok"]
    assert any("authority mismatch" in error for error in result["authority_errors"])

    missing = copy.deepcopy(authority)
    missing["input_signature_locks"].pop("6|input.enum|canonical")
    result = build_version_exact_catalog(missing)
    assert not result["ok"]
    assert result["authority_coverage"]["missing"]["6"] == ["input.enum|canonical"]

    full_tampered = copy.deepcopy(authority)
    full_tampered["full_catalog_reference"]["versions"]["6"][
        "unaffected_definition_hash"
    ] = "sha256:" + "0" * 64
    result = build_version_exact_catalog(full_tampered)
    assert not result["ok"]
    full = result["independent_reference"]["frozen_full_catalog_reference"]
    assert not full["ok"]
    assert any(
        "content hash mismatch" in error or "Pine v6" in error
        for error in full["errors"]
    )


def test_source_lock_covers_all_components_and_detects_byte_changes(tmp_path):
    stack = tmp_path / "stack"
    for component in COMPONENTS:
        root = stack / component
        root.mkdir(parents=True)
        (root / "source.py").write_text(f"OWNER={component!r}\n")
    catalog = matrix()
    first = build_source_lock(stack, catalog)
    assert set(first["components"]) == set(COMPONENTS)
    assert all(value["file_count"] == 1 for value in first["components"].values())
    (stack / "pinelib/source.py").write_text("OWNER='changed'\n")
    second = build_source_lock(stack, catalog)
    assert first["content_hash"] != second["content_hash"]
    assert (
        first["components"]["pinelib"]["content_tree_hash"]
        != second["components"]["pinelib"]["content_tree_hash"]
    )


def test_unclosed_direct_contract_remains_unverified():
    from openpine.verification.stage2_catalog import _execution_cell

    symbol = "pine:function:test.synthetic"
    definition = {"parameters": [{"name": "x"}], "overloads": []}
    row = {
        "disposition": "TARGET_DIRECT",
        "version_availability": [6],
        "producer_overload_ids": [symbol + "#canonical"],
        "parameter_bindings": [
            {"parameter_name": "x", "binding": "UNBOUND_FAIL_CLOSED"}
        ],
    }
    execution = _execution_cell(symbol, "test.synthetic", "functions", 6, definition, [row])
    assert "UNVERIFIED" in execution["flags"]
    assert "RUNTIME_DIRECT" not in execution["flags"]
