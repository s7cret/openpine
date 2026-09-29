"""Retain reviewed unavailable identities when a corrected catalog removes them."""

import json
from importlib.resources import files
from pathlib import Path

from pine2ast.catalog import CatalogRepository

from openpine.verification.builtins import build_builtin_surface
from openpine.verification.evidence_index import compare_surface_lock
from openpine.verification.identity import read_json, verify

HISTORICAL = (
    "pine:function:array.binary_search",
    "pine:function:array.binary_search_leftmost",
    "pine:function:array.binary_search_rightmost",
)
OLD_CONTRACT = "sha256:24b46ccd8cee312f5b88b61923e290e37fa99c2d89df808ca10d24722aacca60"


def _historical_rows():
    path = files("openpine.verification").joinpath("stage2-historical-unavailable.json")
    supplement = json.loads(path.read_text(encoding="utf-8"))
    verify(supplement, "openpine.historical_unavailable_callables.v1")
    return supplement


def test_corrected_v4_catalog_keeps_historical_denominator_but_not_admission():
    assert all(
        name.removeprefix("pine:function:") not in CatalogRepository.default().view(4)["functions"]
        for name in HISTORICAL
    )
    supplement = _historical_rows()
    assert len(supplement["rows"]) == len(HISTORICAL) == 3
    assert supplement["prior_lock_hash"] == "sha256:27d61a34a3429bb6ef4508ecb26d8514cd6a72bef7abeb78906024590bf04b7c"
    surface = build_builtin_surface()
    assert len(surface["rows"]) == 2390
    for name in HISTORICAL:
        rows = [row for row in surface["rows"] if row["pine_version"] == 4 and row["symbol_id"] == name]
        assert len(rows) == 1
        row = rows[0]
        assert row["overload_id"] == f"{name}#canonical"
        assert row["contract_hash"] == OLD_CONTRACT
        assert row["frontend_available"] is False
        assert row["status"] == "UNAVAILABLE"
        assert "HISTORICAL_CATALOG_UNAVAILABLE" in row["reasons"]
        assert row["target_binding"] is None and row["oracle"] == "missing"


def test_locked_historical_identity_cannot_be_dropped_or_credited():
    root = Path(__file__).resolve().parents[1]
    surface = build_builtin_surface()
    lock = read_json(root / "verification/stage2-callable-lock.json")
    comparison = compare_surface_lock(surface, lock)
    assert comparison["ok"], comparison
    assert comparison["locked_count"] == comparison["current_count"] == 2390
    assert {
        row["symbol_id"]
        for row in lock["rows"]
        if row["pine_version"] == 4 and row["symbol_id"] in HISTORICAL
    } == set(HISTORICAL)


def test_historical_resource_is_sealed_and_shipped_with_wheel():
    resource = _historical_rows()
    assert all(row["contract_hash"] == OLD_CONTRACT for row in resource["rows"])
    assert {row["symbol_id"] for row in resource["rows"]} == set(HISTORICAL)
    assert resource["source_commit"] == "1a42c1435a647f1859b96ea2d3cf5e1e8a09147a"
