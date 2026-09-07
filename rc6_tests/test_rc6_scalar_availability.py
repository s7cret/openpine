"""Historical callable admission must use the producer's version owner.

The v4 type-system documentation explicitly dates cast functions to v4.
The pre-v4 float type constant is a separate, retained catalogue identity.
"""

from importlib import import_module

import pytest
from pine2ast.semantic.signatures import SignatureResolver

from openpine.verification.builtins import build_builtin_surface


@pytest.fixture(scope="module")
def scalar_surface():
    return build_builtin_surface()


@pytest.fixture(scope="module")
def scalar_graph():
    return import_module("openpine.verification.capabilities").build_capability_graph()


@pytest.mark.parametrize("version", range(1, 7))
def test_float_surface_retains_exact_unavailable_versions(scalar_surface, version):
    rows = [
        row
        for row in scalar_surface["rows"]
        if row["pine_version"] == version and row["symbol_id"] == "pine:function:float"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["overload_id"] == "pine:function:float#canonical"
    assert row["call_form"] == "FUNCTION"
    assert row["spellings"] == ["float"]
    assert row["status"] == ("RUNTIME_DIRECT" if version >= 4 else "UNAVAILABLE")
    assert ("FRONTEND_VERSION_UNAVAILABLE" in row["reasons"]) is (version < 4)
    assert row["oracle"] == "missing"
    assert len(scalar_surface["rows"]) == 2374
    assert not scalar_surface["full_catalog_verified"]


@pytest.mark.parametrize("version", range(1, 7))
def test_float_graph_retains_exact_unavailable_versions(scalar_graph, version):
    rows = [
        row
        for row in scalar_graph["rows"]
        if row["pine_version"] == version
        and row["symbol_id"] == "pine:function:float"
        and row["category"] == "functions"
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["frontend"] is True  # Entry exists even when its callable is inactive.
    assert row["status"] == ("BOUND" if version >= 4 else "UNAVAILABLE")
    assert ("FRONTEND_VERSION_UNAVAILABLE" in row["reasons"]) is (version < 4)
    assert row["signature_bindings"]
    assert all(
        ("FRONTEND_VERSION_UNAVAILABLE" in binding["reasons"]) is (version < 4)
        for binding in row["signature_bindings"]
    )


def test_surface_consults_producer_availability_owner(monkeypatch):
    original = SignatureResolver.candidate_is_active
    calls = []

    def admission(self, candidate):
        calls.append(candidate["__overload_id"])
        if candidate["__overload_id"] == "pine:function:math.sqrt#canonical":
            return False
        return original(self, candidate)

    monkeypatch.setattr(SignatureResolver, "candidate_is_active", admission)
    surface = build_builtin_surface()
    rows = [row for row in surface["rows"] if row["symbol_id"] == "pine:function:math.sqrt"]
    assert len(rows) == 6
    assert all(row["status"] == "UNAVAILABLE" for row in rows)
    assert all("FRONTEND_VERSION_UNAVAILABLE" in row["reasons"] for row in rows)
    assert "pine:function:math.sqrt#canonical" in calls


def test_graph_consults_producer_availability_owner(monkeypatch):
    original = SignatureResolver.candidate_is_active
    calls = []

    def admission(self, candidate):
        calls.append(candidate["__overload_id"])
        if candidate["__overload_id"] == "pine:function:math.sqrt#canonical":
            return False
        return original(self, candidate)

    monkeypatch.setattr(SignatureResolver, "candidate_is_active", admission)
    graph = import_module("openpine.verification.capabilities").build_capability_graph()
    rows = [row for row in graph["rows"] if row["symbol_id"] == "pine:function:math.sqrt"]
    assert len(rows) == 6
    assert all(row["status"] == "UNAVAILABLE" for row in rows)
    assert all("FRONTEND_VERSION_UNAVAILABLE" in row["reasons"] for row in rows)
    assert "pine:function:math.sqrt#canonical" in calls


@pytest.mark.parametrize("active_first", [False, True])
def test_surface_admits_active_alternative_without_duplicate_denominator(monkeypatch, active_first):
    original = SignatureResolver.candidate_entries

    def alternatives(self, entry):
        candidates = original(self, entry)
        if entry["symbol_id"] != "pine:function:math.sqrt":
            return candidates
        assert len(candidates) == 1
        base = {
            key: value
            for key, value in candidates[0].items()
            if key not in {"added_in", "removed_in"}
        }
        inactive, active = {**base, "added_in": 7}, {**base, "added_in": 1}
        return (active, inactive) if active_first else (inactive, active)

    monkeypatch.setattr(SignatureResolver, "candidate_entries", alternatives)
    surface = build_builtin_surface()
    rows = [row for row in surface["rows"] if row["symbol_id"] == "pine:function:math.sqrt"]
    assert len(surface["rows"]) == 2374
    assert len(rows) == 6
    assert all(row["status"] == "RUNTIME_DIRECT" and not row["reasons"] for row in rows)
    assert all(len(row["spellings"]) == 1 for row in rows)


@pytest.mark.parametrize("active_first", [False, True])
def test_graph_reports_inactive_alternative_without_hiding_active_callable(
    monkeypatch, active_first
):
    original = SignatureResolver.candidate_entries

    def alternatives(self, entry):
        candidates = original(self, entry)
        if entry["symbol_id"] != "pine:function:math.sqrt":
            return candidates
        assert len(candidates) == 1
        base = {
            key: value
            for key, value in candidates[0].items()
            if key not in {"added_in", "removed_in"}
        }
        inactive, active = {**base, "added_in": 7}, {**base, "added_in": 1}
        return (active, inactive) if active_first else (inactive, active)

    monkeypatch.setattr(SignatureResolver, "candidate_entries", alternatives)
    graph = import_module("openpine.verification.capabilities").build_capability_graph()
    rows = [row for row in graph["rows"] if row["symbol_id"] == "pine:function:math.sqrt"]
    assert len(rows) == 6
    assert all(row["status"] == "BOUND" and not row["reasons"] for row in rows)
    for row in rows:
        signatures = row["signature_bindings"]
        assert len(signatures) == 2
        assert sum(not signature["reasons"] for signature in signatures) == 1
        assert (
            sum("FRONTEND_VERSION_UNAVAILABLE" in signature["reasons"] for signature in signatures)
            == 1
        )
