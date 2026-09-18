"""Required contract dimensions are checked independently, never inferred from green flags."""
from copy import deepcopy
import pytest
import openpine.verification.stage2_catalog as gate


def contract():
    return {"symbol_id":"pine:function:test", "parameters":[{
        "name":"value", "type":"float", "required":False, "default":0.0,
        "qualifier_max":"series"}], "returns":"float", "return_qualifier":"series"}


def test_complete_dimension_shape_is_represented():
    row = contract()
    result = gate._contract_dimensions(row, row, "canonical")
    assert set(result) == set(gate.REQUIRED_CONTRACT_DIMENSIONS)
    assert set(result.values()) == {"REPRESENTED"}


@pytest.mark.parametrize("field,dimension", [
    ("type","parameters"), ("name","parameters"), ("required","parameters"),
    ("default","defaults"), ("qualifier_max","qualifiers")])
def test_removed_parameter_metadata_cannot_remain_green(field, dimension):
    row=contract(); del row["parameters"][0][field]
    assert gate._contract_dimensions(row,row,"canonical")[dimension] == "UNVERIFIED"


@pytest.mark.parametrize("field,dimension", [
    ("symbol_id","overload_identity"), ("returns","return_type"),
    ("return_qualifier","qualifiers"), ("parameters","parameters")])
def test_removed_contract_metadata_cannot_remain_green(field, dimension):
    row=contract(); del row[field]
    assert gate._contract_dimensions(row,row,"canonical")[dimension] == "UNVERIFIED"


def test_wrong_overload_identity_is_unverified():
    row=contract()
    assert gate._contract_dimensions(row,row,"pine:function:other#0")["overload_identity"] == "UNVERIFIED"


def test_omitted_dimension_is_reported_even_when_not_returned(monkeypatch):
    original=gate._contract_dimensions
    def missing(*args):
        result=original(*args); del result["defaults"]; return result
    monkeypatch.setattr(gate,"_contract_dimensions",missing)
    monkeypatch.setattr(gate,"_public_surface_role",lambda *args:"OFFICIAL_PUBLIC")
    packs={v:{"sections":{"functions":{"test":contract()},"methods":{}}} for v in gate.VERSIONS}
    report=gate._contract_denominator(packs,{})
    assert len(report["silent_dimension_omissions"]) == 6
    assert all(row["dimension"] == "defaults" for row in report["silent_dimension_omissions"])
    assert report["dimension_status_counts"]["UNVERIFIED"] == 6


@pytest.mark.parametrize("mutation", ["none", "missing_old", "missing_new", "unreviewed_extra", "renamed_old"])
def test_additive_inventory_keeps_exact_historical_identity(mutation):
    from openpine.verification.pytest_gate import validate_inventory, collection_hash
    original = ["tests/a.py::a", "tests/b.py::b"]
    added = ["tests/new.py::c"]
    expected = {"identity_mode":"reviewed_addition_to_hashed_baseline", "count":3,
        "deselected":0, "baseline":{"count":2,"sha256":collection_hash(original),"deselected":0},
        "added_nodeids":added}
    observed = original + added
    if mutation == "missing_old": observed.remove(original[0])
    elif mutation == "missing_new": observed.remove(added[0])
    elif mutation == "unreviewed_extra": observed.append("tests/extra.py::d")
    elif mutation == "renamed_old": observed[0] = "tests/a.py::different"
    if mutation == "none":
        validate_inventory(observed,expected,0)
    else:
        with pytest.raises(ValueError): validate_inventory(observed,expected,0)
