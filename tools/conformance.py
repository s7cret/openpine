"""Run fixed source programs against independently authored expectations."""

import argparse
import sys

from packages.compiler.semantic.specification import ROOT, encode, index, read_json, safe_path, sha
from packages.contracts import (
    BinaryIR, ConditionalIR, ConvertIR, HISTORICAL_PROFILE_ID, IfIR, LiteralIR,
    LoadIR, NAIR, OpenPineError, ReferenceIR, StoreIR, UnaryIR, HistoryIR, BlockIR, EvaluateIR, UserCallIR,
    InputIR,
)
from packages.execution.session import PROFILE_ID, Session
from packages.marketdata import read_bars
from packages.runtime.engine import program_hash

CASES = "conformance/literal-harness/cases.json"
REPORT = "conformance/results/literal-harness.1.json"
BAR_CASES = "conformance/historical-bars/cases.json"
BAR_REPORT = "conformance/results/historical-bars.1.json"
VERSION_CASES = "conformance/versions/cases.json"
VERSION_REPORT = "conformance/results/versions.1.json"
LANGUAGE_CASES = "conformance/language/cases.json"
LANGUAGE_REPORT = "conformance/results/language.1.json"
SERIES_CASES = "conformance/series/cases.json"
SERIES_REPORT = "conformance/results/series.1.json"
FUNCTION_CASES = "conformance/functions/cases.json"
FUNCTION_REPORT = "conformance/results/functions.1.json"
CONTEXT_CASES="conformance/contexts/cases.json"
CONTEXT_REPORT="conformance/results/contexts.1.json"
STRATEGY_CASES='conformance/strategies/cases.json'
STRATEGY_REPORT='conformance/results/strategies.1.json'
INPUT_CASES='conformance/inputs/cases.json'
INPUT_REPORT='conformance/results/inputs.1.json'


def context_inputs(path):
    from packages.contracts.contexts import MarketContext,ContextBinding
    from packages.execution.driver import MemorySource
    from packages.marketdata.providers import make_snapshot
    value=read_json(path);sources=[]
    for row in value["contexts"]:
        context=MarketContext.from_payload(row["context"])
        data=read_bars(safe_path(path.parent,row["bars"]))
        snapshot=make_snapshot(data,{"provider":context.source,"gap_policy":"preserve"})
        sources.append((ContextBinding(context,snapshot.sha256),MemorySource(data)))
    return {"primary_context":MarketContext.from_payload(value["primary_context"]),"context_sources":sources}


def operations(node):
    if isinstance(node,InputIR):return {'input.'+node.definition.input_type}
    if isinstance(node, EvaluateIR):
        return operations(node.expression)
    if isinstance(node, (BlockIR, UserCallIR)):
        features = {"function.call", "function.parameter"} if isinstance(node, UserCallIR) else {"block.value"}
        result = features | operations(node.result) | set().union(*(operations(s) for s in node.statements))
        if isinstance(node, UserCallIR):
            result |= set().union(*(operations(a.expression) for a in node.arguments))
        return result
    if isinstance(node, LiteralIR):
        return set()
    if isinstance(node, NAIR):
        return {"value.na"}
    if isinstance(node, LoadIR):
        return {"variable.load"}
    if isinstance(node, StoreIR):
        feature = ("variable.var" if node.binding.mode == "var" else "variable.declare") if node.declaration else "variable.assign"
        return {feature} | operations(node.expression)
    if isinstance(node, IfIR):
        return {"statement.if"} | operations(node.condition) | set().union(*(operations(child) for child in (*node.body, *node.otherwise)))
    if isinstance(node, BinaryIR):
        return {f"operator.binary.{node.operator}"} | operations(node.left) | operations(node.right)
    if isinstance(node, ConditionalIR):
        return {"operator.conditional"} | operations(node.condition) | operations(node.when_true) | operations(node.when_false)
    if isinstance(node, ReferenceIR):
        return {f"context.{node.contract.slot}"}
    if isinstance(node, HistoryIR):
        return {"series.history"} | operations(node.operand) | operations(node.offset)
    if isinstance(node, ConvertIR):
        return {f"conversion.{node.operand.value_type.base}.{node.value_type.base}"} | operations(node.operand)
    if isinstance(node, UnaryIR):
        return {f"operator.unary.{node.operator}"} | operations(node.operand)
    return {node.operation} | set().union(*(operations(argument.expression) for argument in node.arguments))


