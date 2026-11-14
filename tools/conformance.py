"""Run fixed source programs against independently authored expectations."""

import argparse
import math
import sys
from dataclasses import asdict

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
ALERT_CASES='conformance/alerts/cases.json'
ALERT_REPORT='conformance/results/alerts.json'
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


def observe_output(output):
    row = {'title':output.title, 'type':output.value_type.base, 'values':list(output.values)}
    if hasattr(output, 'colors'):
        row.update(colors=list(output.colors), linewidth=output.linewidth, style=output.style)
    if hasattr(output, 'textcolors'):
        row.update((field,getattr(output,field)) for field in ('kind','location','size','offset','text','char'))
        row['textcolors'] = list(output.textcolors)
    if hasattr(output, 'references'):
        row.update(kind=output.kind, references=list(output.references), fillgaps=output.fillgaps)
    if hasattr(output, 'bars'):
        row.update(kind=output.kind, bars=[asdict(bar) if bar is not None else None for bar in output.bars])
    if hasattr(output, 'gradients'):
        row.update(style=output.style,mask=output.mask,gradients=[asdict(sample) for sample in output.gradients])
    for field in ('offset','minheight','maxheight','max_abs','show_last','force_overlay','histbase','join','trackprice','linestyle','display'):
        if (value := getattr(output,field,None)) is not None:
            row[field] = list(value) if field=='display' and type(value) is tuple else value
    return row


