"""Static input metadata must agree with independent literal calculations."""

import hashlib
import json
from pathlib import Path

import pytest

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/constant-metadata-manual-expected.json"
EXPECTED_SHA256 = "3e82022b1f91a322f4c84eaa888d4c61fdebdcac5a8e24f9b1d298a5eaa6bc38"
CASES = json.loads(CORPUS.read_bytes())["cases"]


def test_constant_metadata_expectations_are_independently_frozen():
    assert hashlib.sha256(CORPUS.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert len(CASES) == 40
    assert len({row["case_id"] for row in CASES}) == 40
    for row in CASES:
        assert hashlib.sha256(row["source"].encode()).hexdigest() == row["source_sha256"]


@pytest.mark.parametrize("case", CASES, ids=lambda row: row["case_id"])
def test_constant_input_default_matches_manual_calculation(case):
    pins = json.loads((ROOT / "docs/RC6_LIFECYCLE_SOURCES.json").read_bytes())
    result = NativeRC6CompilerAdapter().compile(case["source"], producer_commits=pins)
    assert result.success, result.errors
    descriptors = result.compile_meta["input_descriptors"]
    selected = [row for row in descriptors if row.get("alias") == case["input_alias"]]
    assert len(selected) == 1
    assert selected[0]["kind"] == case["input_kind"]
    actual = selected[0]["default"]
    expected = case["expected_default"]
    if case["input_kind"] == "int":
        assert type(actual) is int
        assert actual == expected
    else:
        # Float input descriptors admit JSON integers and normalize them in
        # InputSpec; the static value must be correct without a spelling rule.
        assert type(actual) in (int, float)
        assert actual == pytest.approx(expected, rel=0, abs=1e-14)
