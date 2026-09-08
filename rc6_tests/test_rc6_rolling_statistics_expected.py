"""Independent rolling-window and estimate tables through five execution paths."""

import os
from pathlib import Path

import pytest

from openpine.verification.builtins import (
    build_builtin_surface,
    builtin_evidence_report,
)
from openpine.verification.conformance import first_difference, load_corpus
from openpine.verification.identity import read_json, write_json
from rc6_tests.test_rc6_builtin_expected import PATHS, execute_case

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "verification/builtin-rolling-statistics-v1/manifest.json"
MANIFEST = load_corpus(CORPUS)
LOCK = read_json(CORPUS.parent / "lock.json")
assert MANIFEST["content_hash"] == LOCK["content_hash"]
assert len(MANIFEST["cases"]) == LOCK["case_count"] == 66


@pytest.mark.parametrize("case", MANIFEST["cases"], ids=lambda case: case["id"])
@pytest.mark.parametrize("path", PATHS)
def test_independent_rolling_statistics_expected(case, path, observations):
    observation = execute_case(case, path, corpus_path=CORPUS)
    observations.setdefault(path, {})[case["id"]] = observation
    expected = read_json(CORPUS.parent / case["expected"]["path"])
    assert (
        first_difference(
            expected,
            {"compile": observation["compile"], "events": observation["events"]},
            case["tolerance"],
        )
        is None
    )


@pytest.fixture(scope="module")
def observations():
    observed = {}
    yield observed
    if not (output := os.environ.get("OPENPINE_STAGE1_EVIDENCE")):
        return
    surface = build_builtin_surface()
    indexed = {
        (
            row["pine_version"],
            row["symbol_id"],
            row["overload_id"],
            row["call_form"],
        ): row
        for row in surface["rows"]
    }
    write_json(Path(output) / "builtin-rolling_statistics-surface.json", surface)
    for path, results in observed.items():
        assignments = []
        for case_id, observation in results.items():
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
            surface,
            CORPUS,
            results,
            corpus_hash=LOCK["content_hash"],
            assignments=assignments,
        )
        directory = Path(output) / "builtin-rolling_statistics-reports" / path
        write_json(directory / "observations.json", results)
        write_json(directory / "assignments.json", assignments)
        write_json(directory / "report.json", report)
        assert report["execution_evidence"]["all_assigned_passed"]
        assert not report["full_builtin_expected_accepted"]


def test_rolling_statistics_corpus_records_independent_sources_and_scope():
    assert {case["pine_version"] for case in MANIFEST["cases"]} == {5, 6}
    assert all(case["oracle"]["kind"] == "manual_fixture" for case in MANIFEST["cases"])
    assert MANIFEST["profile"] == "engineering"
    assert sum("series-biased" in case["id"] for case in MANIFEST["cases"]) == 4
    assert sum("na-gap" in case["id"] for case in MANIFEST["cases"]) == 14
