"""Run fixed source programs against independently authored expectations."""

import argparse
import math
import sys

from packages.compiler.semantic.specification import ROOT, encode, index, read_json, safe_path, sha
from packages.contracts import (
    BinaryIR, ConditionalIR, ConvertIR, HISTORICAL_PROFILE_ID, IfIR, LiteralIR,
    LoadIR, NAIR, OpenPineError, ReferenceIR, StoreIR, UnaryIR, HistoryIR, BlockIR, EvaluateIR, UserCallIR,
    InputIR,TupleIR,UnpackIR,ForIR,WhileIR,LoopControlIR,ArrayIR,ForInIR,
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
EXIT_CASES='conformance/exits/cases.json'
EXIT_REPORT='conformance/results/exits.1.json'
TECHNICAL_CASES='conformance/technical/cases.json'
TECHNICAL_REPORT='conformance/results/technical.1.json'
COMPOSITE_CASES='conformance/composite/cases.json'
COMPOSITE_REPORT='conformance/results/composite.1.json'
LOOP_CASES='conformance/loops/cases.json'
LOOP_REPORT='conformance/results/loops.1.json'
ARRAY_CASES='conformance/arrays/cases.json'
ARRAY_REPORT='conformance/results/arrays.1.json'


def observation_matches(observed, expected, tolerance=None):
    if tolerance is None:return encode(observed)==encode(expected)
    if type(tolerance) is not dict or set(tolerance)!={'absolute','relative'} or any(
            type(v) not in (int,float) or not 0<=v<=1e-12 for v in tolerance.values()):
        raise ValueError('Conformance tolerance must be explicitly bounded to 1e-12.')
    def equal(actual, reference, in_values=False):
        if in_values and type(reference) is float:
            return type(actual) is float and math.isfinite(actual) and math.isclose(actual,reference,
                rel_tol=tolerance['relative'],abs_tol=tolerance['absolute'])
        if type(actual) is not type(reference):return False
        if type(reference) is dict:
            return actual.keys()==reference.keys() and all(equal(actual[k],reference[k],k=='values') for k in reference)
        if type(reference) is list:
            return len(actual)==len(reference) and all(equal(a,b,in_values) for a,b in zip(actual,reference,strict=True))
        return actual==reference
    return equal(observed,expected)


SIGNAL_CASES='conformance/signals/cases.json'
SIGNAL_REPORT='conformance/results/signals.json'
CONTEXT_GRAPH_CASES='conformance/context-graph/cases.json'
CONTEXT_GRAPH_REPORT='conformance/results/context-graph.json'
PARTIAL_CASES='conformance/partial-exits/cases.json'
PARTIAL_REPORT='conformance/results/partial-exits.json'
PENDING_CASES='conformance/pending-entries/cases.json'
PENDING_REPORT='conformance/results/pending-entries.json'
LOT_CASES='conformance/lots/cases.json'
LOT_REPORT='conformance/results/lots.json'
GROUP_CASES='conformance/order-groups/cases.json'
GROUP_REPORT='conformance/results/order-groups.json'
TRAILING_CASES='conformance/trailing/cases.json'
TRAILING_REPORT='conformance/results/trailing.json'
ECONOMIC_CASES='conformance/economics/cases.json'
ECONOMIC_REPORT='conformance/results/economics.json'
ENVIRONMENT_CASES='conformance/environment/cases.json'
ENVIRONMENT_REPORT='conformance/results/environment.json'


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


from apps.openpine.capability_operations import operations


def run_cases(session=None, *, cases_path=CASES):
    path = ROOT / cases_path
    corpus = read_json(path)
    profile = corpus["implementation_profile_id"]
    if corpus["schema_version"] != "openpine.conformance.cases.1" or profile not in (PROFILE_ID, HISTORICAL_PROFILE_ID):
        raise ValueError("Unsupported conformance corpus")
    from packages.storage.trade_index import TradeIndex
    session = session if session is not None else Session(profile_id=profile,trade_history_factory=TradeIndex)
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
            if 'broker_settings' in case:
                from packages.contracts.trading import BrokerSettings
                inputs['broker_settings']=BrokerSettings.from_payload(case['broker_settings'])
            program, result = session.run(raw.decode("utf-8"), relative, data=data,**inputs)
            observed = {"status": "executed", "title": result.declaration.title,
                        "shorttitle": result.declaration.shorttitle, "overlay": result.declaration.overlay,
                        "outputs": [{"title": output.title, "type": output.value_type.base,
                                     "values": list(output.values)} for output in result.outputs]}
            if getattr(result,'simulation',None) is not None:
                statistics=result.simulation['statistics']
                if 'order_groups' in statistics:
                    members={o['order_id']:{'entry_id':o['intent']['entry_id'],'exit_id':o['intent'].get('exit_id','')} for o in (*statistics['entries'],*statistics['protections'])}
                    statistics={**statistics,'order_groups':[{'name':g['name'],'type':g['type'],'members':[{**{k:v for k,v in m.items() if k!='order_id'},**members[m['order_id']]} for m in g['members']]} for g in statistics['order_groups']]}
                if 'lots' in statistics:
                    lots={p['fill_id']:p for p in statistics['lots']};entries={o['order_id']:o for o in statistics['entries']}
                    def target(t):
                        p=lots[t['target_id']] if t['kind']=='lot' else entries[t['target_id']]
                        return {'kind':t['kind'],'entry_id':p['entry_id'] if t['kind']=='lot' else p['intent']['entry_id'],
                            'entry_bar' if t['kind']=='lot' else 'created_bar':p['bar_index'] if t['kind']=='lot' else p['created_bar'],'quantity':t['quantity']}
                    statistics={**statistics,'lots':[{k:p[k] for k in ('entry_id','direction','quantity','price','commission','bar_index','original_quantity','original_commission','closed_quantity','allocated_commission','closed_slices')} for p in statistics['lots']],
                        'protections':[{**{k:o[k] for k in ('exit_id','from_entry','stop','limit','quantity','created_bar','eligible_bar','direction')},'targets':[target(t) for t in o['targets']]} for o in statistics['protections']]}
                    if 'exit_states' in statistics:
                        names=('entry_price','stop','limit','activation','activated_bar','extreme','trail_stop','stop_eligible_bar','previous_trail_stop','previous_stop_eligible_bar','effective_stop','effective_role','status','extreme_bar','extreme_basis')
                        statistics={**statistics,'exit_states':[{'exit_id':o['exit_id'],'from_entry':o['from_entry'],'targets':[{**target(t),**{n:t[n] for n in names}} for t in o['targets']]} for o in statistics['exit_states']]}
                if 'entries' in statistics:
                    # Execution hashes/call-site IDs are tested by checkpoint/ABI
                    # suites. The independent corpus compares pending semantics.
                    statistics={**statistics,'entries':[{**{k:o['intent'][k] for k in ('entry_id','direction','quantity','limit','stop')},
                        **{k:o[k] for k in ('order_kind','status','created_bar','activated_bar','activation_basis')}} for o in statistics['entries']]}
                if 'risk' in statistics and statistics['risk']['halt'] is not None:
                    statistics={**statistics,'risk':{**statistics['risk'],'halt':{k:v for k,v in statistics['risk']['halt'].items() if k!='call_site_id'}}}
                if 'performance' in statistics:
                    statistics={**statistics,'performance':{**statistics['performance'],'archive':{'count':statistics['performance']['archive']['count']}}}
                observed.update(simulation_statistics=statistics,
                    pending_entries=[o['intent']['entry_id'] for o in (*result.simulation['pending_orders'],*result.simulation.get('pending_entries',[]))])
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
        if not observation_matches(observed,case["expected"],case.get('numeric_tolerance')):
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
        if 'broker_settings' in case:results[-1]['broker_settings_sha256']=sha(encode(case['broker_settings']))
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


RISK_CASES='conformance/risk/cases.json'
RISK_REPORT='conformance/results/risk.json'
PERFORMANCE_CASES='conformance/performance/cases.json'
PERFORMANCE_REPORT='conformance/results/performance.json'

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="record newly verified results")
    args = parser.parse_args(argv)
    core_report=run_cases(cases_path='conformance/semantic-core/cases.json')
    core_path=ROOT/'conformance/results/semantic-core.json'
    if args.write_report:core_path.write_bytes(encode(core_report))
    elif core_path.read_bytes()!=encode(core_report):raise SystemExit('Semantic-core conformance is stale.')
    signal_window_report=run_cases(cases_path='conformance/dynamic-signals/cases.json')
    if args.write_report:(ROOT/'conformance/results/dynamic-signals.json').write_bytes(encode(signal_window_report))
    elif (ROOT/'conformance/results/dynamic-signals.json').exists():assert read_json(ROOT/'conformance/results/dynamic-signals.json')==signal_window_report
    window_report=run_cases(cases_path='conformance/dynamic-windows/cases.json')
    if args.write_report:(ROOT/'conformance/results/dynamic-windows.json').write_bytes(encode(window_report))
    elif (ROOT/'conformance/results/dynamic-windows.json').exists():assert read_json(ROOT/'conformance/results/dynamic-windows.json')==window_report
    numeric_report=run_cases(cases_path='conformance/numeric-targets/cases.json')
    if args.write_report:(ROOT/'conformance/results/numeric-targets.json').write_bytes(encode(numeric_report))
    elif (ROOT/'conformance/results/numeric-targets.json').read_bytes()!=encode(numeric_report):raise SystemExit('Numeric-target conformance is stale.')
    scalar_report=run_cases(cases_path='conformance/scalar-targets/cases.json')
    if args.write_report:(ROOT/'conformance/results/scalar-targets.json').write_bytes(encode(scalar_report))
    elif (ROOT/'conformance/results/scalar-targets.json').read_bytes()!=encode(scalar_report):raise SystemExit('Scalar-target conformance is stale.')
    function_report=run_cases(cases_path='conformance/function-targets/cases.json')
    if args.write_report:(ROOT/'conformance/results/function-targets.json').write_bytes(encode(function_report))
    elif (ROOT/'conformance/results/function-targets.json').read_bytes()!=encode(function_report):raise SystemExit('Function-target conformance is stale.')
    primary_report=run_cases(cases_path='conformance/primary-targets/cases.json')
    if args.write_report:(ROOT/'conformance/results/primary-targets.json').write_bytes(encode(primary_report))
    elif (ROOT/'conformance/results/primary-targets.json').read_bytes()!=encode(primary_report):raise SystemExit('Primary-target conformance is stale.')
    computed_report=run_cases(cases_path='conformance/computed-targets/cases.json')
    if args.write_report:(ROOT/'conformance/results/computed-targets.json').write_bytes(encode(computed_report))
    else:
        if (ROOT/'conformance/results/computed-targets.json').read_bytes()!=encode(computed_report):raise SystemExit('Computed-target conformance is stale.')
    methods_report=run_cases(cases_path='conformance/methods/cases.json')
    methods_path=ROOT/'conformance/results/methods.json'
    if args.write_report:methods_path.write_bytes(encode(methods_report))
    elif methods_path.read_bytes()!=encode(methods_report):raise SystemExit('Method conformance is stale.')
    objects_report=run_cases(cases_path='conformance/objects/cases.json')
    objects_path=ROOT/'conformance/results/objects.json'
    if args.write_report:objects_path.write_bytes(encode(objects_report))
    elif objects_path.read_bytes()!=encode(objects_report):raise SystemExit('Object conformance is stale.')
    object_arrays_report=run_cases(cases_path='conformance/object-arrays/cases.json')
    object_arrays_path=ROOT/'conformance/results/object-arrays.json'
    if args.write_report:object_arrays_path.write_bytes(encode(object_arrays_report))
    elif object_arrays_path.read_bytes()!=encode(object_arrays_report):raise SystemExit('Object-array conformance is stale.')
    enums_report=run_cases(cases_path='conformance/enums/cases.json')
    enums_path=ROOT/'conformance/results/enums.json'
    if args.write_report:enums_path.write_bytes(encode(enums_report))
    elif enums_path.read_bytes()!=encode(enums_report):raise SystemExit('Enum conformance is stale.')
    overload_report=run_cases(cases_path='conformance/function-overloads/cases.json')
    overload_path=ROOT/'conformance/results/function-overloads.json'
    if args.write_report:overload_path.write_bytes(encode(overload_report))
    elif overload_path.read_bytes()!=encode(overload_report):raise SystemExit('Function-overload conformance is stale.')
    env_report=run_cases(cases_path='conformance/request-environment/cases.json')
    env_path=ROOT/'conformance/results/request-environment.json'
    if args.write_report:env_path.write_bytes(encode(env_report))
    elif env_path.read_bytes()!=encode(env_report):raise SystemExit('Requested-environment conformance is stale.')
    prepared_report=run_cases(cases_path='conformance/prepared-requests/cases.json')
    print(f"Prepared contexts: {prepared_report['executed_programs']} executed, {prepared_report['expected_rejections']} expected rejections.",flush=True)
    if args.write_report:
        (ROOT/'conformance/results/prepared-requests.json').write_bytes(encode(prepared_report))
    for cases_path, report_path in ((CASES, REPORT), (BAR_CASES, BAR_REPORT), (VERSION_CASES, VERSION_REPORT), (LANGUAGE_CASES, LANGUAGE_REPORT), (SERIES_CASES, SERIES_REPORT), (FUNCTION_CASES, FUNCTION_REPORT),(CONTEXT_CASES,CONTEXT_REPORT),(STRATEGY_CASES,STRATEGY_REPORT),(INPUT_CASES,INPUT_REPORT),(EXIT_CASES,EXIT_REPORT),(TECHNICAL_CASES,TECHNICAL_REPORT),(COMPOSITE_CASES,COMPOSITE_REPORT),(LOOP_CASES,LOOP_REPORT),(ARRAY_CASES,ARRAY_REPORT),(ENVIRONMENT_CASES,ENVIRONMENT_REPORT),(SIGNAL_CASES,SIGNAL_REPORT),(CONTEXT_GRAPH_CASES,CONTEXT_GRAPH_REPORT),(PARTIAL_CASES,PARTIAL_REPORT),(PENDING_CASES,PENDING_REPORT),(LOT_CASES,LOT_REPORT),(GROUP_CASES,GROUP_REPORT),(TRAILING_CASES,TRAILING_REPORT),(ECONOMIC_CASES,ECONOMIC_REPORT),(RISK_CASES,RISK_REPORT),(PERFORMANCE_CASES,PERFORMANCE_REPORT),('conformance/lower-tf/cases.json','conformance/results/lower-tf.json')):
        report = run_cases(cases_path=cases_path)
        if cases_path==RISK_CASES:
            from packages.contracts.risk import OPERATIONS
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any(not set(OPERATIONS)<=set(report['operations_by_version'][str(v)]) for v in range(1,7)):raise SystemExit('Risk declarations require explicit all-version execution.')
        if cases_path==ECONOMIC_CASES:
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any('simulation.strategy_economics' not in report['operations_by_version'][str(v)] for v in range(1,7)):raise SystemExit('Economic modes require explicit all-version execution.')
        if cases_path==TRAILING_CASES:
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any('simulation.exit_extended' not in report['operations_by_version'][str(v)] for v in range(1,7)):
                raise SystemExit('Trailing requires explicit execution in every version.')
        if cases_path==GROUP_CASES:
            from packages.contracts.order_groups import OPERATIONS
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any(not set(OPERATIONS)<=set(report['operations_by_version'][str(v)]) for v in range(1,7)):
                raise SystemExit('Grouped order families require explicit execution in every version.')
        if cases_path==LOT_CASES:
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any(not {'simulation.strategy_lots','simulation.entry_pending','simulation.close_partial','simulation.exit_partial','simulation.cancel'}<=set(report['operations_by_version'][str(v)]) for v in range(1,7)):
                raise SystemExit('Lot families require explicit execution in every version.')
        if cases_path==PENDING_CASES:
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any(not {'simulation.entry_pending','simulation.exit_partial','simulation.close_partial'}<=set(report['operations_by_version'][str(v)]) for v in range(1,7)):
                raise SystemExit('Pending entry/partial exit families need explicit execution in all six versions.')
        if cases_path == SIGNAL_CASES:
            from packages.contracts.signals import OPERATIONS
            if set(report['operations_by_version'])!={str(v) for v in range(1,7)} or any(not OPERATIONS<=set(report['operations_by_version'][str(v)]) for v in range(1,7)):
                raise SystemExit('Signal function families need explicit coverage in every version.')
        if cases_path == ARRAY_CASES:
            for version in range(1,7):
                features=set(report['operations_by_version'].get(str(version),[]))
                required={'array.'+op for op in ('new','from','size','get','set','push','pop','shift','unshift','clear','copy','sum','avg','min','max','is_na')} if version>=4 else set()
                if version>=5:required.add('loop.for_in')
                if not required<=features or version<4 and any(f.startswith('array.') for f in features):
                    raise SystemExit('Array version coverage differs from documented availability.')
        if cases_path == LOOP_CASES:
            for version in range(1,7):
                features=set(report['operations_by_version'].get(str(version), []))
                required={'loop.for','loop.break','loop.continue','loop.value'} if version>=2 else set()
                if version>=5:required.add('loop.while')
                if not required<=features or version==1 and any(f.startswith('loop.') for f in features):
                    raise SystemExit('Loop version coverage differs from documented availability.')
        if cases_path in (VERSION_CASES, LANGUAGE_CASES, SERIES_CASES, FUNCTION_CASES,CONTEXT_CASES,STRATEGY_CASES,INPUT_CASES,EXIT_CASES,TECHNICAL_CASES,COMPOSITE_CASES):
            matrix = {key: {"series.ema" if op == "series.ema.sma_seed" else op.removesuffix('.population') for op in values}
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
