"""Run fixed source programs against independently authored expectations."""

import argparse
import sys

from packages.compiler.semantic.specification import ROOT, encode, index, read_json, safe_path, sha
from packages.contracts import LiteralIR, OpenPineError, UnaryIR
from packages.execution.session import PROFILE_ID, Session
from packages.runtime.engine import program_hash

CASES = "conformance/literal-harness/cases.json"
REPORT = "conformance/results/literal-harness.1.json"


def operations(node):
    if isinstance(node, LiteralIR):
        return set()
    if isinstance(node, UnaryIR):
        return {f"operator.unary.{node.operator}"} | operations(node.operand)
    return {node.operation} | set().union(*(operations(argument.expression) for argument in node.arguments))


def run_cases(session=None):
    session = session if session is not None else Session()
    path = ROOT / CASES
    corpus = read_json(path)
    if corpus["schema_version"] != "openpine.conformance.cases.1" or corpus["implementation_profile_id"] != PROFILE_ID:
        raise ValueError("Unsupported conformance corpus")
    if corpus["oracle"]["kind"] != "independent_manual_expectations" or corpus["oracle"]["external_pine_execution"] is not False:
        raise ValueError("Corpus oracle attribution has changed")
    index(corpus["cases"], "case_id")
    assertions = index(session.snapshot.registry["assertions"], "assertion_id")
    rules = index(session.snapshot.registry["rules"], "rule_id")
    if not set(corpus["rule_refs"]) <= rules.keys() or not set(corpus["assertion_refs"]) <= assertions.keys():
        raise ValueError("Corpus refers to missing specification entries")
    results = []
    exercised = set()
    for case in corpus["cases"]:
        source_path = safe_path(path.parent, case["source"])
        relative = source_path.relative_to(ROOT).as_posix()
        raw = source_path.read_bytes()
        ir_hash = None
        try:
            program, result = session.run(raw.decode("utf-8"), relative)
            observed = {"status": "executed", "title": result.declaration.title,
                        "shorttitle": result.declaration.shorttitle, "overlay": result.declaration.overlay,
                        "outputs": [{"title": output.title, "type": output.value_type.base,
                                     "values": list(output.values)} for output in result.outputs]}
            observed_version = result.identity.language_version
            ir_hash = program_hash(program)
            for statement in program.statements:
                exercised |= operations(statement)
            if len({output.output_id for output in result.outputs}) != len(result.outputs):
                raise AssertionError("Outputs share a call-site identity")
        except OpenPineError as error:
            observed = {"status": "rejected", "category": error.diagnostic.category, "code": error.diagnostic.code}
            observed_version = error.diagnostic.language_version
        if encode(observed) != encode(case["expected"]):
            raise AssertionError(f"{case['case_id']}: expected {case['expected']!r}, got {observed!r}")
        if observed_version != case["language_version"]:
            raise AssertionError(f"{case['case_id']}: wrong language profile")
        results.append({"case_id": case["case_id"], "source_sha256": sha(raw), "program_sha256": ir_hash,
                        "expected_sha256": sha(encode(case["expected"])), "observed": observed, "passed": True})
    return {"schema_version": "openpine.conformance.results.1", "implementation_profile_id": PROFILE_ID,
            "implementation_sha256": session.implementation_sha256,
            "spec_lock_sha256": sha(encode(session.snapshot.lock)), "cases_sha256": sha(path.read_bytes()),
            "python": sys.version.split()[0], "oracle": corpus["oracle"], "results": results,
            "executed_programs": sum(row["observed"]["status"] == "executed" for row in results),
            "expected_rejections": sum(row["observed"]["status"] == "rejected" for row in results),
            "exercised_operations": sorted(exercised),
            "scope": "Literal/call harness, one synthetic observation, no market data or complete Pine-version compatibility claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="record newly verified results")
    args = parser.parse_args(argv)
    report = run_cases()
    encoded = encode(report)
    path = ROOT / REPORT
    if args.write_report:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    elif not path.is_file() or path.read_bytes() != encoded:
        raise SystemExit("Conformance report is missing/stale; review changes and run --write-report.")
    print(f"Conformance: {report['executed_programs']} executed programs, "
          f"{report['expected_rejections']} expected rejections, Python {report['python']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
