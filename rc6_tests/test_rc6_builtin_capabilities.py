"""Exact builtin availability remains separate from numerical conformance.

These tests use the real installed producer, compiler, ABI, and host graph.
The host import is unconditional when its fixture executes: platforms without
the required POSIX dependencies fail visibly, rather than skipping those tests.
"""

from copy import deepcopy
from dataclasses import replace
from importlib import import_module

import pytest
from ast2python import compile_consumer_bundle
from ast2python.errors import BundleInvariantError
from ast2python.lowering import load_pinelib_target_manifest
from pine2ast.hardening.consumer_bundle import build_consumer_bundle
from pinelib.abi import load_target_manifest

from openpine.verification.builtins import binding_reasons, build_builtin_surface

ALIASES = {
    "exp": "math.exp",
    "round": "math.round",
    "sqrt": "math.sqrt",
    "pow": "math.pow",
    "abs": "math.abs",
    "ceil": "math.ceil",
    "floor": "math.floor",
    "sma": "ta.sma",
    "wma": "ta.wma",
    "macd": "ta.macd",
}


@pytest.fixture(scope="module")
def target():
    return load_pinelib_target_manifest()


@pytest.fixture(scope="module")
def surface():
    return build_builtin_surface()


@pytest.fixture(scope="module")
def capability_owner():
    return import_module("openpine.verification.capabilities")


@pytest.fixture(scope="module")
def graph(capability_owner):
    return capability_owner.build_capability_graph()


