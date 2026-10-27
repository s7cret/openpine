"""Report admitted offline APIs and linked fixtures without claiming test results.

This is an implementation inventory, not a TradingView compatibility score.
The separate spec/coverage.json counts imported reference material.
"""
from dataclasses import asdict
import argparse
import json

from packages.compiler.semantic.specification import ROOT, read_json
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.strategy_cycle import policy as cycle_policy
from packages.execution.session import Session


def report():
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    corpora = []
    for path in sorted((ROOT/'conformance').glob('*/cases.json')):
        value = read_json(path)
        if value.get('schema_version') == 'openpine.conformance.cases.1':
            corpora.append((path.relative_to(ROOT).as_posix(), value['cases']))
    versions = []
    for version in range(1, 7):
        catalog = session.catalog_for(version)
        profile = session.snapshot.profile(version)
        apis = []
        for name, overloads in sorted(catalog.signatures.items()):
            for signature, contract in overloads:
                if contract is None:
                    continue
                apis.append({'name': name, 'operation': contract.operation,
                    'overload_id': contract.overload_id,
                    'parameters': [parameter['name'] for parameter in signature['parameters']],
                    'named': signature['binding']['named'],
                    'qualifier_limits': list(contract.qualifier_limits),
                    'result_type':asdict(contract.result_type), 'result_rule':contract.result_rule, 'result_parameter':contract.result_parameter, 'effects': list(contract.effects)})
                if contract.operation in ('context.request','context.lower_tf'):
                    prepared=catalog.contracts[contract.overload_id+'.prepared']
                    apis.append({**apis[-1],'overload_id':prepared.overload_id,'qualifier_limits':list(prepared.qualifier_limits),
                        'activation':'frozen global input/literal target dependencies before execution','target_profile':'openpine.prepared-requests.1'})
        extra=[*catalog.partial_exits.items(),*catalog.pending_entries.items(),*({'strategy':catalog.lot_header}.items() if catalog.lot_header else ()),
            *((name,row) for name,row in catalog.order_groups.items() if name in ('strategy.entry','strategy.exit')),*catalog.trailing.items(),*({'strategy':catalog.economics_header}.items() if catalog.economics_header else ()),*({'strategy':catalog.intrabar_header}.items() if catalog.intrabar_header else ()),*({'strategy':catalog.cycle_header}.items() if catalog.cycle_header and version>=2 else ())]
        for name,(signature,contract) in sorted(extra,key=lambda row:(row[0],row[1][1].overload_id)):
            apis.append({'name':name,'operation':contract.operation,'overload_id':contract.overload_id,
                'activation':'explicit calc_on_order_fills or process_orders_on_close with version gates' if contract.operation=='simulation.strategy_cycle' else 'explicit calc_on_every_tick parameter' if contract.operation=='simulation.strategy_intrabar' else 'explicit cash/percent sizing or cash fee mode' if contract.operation=='simulation.strategy_economics' else 'explicit OCA arguments' if contract.operation in ('simulation.entry_group','simulation.exit_group') else 'positive literal pyramiding or explicit close_entries_rule' if contract.operation=='simulation.strategy_lots' else 'explicit named limit/stop' if contract.operation=='simulation.entry_pending' else 'explicit named qty/qty_percent','parameters':[p['name'] for p in signature['parameters']],
                **({'source_flag_availability':cycle_policy(version)['source_flags']} if contract.operation=='simulation.strategy_cycle' else {}),
                'named':True,'qualifier_limits':list(contract.qualifier_limits),'result_type':asdict(contract.result_type),
                'result_rule':contract.result_rule,'result_parameter':contract.result_parameter,'effects':list(contract.effects)})
        fixtures = [{'path': path, 'case_ids': [case['case_id'] for case in cases
            if case['language_version'] == version]} for path, cases in corpora]
        fixtures = [row for row in fixtures if row['case_ids']]
        versions.append({'language_version': version,
            'array_intrinsics':session.snapshot.arrays['versions'][str(version)]['apis'],
            'profile_ids': {key: value for key, value in profile.items() if key.endswith('_profile_ids')},
            'apis': apis, 'references': sorted(catalog.references),
            'computed_references': [{'name':name.removeprefix('reference:'),
                'operation':item[1].operation, 'overload_id':item[1].overload_id}
                for name,item in sorted(catalog.technical.items()) if name.startswith('reference:')],
            'fixtures': fixtures,
            'compatibility_notes': list(session.snapshot.semantic_identity(version)[2])})
    return {'schema_version': 'openpine.capabilities.1',
        'meaning': 'Admitted bounded APIs and fixture inventory; neither execution proof nor full Pine compatibility.',
        'implementation_profile_id': HISTORICAL_PROFILE_ID,
        'spec_inventory': 'spec/coverage.json', 'execution_evidence': False,
        'strategy_cycle':{'profile_id':'openpine.strategy-cycle.1','language_versions':list(range(1,7)),'fill_header_versions':list(range(2,7)),'close_header_versions':[4,5,6],'specification':'spec/strategy-cycle-profile.json','documentation':'docs/STRATEGY_CYCLE.md'},
        'observed_calculation':{'profile_id':'openpine.observed-calculation.1','language_versions':list(range(1,7)),
            'source_header_versions':list(range(2,7)),'varip_versions':[4,5,6],'specification':'spec/intrabar-profile.json','documentation':'docs/INTRABAR.md'},
        'observed_broker':{'profile_id':'openpine.observed-broker.1','language_versions':list(range(1,7)),
            'opt_in':True,'specification':'spec/observed-broker-profile.json','documentation':'docs/OBSERVED_TRADING.md',
            'price_source':'received_sample_close_without_high_low_tick_reconstruction','script_calculation':'confirmed_primary_close'},
        'prepared_requests':{'profile_id':'openpine.prepared-requests.1','language_versions':list(range(1,7)),'lower_tf_versions':[5,6],
            'specification':'spec/prepared-requests-profile.json','documentation':'docs/PREPARED_REQUESTS.md'},
        'computed_targets':{'profile':'openpine.computed-targets.1','versions':[1,2,3,4,5,6],
            'specification':'spec/computed-targets-profile.json','documentation':'docs/COMPUTED_TARGETS.md'},
        'lower_tf':{'historical_profile':'openpine.lower-tf-profile.1','event_profile':'openpine.lower-events.1','language_versions':[5,6],
            'documentation':'docs/LOWER_TF.md','events_documentation':'docs/LOWER_EVENTS.md'},
        'special_forms': {'inputs': 'docs/INPUTS.md', 'statements': 'docs/STATEMENTS.md',
            'user_functions': 'docs/FUNCTIONS.md', 'requests': 'docs/CONTEXTS.md','context_graph':'docs/CONTEXT_GRAPH.md','partial_exits':'docs/PARTIAL_EXITS.md','pending_entries':'docs/PENDING_ENTRIES.md','lots':'docs/LOTS.md','order_groups':'docs/ORDER_GROUPS.md','trailing':'docs/TRAILING.md','economics':'docs/ECONOMICS.md','risk':'docs/RISK.md','performance':'docs/PERFORMANCE.md',
            'strategy_cycle':'docs/STRATEGY_CYCLE.md','intrabar':'docs/INTRABAR.md','strategies': 'docs/STRATEGIES.md', 'protective_exits': 'docs/PROTECTION.md', 'composite_results': 'docs/COMPOSITE.md', 'loops': 'docs/LOOPS.md', 'arrays':'docs/ARRAYS.md','environment':'docs/ENVIRONMENT.md','signals':'docs/SIGNALS.md'},
        'versions': versions}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='print the full machine-readable inventory')
    args = parser.parse_args(argv)
    value = report()
    if args.json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value['meaning'])
        for row in value['versions']:
            case_count = sum(len(corpus['case_ids']) for corpus in row['fixtures'])
            print(f"Pine v{row['language_version']}: {len(row['apis'])} admitted overloads, "
                  f"{len(row['references'])+len(row['computed_references'])} series references, {case_count} fixture cases")
        print('Special-form constraints and verification links: docs/CAPABILITIES.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
