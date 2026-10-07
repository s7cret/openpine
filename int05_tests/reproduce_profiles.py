"""Reproduce INT05 counts/hash/scope with the existing planner and collections.

Planning only: this command never launches pytest, builds packages or runs owner
gates. Output must be outside source roots and every attempt has its own folder.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_identity import ensure_external_output, write_once_json
from openpine.verification.execution_plan import PROFILES, make_plan
from openpine.verification.identity import digest, read_json


def reproduce(policy, collection, owner_launch, scenarios, output):
    roots = {n: Path(p) for n, p in collection['roots'].items()}
    ensure_external_output(output, roots)
    # Join rechecks the live source, locked inventory and collected markers.
    observed = join_collections([collection], roots)
    if observed['policy_hash'] != digest(policy):
        raise ValueError('collection differs from frozen policy')
    output.mkdir(parents=True, exist_ok=False)
    rows, emitted = [], set()
    for scenario in scenarios:
        for profile in PROFILES:
            started = time.perf_counter()
            plan = make_plan(profile=profile, policy=policy, roots=roots,
                source=observed['source'], inventories=observed['inventories'],
                environments=observed['environments'], requested=[scenario['owner']],
                changes=[scenario['owner'] + '/' + scenario['path']] if profile == 'affected' else [],
                owner_launch=owner_launch)
            if profile == 'affected':
                expected = sorted(policy['components']) if scenario['expected_owners'] == 'all' else scenario['expected_owners']
                if sorted(t['component'] for t in plan['tasks']) != expected:
                    raise ValueError('scenario differs from independent owner expectation')
            plan_hash = plan['content_hash']
            if plan_hash not in emitted:
                write_once_json(output / (plan_hash.removeprefix('sha256:') + '.json'), plan)
                emitted.add(plan_hash)
            tasks = [{'component': t['component'], 'id': t['id'], 'count': len(t['nodeids']),
                      'nodeids_hash': t['nodeids_hash'], 'full_inventory_hash': t['full_inventory_hash'],
                      'reviewed_lock_hash': t['reviewed_lock_hash'], 'deselected': t['deselected']}
                     for t in plan['tasks']]
            rows.append({'scenario': scenario['id'], 'profile': profile, 'plan_hash': plan_hash,
                         'obligation_count': sum(t['count'] for t in tasks),
                         'obligations_hash': digest(tasks), 'tasks': tasks,
                         'preparation_components': plan['preparation_components'],
                         'required_gates': plan['required_gates'],
                         'planning_wall_seconds': time.perf_counter() - started})
    result = {'collection_hash': collection['content_hash'], 'policy_hash': digest(policy),
              'source_hash': observed['source']['content_hash'], 'rows': rows,
              'unique_plans': len(emitted), 'executed': False, 'full_acceptance': False,
              'scope': 'baseline candidate planning for real patch paths; execution comparison remains required'}
    write_once_json(output / 'counts-hash-scope.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('policy', 'collection', 'owner-launch', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--scenarios', type=Path, default=Path(__file__).with_name('patch_scenarios.json'))
    args = parser.parse_args()
    result = reproduce(read_json(args.policy), read_json(args.collection), read_json(args.owner_launch),
                       read_json(args.scenarios), args.output)
    print(f"{len(result['rows'])} profile/scenario rows, {result['unique_plans']} frozen plans; execution not run")


if __name__ == '__main__':
    main()
