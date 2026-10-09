"""Planning-only composition from the existing CI owner's frozen attestation."""
from __future__ import annotations

import argparse
from pathlib import Path

from int05_tests.check_full_comparison import instrumentation_contract
from openpine.verification.execution_ci import attest_ci_source_commits, verify_bundle
from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_identity import ensure_external_output, write_once_json
from openpine.verification.execution_plan import make_plan, validate_plan
from openpine.verification.identity import digest, read_json, seal


def affected_from_ci(bundle, full, *, expected_bundle_hash, expected_full_hash,
                     changes, expected_owners, output):
    validate_plan(full, expected_hash=expected_full_hash)
    if full['profile'] != 'stage-full':
        raise ValueError('CI full plan must retain its stage-full owner boundary')
    report = verify_bundle(bundle, expected_source_hash=full['source']['content_hash'])
    if report['content_hash'] != expected_bundle_hash:
        raise ValueError('prepared bundle differs from independently frozen identity')
    commits = attest_ci_source_commits([report])
    if full.get('source_commits') != commits:
        raise ValueError('CI full plan differs from prepared producer attestation')
    roots = {n: Path(p) for n, p in full['roots'].items()}
    ensure_external_output(output, roots)
    policy = read_json(roots['openpine'] / 'verification/execution-policy.json')
    if digest(policy) != full['policy_hash']:
        raise ValueError('CI policy changed before affected preparation')
    observed = join_collections([read_json(bundle / 'collection.json')], roots)
    if observed['policy_hash'] != full['policy_hash']:
        raise ValueError('CI collection differs from frozen policy')
    if observed['environments'] != full['environments']:
        raise ValueError('CI interpreter changed before affected preparation')
    coverage = {t['coverage'] for t in full['tasks']}
    if len(coverage) != 1:
        raise ValueError('CI mixed instrumentation needs an explicit reviewed profile')
    coverage = coverage.pop()
    # A structurally valid, resealed full plan can still omit an owner or carry
    # a different reviewed lock. Reconstruct its obligations through the owner
    # before using it as the authority for any narrowed execution plan.
    reviewed_full = make_plan(profile='stage-full', policy=policy, roots=roots,
        source=observed['source'], inventories=observed['inventories'],
        environments=observed['environments'], shard_count=4,
        coverage=coverage, owner_launch=full.get('owner_launch'))
    for field in ('required_gates', 'preparation_components',
                  'pending_required_inventories', 'disk_free_guard',
                  'full_acceptance_requires_owner_gates'):
        if full.get(field) != reviewed_full.get(field):
            raise ValueError('CI full plan differs from reviewed owner requirements: ' + field)
    full_tasks = {t['id']: t for t in full['tasks']}
    reviewed_tasks = {t['id']: t for t in reviewed_full['tasks']}
    if set(full_tasks) != set(reviewed_tasks):
        raise ValueError('CI full plan omitted reviewed owner/interpreter obligations')
    for task_id, reference in reviewed_tasks.items():
        task = full_tasks[task_id]
        # Shard grouping is not an obligation; actual per-node instrumentation is.
        if ({k: v for k, v in task.items() if k != 'shards'}
                != {k: v for k, v in reference.items() if k != 'shards'}
                or instrumentation_contract(task) != instrumentation_contract(reference)):
            raise ValueError('CI full obligations differ from reviewed collection: ' + task_id)
    plan = make_plan(profile='affected', policy=policy, roots=roots,
        source=observed['source'], inventories=observed['inventories'],
        environments=observed['environments'], changes=changes, shard_count=4,
        coverage=coverage, owner_launch=full.get('owner_launch'))
    # Reuse make_ci_plan's sealing composition only after the existing
    # verify_bundle/attest_ci_source_commits owner path succeeds.
    plan = seal({**{k: v for k, v in plan.items() if k != 'content_hash'}, 'source_commits': commits})
    validate_plan(plan)
    if sorted({t['component'] for t in plan['tasks']}) != sorted(expected_owners):
        raise ValueError('affected scope differs from independent scenario expectation')
    for task in plan['tasks']:
        reference = full_tasks[task['id']]
        if (task['nodeids'] != reference['nodeids']
                or instrumentation_contract(task) != instrumentation_contract(reference)):
            raise ValueError('affected CI obligations or instrumentation differ from full')
    write_once_json(output, plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'full-plan', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-bundle-hash', 'expected-full-hash'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--changed', action='append', required=True)
    parser.add_argument('--expected-owner', action='append', required=True)
    args = parser.parse_args()
    plan = affected_from_ci(args.bundle, read_json(args.full_plan),
        expected_bundle_hash=args.expected_bundle_hash, expected_full_hash=args.expected_full_hash,
        changes=args.changed, expected_owners=args.expected_owner, output=args.output)
    print(plan['content_hash'])


if __name__ == '__main__':
    raise SystemExit(main())