def surface_row(report, version, modern, *, overload="canonical", form=None):
    symbol = "pine:function:" + modern
    form = form or ("NAMESPACE_FUNCTION" if version >= 5 else "FUNCTION")
    selected = [
        row
        for row in report["rows"]
        if (row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"])
        == (version, symbol, symbol + "#" + overload, form)
    ]
    assert len(selected) == 1
    return selected[0]


def graph_row(report, version, modern):
    selected = [
        row
        for row in report["rows"]
        if row["pine_version"] == version
        and row["symbol_id"] == "pine:function:" + modern
        and row["category"] == "functions"
    ]
    assert len(selected) == 1
    return selected[0]


def compile_source(source, target):
    return compile_consumer_bundle(
        build_consumer_bundle(source, producer_commit="1" * 40),
        target=target,
        expected_pine2ast_commit="1" * 40,
        producer_commit="2" * 40,
    )


@pytest.mark.parametrize("version", range(1, 7))
def test_surface_uses_exact_historical_spelling_and_call_form(surface, version):
    for legacy, modern in ALIASES.items():
        row = surface_row(surface, version, modern)
        assert row["spellings"] == [modern if version >= 5 else legacy]
        assert row["status"] == "RUNTIME_DIRECT", (modern, row["reasons"])
        assert not row["reasons"]
        assert row["oracle"] == "missing"
        opposite = "FUNCTION" if version >= 5 else "NAMESPACE_FUNCTION"
        assert not any(
            item["pine_version"] == version
            and item["symbol_id"] == row["symbol_id"]
            and item["call_form"] == opposite
            for item in surface["rows"]
        )


@pytest.mark.parametrize("version", range(1, 7))
def test_graph_preserves_active_source_spelling_and_original_target_spelling(
    graph, version
):
    for legacy, modern in ALIASES.items():
        row = graph_row(graph, version, modern)
        assert row["catalog_spelling"] == (modern if version >= 5 else legacy)
        assert row["target_spelling"] == modern
        assert row["frontend"] is True
        assert row["status"] == "BOUND", (modern, row["reasons"])
        assert row["signature_bindings"]
        assert all(not signature["reasons"] for signature in row["signature_bindings"])
        assert {signature["call_form"] for signature in row["signature_bindings"]} == {
            "NAMESPACE_FUNCTION" if version >= 5 else "FUNCTION"
        }
        assert row["oracle"] == "missing"


def test_round_precision_and_legacy_rsi_overloads_remain_version_specific(surface):
    for version in range(1, 7):
        round_rows = [
            row
            for row in surface["rows"]
            if row["pine_version"] == version
            and row["symbol_id"] == "pine:function:math.round"
        ]
        expected = {"pine:function:math.round#canonical"}
        if version >= 4:
            expected.add("pine:function:math.round#overload:0")
        assert {row["overload_id"] for row in round_rows} == expected
        assert all(row["status"] == "RUNTIME_DIRECT" for row in round_rows)
        conventional = surface_row(
            surface,
            version,
            "ta.rsi",
            overload="canonical" if version >= 5 else "overload:0",
        )
        assert conventional["status"] == "RUNTIME_DIRECT"
        if version <= 4:
            ratio = surface_row(surface, version, "ta.rsi", overload="overload:1")
            assert ratio["status"] == "UNAVAILABLE"
            assert "COMPILER_BINDING_MISSING" in ratio["reasons"]
        else:
            assert not any(
                row["pine_version"] == version
                and row["symbol_id"] == "pine:function:ta.rsi"
                and row["overload_id"].endswith("#overload:1")
                for row in surface["rows"]
            )


def test_one_bound_rsi_overload_does_not_mark_all_legacy_rsi_bound(graph):
    for version in range(1, 5):
        row = graph_row(graph, version, "ta.rsi")
        assert row["status"] == "UNAVAILABLE"
        signatures = {item["overload_id"]: item for item in row["signature_bindings"]}
        assert not signatures["pine:function:ta.rsi#overload:0"]["reasons"]
        assert (
            "COMPILER_BINDING_MISSING"
            in signatures["pine:function:ta.rsi#overload:1"]["reasons"]
        )


def faulty_target(target, fault):
    modern = "math.round" if fault == "drop_precision" else "math.sqrt"
    overload = "overload:0" if fault == "drop_precision" else "canonical"
    symbol = "pine:function:" + modern
    key = (symbol, symbol + "#" + overload, "NAMESPACE_FUNCTION")
    original = target.call_bindings[key]
    if fault == "drop_precision":
        mappings = tuple(
            item
            for item in original.parameter_bindings
            if item.get("source") != "precision"
        )
    elif fault == "required_default":
        mappings = (
            {"abi_parameter": "value", "binding": "ABI_DEFAULT", "source": None},
        )
    else:
        mappings = (
            {
                "abi_parameter": "value",
                "binding": "SOURCE_PARAMETER",
                "source": "ghost",
            },
        )
    return (
        replace(
            target,
            call_bindings={
                **target.call_bindings,
                key: replace(original, parameter_bindings=mappings),
            },
        ),
        modern,
        overload,
    )


@pytest.mark.parametrize(
    "fault", ["ghost_source", "required_default", "drop_precision"]
)
def test_surface_rejects_incomplete_argument_mapping_and_compiler_confirms_it(
    target, fault
):
    changed, modern, overload = faulty_target(target, fault)
    row = surface_row(
        build_builtin_surface(target=changed), 6, modern, overload=overload
    )
    assert row["status"] == "UNAVAILABLE"
    assert "A2P_PINELIB_SOURCE_PARAMETER" in row["reasons"]
    expression = "math.round(1.25,1)" if fault == "drop_precision" else "math.sqrt(4)"
    with pytest.raises(BundleInvariantError, match="A2P_PINELIB_SOURCE_PARAMETER"):
        compile_source(
            f'//@version=6\nindicator("mapping")\nplot({expression})\n', changed
        )
    if fault == "drop_precision":
        single = surface_row(build_builtin_surface(target=changed), 6, modern)
        assert single["status"] == "RUNTIME_DIRECT"


@pytest.mark.parametrize(
    "fault", ["ghost_source", "required_default", "drop_precision"]
)
def test_graph_rejects_partial_parameter_mapping(
    capability_owner, monkeypatch, target, fault
):
    changed, modern, _ = faulty_target(target, fault)
    monkeypatch.setattr(
        capability_owner, "load_pinelib_target_manifest", lambda: changed
    )
    row = graph_row(capability_owner.build_capability_graph(), 6, modern)
    assert row["status"] == "UNAVAILABLE"
    assert "A2P_PINELIB_SOURCE_PARAMETER" in row["reasons"]


def test_optional_abi_default_is_valid_for_one_arg_round_but_not_dropped_explicit_precision(
    target,
):
    symbol = "pine:function:math.round"
    key = (symbol, symbol + "#canonical", "NAMESPACE_FUNCTION")
    original = target.call_bindings[key]
    # This overload only has `number`; precision belongs to another exact ID.
    changed = replace(
        original,
        parameter_bindings=(
            original.parameter_bindings[0],
            {
                "abi_parameter": "precision",
                "binding": "ABI_DEFAULT",
                "source": None,
            },
        ),
    )
    candidate = replace(target, call_bindings={**target.call_bindings, key: changed})
    report = build_builtin_surface(target=candidate)
    assert surface_row(report, 6, "math.round")["status"] == "RUNTIME_DIRECT"
    assert (
        surface_row(report, 6, "math.round", overload="overload:0")["status"]
        == "RUNTIME_DIRECT"
    )
    compile_source(
        '//@version=6\nindicator("default")\nplot(math.round(1.5))\n', candidate
    )


def test_missing_historical_row_does_not_create_a_backport_from_canonical_identity(
    capability_owner, monkeypatch, target
):
    raw = deepcopy(load_target_manifest())
    raw["historical_call_bindings"] = [
        row for row in raw["historical_call_bindings"] if row["name"] != "exp"
    ]
    symbol = "pine:function:math.exp"
    changed = target.without_call_binding((symbol, symbol + "#canonical", "FUNCTION"))
    monkeypatch.setattr(capability_owner, "load_target_manifest", lambda: raw)
    monkeypatch.setattr(
        capability_owner, "load_pinelib_target_manifest", lambda: changed
    )
    report = capability_owner.build_capability_graph()
    for version in range(1, 5):
        row = graph_row(report, version, "math.exp")
        assert row["status"] == "UNAVAILABLE"
        assert row["catalog_spelling"] == "math.exp"
        assert row["frontend"] is False
        assert "FRONTEND_UNAVAILABLE" in row["reasons"]
    for version in (5, 6):
        assert graph_row(report, version, "math.exp")["status"] == "BOUND"


def test_historical_spelling_must_resolve_to_its_declared_canonical_symbol(
    capability_owner, monkeypatch, target
):
    raw = deepcopy(load_target_manifest())
    exp = next(row for row in raw["historical_call_bindings"] if row["name"] == "exp")
    exp["name"] = "sqrt"
    symbol = "pine:function:math.exp"
    changed = target.without_call_binding((symbol, symbol + "#canonical", "FUNCTION"))
    monkeypatch.setattr(capability_owner, "load_target_manifest", lambda: raw)
    monkeypatch.setattr(
        capability_owner, "load_pinelib_target_manifest", lambda: changed
    )
    report = capability_owner.build_capability_graph()
    for version in range(1, 5):
        row = graph_row(report, version, "math.exp")
        assert row["status"] == "UNAVAILABLE"
        assert "FRONTEND_SYMBOL_MISMATCH" in row["reasons"]
    # The real sqrt remains valid. Its availability must not be attributed to exp.
    assert graph_row(report, 4, "math.sqrt")["status"] == "BOUND"


def test_metadata_dependent_input_is_unverified_instead_of_structurally_bound(surface):
    row = surface_row(surface, 6, "input.int")
    assert row["status"] == "UNVERIFIED"
    assert row["reasons"] == ["A2P_PINELIB_INPUT_METADATA_UNVERIFIED"]


def test_surface_denominator_and_versions_survive_missing_binding(target, surface):
    symbol = "pine:function:math.sqrt"
    changed = target.without_call_binding(
        (symbol, symbol + "#canonical", "NAMESPACE_FUNCTION")
    )
    report = build_builtin_surface(target=changed)

    def identity(row):
        return (
            row["pine_version"],
            row["symbol_id"],
            row["overload_id"],
            row["call_form"],
        )

    assert {identity(row) for row in report["rows"]} == {
        identity(row) for row in surface["rows"]
    }
    for version in (5, 6):
        row = surface_row(report, version, "math.sqrt")
        assert row["status"] == "UNAVAILABLE"
        assert "COMPILER_BINDING_MISSING" in row["reasons"]
    for version in range(1, 5):
        assert surface_row(report, version, "math.sqrt")["status"] == "RUNTIME_DIRECT"


def test_value_injection_must_match_value_emission_instead_of_generic_call_rules(
    target,
):
    original = target.value_bindings["pine:variable:close"]
    assert not binding_reasons(original, 6)
    wrong = replace(
        original,
        parameter_bindings=(
            {
                "abi_parameter": "tx",
                "binding": "INJECTED",
                "source": "SOURCE_SPAN",
            },
        ),
    )
    changed = replace(
        target, value_bindings={**target.value_bindings, original.symbol_id: wrong}
    )
    with pytest.raises(
        BundleInvariantError, match="A2P_PINELIB_VALUE_PARAMETER_BINDING"
    ):
        compile_source('//@version=6\nindicator("value")\nplot(close)\n', changed)
    assert "A2P_PINELIB_VALUE_PARAMETER_BINDING" in binding_reasons(wrong, 6)


def test_graph_does_not_mark_unsupported_value_injection_bound(
    capability_owner, monkeypatch, target
):
    original = target.value_bindings["pine:variable:close"]
    wrong = replace(
        original,
        parameter_bindings=(
            {
                "abi_parameter": "tx",
                "binding": "INJECTED",
                "source": "SOURCE_SPAN",
            },
        ),
    )
    changed = replace(
        target, value_bindings={**target.value_bindings, original.symbol_id: wrong}
    )
    monkeypatch.setattr(
        capability_owner, "load_pinelib_target_manifest", lambda: changed
    )
    rows = [
        row
        for row in capability_owner.build_capability_graph()["rows"]
        if row["symbol_id"] == original.symbol_id and row["category"] == "variables"
    ]
    assert {row["pine_version"] for row in rows} == set(range(1, 7))
    assert all(row["status"] == "UNAVAILABLE" for row in rows)
    assert all("A2P_PINELIB_VALUE_PARAMETER_BINDING" in row["reasons"] for row in rows)
