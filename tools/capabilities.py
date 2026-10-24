"""Report admitted offline APIs and linked fixtures without claiming test results.

This is an implementation inventory, not a TradingView compatibility score.
The separate spec/coverage.json counts imported reference material.
"""
from dataclasses import asdict
import argparse
import json

from packages.compiler.semantic.specification import ROOT, read_json
from packages.contracts import HISTORICAL_PROFILE_ID
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
        for name,(signature,contract) in sorted({**catalog.partial_exits,**catalog.pending_entries}.items()):
            apis.append({'name':name,'operation':contract.operation,'overload_id':contract.overload_id,
                'activation':'explicit named limit/stop' if contract.operation=='simulation.entry_pending' else 'explicit named qty/qty_percent','parameters':[p['name'] for p in signature['parameters']],
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
        'special_forms': {'inputs': 'docs/INPUTS.md', 'statements': 'docs/STATEMENTS.md',
            'user_functions': 'docs/FUNCTIONS.md', 'requests': 'docs/CONTEXTS.md','context_graph':'docs/CONTEXT_GRAPH.md','partial_exits':'docs/PARTIAL_EXITS.md','pending_entries':'docs/PENDING_ENTRIES.md',
            'strategies': 'docs/STRATEGIES.md', 'protective_exits': 'docs/PROTECTION.md', 'composite_results': 'docs/COMPOSITE.md', 'loops': 'docs/LOOPS.md', 'arrays':'docs/ARRAYS.md','environment':'docs/ENVIRONMENT.md','signals':'docs/SIGNALS.md'},
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
