"""Frozen manual scalar examples through five real execution paths.

The original builtin corpus stays byte-for-byte frozen. This separately locked
corpus expands exact version/overload evidence without inferring full parity.
"""

import os
from pathlib import Path

import pytest

from openpine.verification.builtins import build_builtin_surface, builtin_evidence_report
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json
from rc6_tests.test_rc6_builtin_expected import PATHS, _fixture_argument, execute_case
from pinelib import na

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-scalars-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
assert MANIFEST["content_hash"] == LOCK["content_hash"]
assert len(MANIFEST["cases"]) == LOCK["case_count"] == 75


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
def test_independent_scalar_expected(case, path, scalar_observations):
    observation = execute_case(case, path, corpus_path=CORPUS)
    scalar_observations.setdefault(path, {})[case["id"]] = observation
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    difference = first_difference(
        expected,
        {"compile": observation["compile"], "events": observation["events"]},
        case["tolerance"],
    )
    assert difference is None, difference
    if output := os.environ.get("OPENPINE_STAGE1_EVIDENCE"):
        write_json(
            Path(output) / "builtin-scalar-observations" / path / (case["id"] + ".json"),
            observation,
        )


@pytest.fixture(scope="module")
def scalar_observations():
    observations = {}
    yield observations
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {
        (row["pine_version"], row["symbol_id"], row["overload_id"], row["call_form"]): row
        for row in surface["rows"]
    }
    write_json(Path(output) / "builtin-scalar-surface.json", surface)
    for path, observed in observations.items():
        assignments = []
        for case_id, observation in observed.items():
            for binding in observation["executed_bindings"]:
                row = indexed[tuple(binding)]
                assert row["status"] == "RUNTIME_DIRECT"
                assignments.append(
                    {
                        **{
                            key: row[key]
                            for key in (
                                "pine_version",
                                "symbol_id",
                                "overload_id",
                                "call_form",
                                "contract_hash",
                            )
                        },
                        "case_id": case_id,
                        "path": path,
                    }
                )
        report = builtin_evidence_report(
            surface, CORPUS, observed, corpus_hash=LOCK["content_hash"], assignments=assignments
        )
        directory = Path(output) / "builtin-scalar-reports" / path
        write_json(directory / "observations.json", observed)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "report.json", report)
        assert report["execution_evidence"]["all_assigned_passed"]
        assert not report["full_builtin_expected_accepted"]


def test_scalar_corpus_has_independent_provenance_and_exact_version_denominator():
    cases = MANIFEST["cases"]
    assert {case["pine_version"] for case in cases} == set(range(1, 7))
    assert all(case["oracle"]["kind"] == "manual_fixture" for case in cases)
    assert all(case["tolerance"] == {"absolute": 0, "relative": 0} for case in cases)
    inferred = [
        case for case in cases if "INFERRED_FROM_GENERAL_DOCS" in case["oracle"]["provenance"]
    ]
    assert len(inferred) == 12
    assert {case["pine_version"] for case in inferred} == {4, 5, 6}
    assert {
        case["pine_version"]
        for case in cases
        if "-float-" in case["id"] and case["id"].split("-")[1] == "float"
    } == {4, 5, 6}
    assert MANIFEST["profile"] == "engineering"


@pytest.mark.parametrize(
    "expected,actual", [(False, 0), (True, 1), ({"$na": True}, False), ({"$na": True}, None)]
)
def test_scalar_comparison_preserves_bool_na_transport_distinctions(expected, actual):
    assert first_difference(expected, actual, {"absolute": 0, "relative": 0}) is not None


def test_scalar_fixture_na_token_is_explicit_and_preserves_other_values():
    assert _fixture_argument({"$na": True}, 7.0) is na
    assert _fixture_argument({"$pine": "na"}, 7.0) is na
    for value in (False, True, 0, 1, None, {"$na": 1}, {"$na": False}, {"$na": True, "extra": 1}):
        actual = _fixture_argument(value, 7.0)
        assert actual is not na
        assert type(actual) is type(value)
        assert actual == value