def run_cases(session=None, *, cases_path=CASES):
    path = ROOT / cases_path
    corpus = read_json(path)
    profile = corpus["implementation_profile_id"]
    if corpus["schema_version"] != "openpine.conformance.cases.1" or profile not in (PROFILE_ID, HISTORICAL_PROFILE_ID):
        raise ValueError("Unsupported conformance corpus")
    session = session if session is not None else Session(profile_id=profile)
    if session.profile_id != profile:
        raise ValueError("Session and corpus profiles differ")
    if corpus["oracle"]["kind"] != "independent_manual_expectations" or corpus["oracle"]["external_pine_execution"] is not False:
        raise ValueError("Corpus oracle attribution has changed")
    index(corpus["cases"], "case_id")
    assertions = index(session.snapshot.registry["assertions"], "assertion_id")
    for registry in session.snapshot.version_registries.values():
        assertions.update(index(registry["assertions"], "assertion_id"))
    rules = index(session.snapshot.registry["rules"], "rule_id")
    if not set(corpus["rule_refs"]) <= rules.keys() or not set(corpus["assertion_refs"]) <= assertions.keys():
        raise ValueError("Corpus refers to missing specification entries")
    results = []
    exercised = set()
    operations_by_version = {}
    for case in corpus["cases"]:
        source_path = safe_path(path.parent, case["source"])
        relative = source_path.relative_to(ROOT).as_posix()
        raw = source_path.read_bytes()
        ir_hash = None
        bars_path = safe_path(path.parent, case["bars"]) if "bars" in case else None
        data = read_bars(bars_path) if bars_path else None
        try:
            inputs=context_inputs(safe_path(path.parent,case["context_inputs"])) if "context_inputs" in case else {}
            if 'inputs' in case:inputs['inputs']=case['inputs']
            program, result = session.run(raw.decode("utf-8"), relative, data=data,**inputs)
            observed = {"status": "executed", "title": result.declaration.title,
                        "shorttitle": result.declaration.shorttitle, "overlay": result.declaration.overlay,
                        "outputs": [{"title": output.title, "type": output.value_type.base,
                                     "values": list(output.values)} for output in result.outputs]}
            if getattr(result,'simulation',None) is not None:
                observed.update(simulation_statistics=result.simulation['statistics'],
                    pending_entries=[o['intent']['entry_id'] for o in result.simulation['pending_orders']])
            if profile == HISTORICAL_PROFILE_ID:
                observed["observation_count"] = result.observation_count
                observed["qualifiers"] = [output.value_type.qualifier for output in result.outputs]
                if result.data is None or result.data.times != tuple(bar.time for bar in data.bars):
                    raise AssertionError("Historical result lacks its executed dataset identity")
            observed_version = result.identity.language_version
            ir_hash = program_hash(program)
            case_operations = set()
            for statement in program.statements:
                case_operations |= operations(statement)
            exercised |= case_operations
            operations_by_version.setdefault(str(observed_version), set()).update(case_operations)
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
                        "language_version": observed_version,
                        "semantic_profile_id": session.snapshot.semantic_identity(observed_version)[0],
                        "expected_sha256": sha(encode(case["expected"])), "observed": observed, "passed": True})
        if profile == HISTORICAL_PROFILE_ID:
            results[-1]["bars_sha256"] = sha(bars_path.read_bytes()) if bars_path else None
        if "context_inputs" in case:
            inputs_path=safe_path(path.parent,case["context_inputs"])
            results[-1]["context_inputs_sha256"]=sha(inputs_path.read_bytes())
            results[-1]["context_bars_sha256"]={row["bars"]:sha(safe_path(inputs_path.parent,row["bars"]).read_bytes())
                for row in read_json(inputs_path)["contexts"]}
        if 'inputs' in case:results[-1]['inputs_sha256']=sha(encode(case['inputs']))
    return {"schema_version": "openpine.conformance.results.1", "implementation_profile_id": profile,
            "implementation_sha256": session.implementation_sha256,
            "spec_lock_sha256": sha(encode(session.snapshot.lock)), "cases_sha256": sha(path.read_bytes()),
            "python": sys.version.split()[0], "oracle": corpus["oracle"], "results": results,
            "executed_programs": sum(row["observed"]["status"] == "executed" for row in results),
            "expected_rejections": sum(row["observed"]["status"] == "rejected" for row in results),
            "exercised_operations": sorted(exercised),
            "operations_by_version": {key: sorted(value) for key, value in sorted(operations_by_version.items())},
            "scope": ("Literal/call harness, one synthetic observation, no market data or complete Pine-version compatibility claim."
                      if profile == PROFILE_ID else "Artifact-defined version-specific statement and OHLCV/series profiles on confirmed historical bars.")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="record newly verified results")
    args = parser.parse_args(argv)
    for cases_path, report_path in ((CASES, REPORT), (BAR_CASES, BAR_REPORT), (VERSION_CASES, VERSION_REPORT), (LANGUAGE_CASES, LANGUAGE_REPORT), (SERIES_CASES, SERIES_REPORT), (FUNCTION_CASES, FUNCTION_REPORT),(CONTEXT_CASES,CONTEXT_REPORT),(STRATEGY_CASES,STRATEGY_REPORT),(INPUT_CASES,INPUT_REPORT)):
        report = run_cases(cases_path=cases_path)
        if cases_path in (VERSION_CASES, LANGUAGE_CASES, SERIES_CASES, FUNCTION_CASES,CONTEXT_CASES,STRATEGY_CASES,INPUT_CASES):
            matrix = {key: {"series.ema" if op == "series.ema.sma_seed" else op for op in values}
                      for key, values in report["operations_by_version"].items()}
            # Numeric condition conversion intentionally differs in v6.
            for values in matrix.values():
                values.difference_update({"conversion.int.bool", "conversion.float.bool"})
            if set(matrix) != {str(version) for version in range(1, 7)} or any(value != matrix["6"] for value in matrix.values()):
                raise SystemExit("Version matrix is incomplete: each version needs explicit cases for the declared feature families.")
        encoded = encode(report)
        path = ROOT / report_path
        if args.write_report:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
        elif not path.is_file() or path.read_bytes() != encoded:
            raise SystemExit(f"Conformance report {report_path} is missing/stale; review changes and run --write-report.")
        print(f"Conformance {report['implementation_profile_id']}: {report['executed_programs']} executed programs, "
              f"{report['expected_rejections']} expected rejections, Python {report['python']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