def run_cases(session=None, *, cases_path=CASES, case_ids=None):
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
    cases = index(corpus["cases"], "case_id")
    if case_ids is not None:
        if not case_ids or not set(case_ids) <= cases.keys():
            raise ValueError("Smoke selection must name existing conformance cases")
        cases = {case_id: case for case_id, case in cases.items() if case_id in case_ids}
    assertions = index(session.snapshot.registry["assertions"], "assertion_id")
    for registry in session.snapshot.version_registries.values():
        assertions.update(index(registry["assertions"], "assertion_id"))
    rules = index(session.snapshot.registry["rules"], "rule_id")
    if not set(corpus["rule_refs"]) <= rules.keys() or not set(corpus["assertion_refs"]) <= assertions.keys():
        raise ValueError("Corpus refers to missing specification entries")
    results = []
    exercised = set()
    operations_by_version = {}
    for case in cases.values():
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
                        "outputs": [observe_output(output) for output in result.outputs]}
            from packages.contracts.alerts import definitions as alert_definitions
            if alerts := alert_definitions(program):
                observed['alert_conditions'] = [{k: v for k, v in item.items() if k != 'call_site_id'} for item in alerts]
            if hasattr(result.declaration, 'number_format'):
                observed['number_format'] = asdict(result.declaration.number_format)
            if hasattr(result.declaration, 'chart_scale'):
                observed['chart_scale'] = result.declaration.chart_scale
            if 'visual_order' in case['expected']:
                order = getattr(result.declaration, 'visual_order', None)
                observed['visual_order'] = asdict(order) if order is not None else None
            if hasattr(result, 'labels'):
                observed['labels'] = [asdict(label) for label in result.labels]
            if hasattr(result, 'lines'):
                observed['lines'] = [asdict(line) for line in result.lines]
            if hasattr(result, 'boxes'):
                observed['boxes'] = [asdict(box) for box in result.boxes]
            if hasattr(result, 'tables'):
                from packages.contracts.tables import table_payload
                observed['tables'] = [table_payload(table) for table in result.tables]
            if hasattr(result, 'linefills'):
                observed['linefills'] = [asdict(fill) for fill in result.linefills]
            if hasattr(result, 'polylines'):
                observed['polylines'] = [asdict(polyline) for polyline in result.polylines]
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
    from tools.window_indicators_fixtures import CASES as INDICATOR_CASES
    indicator_report = run_cases(cases_path=INDICATOR_CASES)
    indicator_path = ROOT / 'conformance/results/window-indicators.json'
    if args.write_report:
        indicator_path.write_bytes(encode(indicator_report))
    elif indicator_path.read_bytes() != encode(indicator_report):
        raise SystemExit('Window indicator conformance is stale.')
    from tools.channel_indicators_fixtures import CASES as CHANNEL_CASES
    channel_report = run_cases(cases_path=CHANNEL_CASES)
    channel_path = ROOT / 'conformance/results/channel-indicators.json'
    if args.write_report:
        channel_path.write_bytes(encode(channel_report))
    elif channel_path.read_bytes() != encode(channel_report):
        raise SystemExit('Channel indicator conformance is stale.')
    from tools.trend_indicators_fixtures import CASES as TREND_CASES
    trend_report = run_cases(cases_path=TREND_CASES)
    trend_path = ROOT / 'conformance/results/trend-indicators.json'
    if args.write_report:
        trend_path.write_bytes(encode(trend_report))
    elif trend_path.read_bytes() != encode(trend_report):
        raise SystemExit('Trend indicator conformance is stale.')
    from tools.momentum_fixtures import CASES as MOMENTUM_CASES
    momentum_report = run_cases(cases_path=MOMENTUM_CASES)
    momentum_path = ROOT / 'conformance/results/momentum.json'
    if args.write_report:
        momentum_path.write_bytes(encode(momentum_report))
    elif momentum_path.read_bytes() != encode(momentum_report):
        raise SystemExit('Momentum conformance is stale.')
    from tools.volume_indicators_fixtures import CASES as VOLUME_CASES
    volume_report = run_cases(cases_path=VOLUME_CASES)
    volume_path = ROOT / 'conformance/results/volume-indicators.json'
    if args.write_report:
        volume_path.write_bytes(encode(volume_report))
    elif volume_path.read_bytes() != encode(volume_report):
        raise SystemExit('Volume indicator conformance is stale.')
    from tools.window_statistics_fixtures import CASES as STATISTICS_CASES
    statistics_report = run_cases(cases_path=STATISTICS_CASES)
    statistics_path = ROOT / 'conformance/results/window-statistics.json'
    if args.write_report:
        statistics_path.write_bytes(encode(statistics_report))
    elif statistics_path.read_bytes() != encode(statistics_report):
        raise SystemExit('Window statistics conformance is stale.')
    from tools.pivots_fixtures import CASES as PIVOT_CASES
    pivot_report = run_cases(cases_path=PIVOT_CASES)
    pivot_path = ROOT / 'conformance/results/pivots.json'
    if args.write_report:
        pivot_path.write_bytes(encode(pivot_report))
    elif pivot_path.read_bytes() != encode(pivot_report):
        raise SystemExit('Pivot conformance is stale.')
    from tools.pivot_levels_fixtures import CASES as LEVEL_CASES
    levels_report = run_cases(cases_path=LEVEL_CASES)
    levels_path = ROOT / 'conformance/results/pivot-levels.json'
    if args.write_report:
        levels_path.write_bytes(encode(levels_report))
    elif levels_path.read_bytes() != encode(levels_report):
        raise SystemExit('Pivot levels conformance is stale.')
    from tools.correlation_fixtures import CASES as CORRELATION_CASES
    correlation_report = run_cases(cases_path=CORRELATION_CASES)
    correlation_path = ROOT / 'conformance/results/correlation.json'
    if args.write_report:
        correlation_path.write_bytes(encode(correlation_report))
    elif correlation_path.read_bytes() != encode(correlation_report):
        raise SystemExit('Correlation conformance is stale.')
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
    array_ops_report=run_cases(cases_path='conformance/array-operations/cases.json')
    array_ops_path=ROOT/'conformance/results/array-operations.json'
    if args.write_report:array_ops_path.write_bytes(encode(array_ops_report))
    elif array_ops_path.read_bytes()!=encode(array_ops_report):raise SystemExit('Array operations conformance is stale.')
    order_report=run_cases(cases_path='conformance/array-order/cases.json')
    order_path=ROOT/'conformance/results/array-order.json'
    if args.write_report:order_path.write_bytes(encode(order_report))
    elif order_path.read_bytes()!=encode(order_report):raise SystemExit('Array order conformance is stale.')
    slice_report=run_cases(cases_path='conformance/array-slice/cases.json')
    matrices_report=run_cases(cases_path='conformance/matrices/cases.json')
    matrices_path=ROOT/'conformance/results/matrices.json'
    if args.write_report:matrices_path.write_bytes(encode(matrices_report))
    elif matrices_path.read_bytes()!=encode(matrices_report):raise SystemExit('Matrices conformance is stale.')
    ohlc_report=run_cases(cases_path='conformance/visual-ohlc/cases.json')
    ohlc_path=ROOT/'conformance/results/visual-ohlc.json'
    if args.write_report:ohlc_path.write_bytes(encode(ohlc_report))
    elif ohlc_path.read_bytes()!=encode(ohlc_report):raise SystemExit('Visual OHLC conformance is stale.')
    markers_report=run_cases(cases_path='conformance/visual-signals/cases.json')
    markers_path=ROOT/'conformance/results/visual-signals.json'
    if args.write_report:markers_path.write_bytes(encode(markers_report))
    elif markers_path.read_bytes()!=encode(markers_report):raise SystemExit('Visual signals conformance is stale.')
    labels_report = run_cases(cases_path='conformance/labels/cases.json')
    labels_path = ROOT / 'conformance/results/labels.json'
    if args.write_report:
        labels_path.write_bytes(encode(labels_report))
    elif labels_path.read_bytes() != encode(labels_report):
        raise SystemExit('Label conformance is stale.')
    lines_report = run_cases(cases_path='conformance/lines/cases.json')
    lines_path = ROOT / 'conformance/results/lines.json'
    if args.write_report:
        lines_path.write_bytes(encode(lines_report))
    elif lines_path.read_bytes() != encode(lines_report):
        raise SystemExit('Line conformance is stale.')
    extension_report = run_cases(cases_path='conformance/line-extensions/cases.json')
    extension_path = ROOT / 'conformance/results/line-extensions.json'
    if args.write_report:
        extension_path.write_bytes(encode(extension_report))
    elif extension_path.read_bytes() != encode(extension_report):
        raise SystemExit('Line extension conformance is stale.')
    box_report = run_cases(cases_path='conformance/boxes/cases.json')
    box_path = ROOT / 'conformance/results/boxes.json'
    if args.write_report:
        box_path.write_bytes(encode(box_report))
    elif box_path.read_bytes() != encode(box_report):
        raise SystemExit('Box conformance is stale.')
    table_report = run_cases(cases_path='conformance/tables/cases.json')
    table_path = ROOT / 'conformance/results/tables.json'
    if args.write_report:
        table_path.write_bytes(encode(table_report))
    elif table_path.read_bytes() != encode(table_report):
        raise SystemExit('Table conformance is stale.')
    linefill_report = run_cases(cases_path='conformance/linefills/cases.json')
    linefill_path = ROOT / 'conformance/results/linefills.json'
    if args.write_report:
        linefill_path.write_bytes(encode(linefill_report))
    elif linefill_path.read_bytes() != encode(linefill_report):
        raise SystemExit('Linefill conformance is stale.')
    xloc_report = run_cases(cases_path='conformance/drawing-xloc/cases.json')
    xloc_path = ROOT / 'conformance/results/drawing-xloc.json'
    if args.write_report:
        xloc_path.write_bytes(encode(xloc_report))
    elif xloc_path.read_bytes() != encode(xloc_report):
        raise SystemExit('Drawing coordinate conformance is stale.')
    xloc_mutations_report = run_cases(cases_path='conformance/drawing-xloc-mutations/cases.json')
    xloc_mutations_path = ROOT / 'conformance/results/drawing-xloc-mutations.json'
    if args.write_report:
        xloc_mutations_path.write_bytes(encode(xloc_mutations_report))
    elif xloc_mutations_path.read_bytes() != encode(xloc_mutations_report):
        raise SystemExit('Drawing coordinate mutation and read conformance is stale.')
    line_styles_report = run_cases(cases_path='conformance/line-styles/cases.json')
    line_styles_path = ROOT / 'conformance/results/line-styles.json'
    if args.write_report:
        line_styles_path.write_bytes(encode(line_styles_report))
    elif line_styles_path.read_bytes() != encode(line_styles_report):
        raise SystemExit('Line styles conformance is stale.')
    box_borders_report = run_cases(cases_path='conformance/box-borders/cases.json')
    box_borders_path = ROOT / 'conformance/results/box-borders.json'
    if args.write_report:
        box_borders_path.write_bytes(encode(box_borders_report))
    elif box_borders_path.read_bytes() != encode(box_borders_report):
        raise SystemExit('Box border conformance is stale.')
    box_text_report = run_cases(cases_path='conformance/box-text/cases.json')
    box_text_path = ROOT / 'conformance/results/box-text.json'
    if args.write_report:
        box_text_path.write_bytes(encode(box_text_report))
    elif box_text_path.read_bytes() != encode(box_text_report):
        raise SystemExit('Box text report differs.')
    label_styles_report = run_cases(cases_path='conformance/label-styles/cases.json')
    label_styles_path = ROOT / 'conformance/results/label-styles.json'
    if args.write_report:
        label_styles_path.write_bytes(encode(label_styles_report))
    elif label_styles_path.read_bytes() != encode(label_styles_report):
        raise SystemExit('Label appearance conformance is stale.')
    label_yloc_report = run_cases(cases_path='conformance/label-yloc/cases.json')
    label_yloc_path = ROOT / 'conformance/results/label-yloc.json'
    if args.write_report:
        label_yloc_path.write_bytes(encode(label_yloc_report))
    elif label_yloc_path.read_bytes() != encode(label_yloc_report):
        raise SystemExit('Label vertical-coordinate conformance is stale.')
    label_text_report = run_cases(cases_path='conformance/label-text/cases.json')
    label_text_path = ROOT / 'conformance/results/label-text.json'
    if args.write_report:
        label_text_path.write_bytes(encode(label_text_report))
    elif label_text_path.read_bytes() != encode(label_text_report):
        raise SystemExit('Label text conformance is stale.')
    points_report = run_cases(cases_path='conformance/chart-points/cases.json')
    points_path = ROOT / 'conformance/results/chart-points.json'
    if args.write_report:
        points_path.write_bytes(encode(points_report))
    elif points_path.read_bytes() != encode(points_report):
        raise SystemExit('Chart point conformance is stale.')
    polylines_report = run_cases(cases_path='conformance/polylines/cases.json')
    polylines_path = ROOT / 'conformance/results/polylines.json'
    if args.write_report:
        polylines_path.write_bytes(encode(polylines_report))
    elif polylines_path.read_bytes() != encode(polylines_report):
        raise SystemExit('Polyline conformance is stale.')
    bands_report=run_cases(cases_path='conformance/visual-bands/cases.json')
    bands_path=ROOT/'conformance/results/visual-bands.json'
    if args.write_report:bands_path.write_bytes(encode(bands_report))
    elif bands_path.read_bytes()!=encode(bands_report):raise SystemExit('Visual bands conformance is stale.')
    colors_report=run_cases(cases_path='conformance/colors/cases.json')
    colors_path=ROOT/'conformance/results/colors.json'
    if args.write_report:colors_path.write_bytes(encode(colors_report))
    elif colors_path.read_bytes()!=encode(colors_report):raise SystemExit('Color conformance is stale.')
    products_report=run_cases(cases_path='conformance/product-results/cases.json')
    products_path=ROOT/'conformance/results/product-results.json'
    if args.write_report:products_path.write_bytes(encode(products_report))
    elif products_path.read_bytes()!=encode(products_report):raise SystemExit('Product results conformance is stale.')
    graphs_report=run_cases(cases_path='conformance/object-graphs/cases.json')
    graphs_path=ROOT/'conformance/results/object-graphs.json'
    if args.write_report:graphs_path.write_bytes(encode(graphs_report))
    elif graphs_path.read_bytes()!=encode(graphs_report):raise SystemExit('Object graphs conformance is stale.')
    maps_report=run_cases(cases_path='conformance/maps/cases.json')
    maps_path=ROOT/'conformance/results/maps.json'
    if args.write_report:maps_path.write_bytes(encode(maps_report))
    elif maps_path.read_bytes()!=encode(maps_report):raise SystemExit('Maps conformance is stale.')
    slice_path=ROOT/'conformance/results/array-slice.json'
    if args.write_report:slice_path.write_bytes(encode(slice_report))
    elif slice_path.read_bytes()!=encode(slice_report):raise SystemExit('Array slice conformance is stale.')
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
    for cases_path, report_path in ((CASES, REPORT), (BAR_CASES, BAR_REPORT), (VERSION_CASES, VERSION_REPORT), (LANGUAGE_CASES, LANGUAGE_REPORT), (SERIES_CASES, SERIES_REPORT), (FUNCTION_CASES, FUNCTION_REPORT),(CONTEXT_CASES,CONTEXT_REPORT),(STRATEGY_CASES,STRATEGY_REPORT),(INPUT_CASES,INPUT_REPORT),(EXIT_CASES,EXIT_REPORT),(TECHNICAL_CASES,TECHNICAL_REPORT),(COMPOSITE_CASES,COMPOSITE_REPORT),(LOOP_CASES,LOOP_REPORT),(ARRAY_CASES,ARRAY_REPORT),(ALERT_CASES,ALERT_REPORT),(ENVIRONMENT_CASES,ENVIRONMENT_REPORT),(SIGNAL_CASES,SIGNAL_REPORT),(CONTEXT_GRAPH_CASES,CONTEXT_GRAPH_REPORT),(PARTIAL_CASES,PARTIAL_REPORT),(PENDING_CASES,PENDING_REPORT),(LOT_CASES,LOT_REPORT),(GROUP_CASES,GROUP_REPORT),(TRAILING_CASES,TRAILING_REPORT),(ECONOMIC_CASES,ECONOMIC_REPORT),(RISK_CASES,RISK_REPORT),(PERFORMANCE_CASES,PERFORMANCE_REPORT),('conformance/lower-tf/cases.json','conformance/results/lower-tf.json')):
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
