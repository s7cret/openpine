"""Compose reviewed INT05 plans and explicitly dispatch existing campaign owners.

Preparation never launches tests. Execution requires one named campaign and an
independently recorded checkpoint hash. Checks retain the existing comparator's
verdicts; neither this orchestration nor pytest comparison grants acceptance.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from int05_tests.affected_from_ci import affected_from_ci
from int05_tests.check_full_comparison import (
    check_pair, ensure_external_comparison_output, validate_pair_contract,
)
from openpine.verification.execution_binding import make_binding
from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_ci import attest_ci_source_commits, verify_bundle
from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_identity import (
    ensure_external_output, environment_snapshot, evidence_path, hash_file, write_once_json,
)
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import digest, read_json, seal, verify

CHECKPOINT_SCHEMA = 'openpine.int05_positive_campaign_checkpoint.v1'


def _read_frozen(path, expected):
    if hash_file(path) != expected:
        raise ValueError('input differs from independently frozen file: ' + str(path))
    return read_json(path)


def _scenario_rows(manifest, scenarios, owners):
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError('reviewed scenario set is empty')
    if not isinstance(manifest.get('rows'), list):
        raise ValueError('real patch manifest omitted scenario rows')
    rows = {}
    for row in manifest['rows']:
        key = row.get('id')
        if (not isinstance(key, str) or not re.fullmatch('[a-zA-Z0-9_-]{1,60}', key)
                or key == 'full' or key in rows):
            raise ValueError('invalid or duplicate real patch scenario')
        rows[key] = row
    seen = set()
    for scenario in scenarios:
        key = scenario.get('id')
        if key in seen or key not in rows:
            raise ValueError('real patch manifest differs from reviewed scenario set')
        seen.add(key)
        row = rows[key]
        expected = sorted(owners) if scenario['expected_owners'] == 'all' else scenario['expected_owners']
        if (not isinstance(expected, list) or not expected or len(set(expected)) != len(expected)
                or not set(expected).issubset(owners)
                or row.get('changed') != scenario['owner'] + '/' + scenario['path']
                or row.get('owner') != scenario['owner']
                or sorted(row.get('expected_owners', [])) != sorted(expected)
                or row.get('expected_preparation') != sorted(set(owners) - set(expected))
                or row.get('oracle_owner') != scenario.get('oracle_owner', scenario['owner'])
                or row.get('oracle') != scenario['oracle']
                or row.get('oracle_in_reviewed_inventory') is not True
                or row.get('patch_replay_matches_after') is not True):
            raise ValueError('real patch scenario differs from independently reviewed scope: ' + key)
    if seen != set(rows):
        raise ValueError('real patch manifest has unreviewed scenarios')
    return [rows[scenario['id']] for scenario in scenarios]


def prepare_positive_campaign(*, bundle, full_plan, patch_manifest, reviewed_collection,
                              scenarios, expected_bundle_hash, expected_full_hash,
                              expected_patch_manifest_sha256, expected_scenarios_sha256, output):
    """Write plans and their counts/hashes without execution or rebaselining."""
    full = validate_plan(read_json(full_plan), expected_hash=expected_full_hash)
    roots = {n: Path(p) for n, p in full['roots'].items()}
    ensure_external_output(output, roots)
    if output.exists():
        raise ValueError('campaign preparation output exists; retain the prior checkpoint')
    report = verify_bundle(bundle, expected_source_hash=full['source']['content_hash'])
    if report['content_hash'] != expected_bundle_hash:
        raise ValueError('prepared bundle differs from independently frozen identity')
    commits = attest_ci_source_commits([report])
    manifest = _read_frozen(patch_manifest, expected_patch_manifest_sha256)
    declarations = _read_frozen(scenarios, expected_scenarios_sha256)
    policy = read_json(roots['openpine'] / 'verification/execution-policy.json')
    if (manifest.get('source_hash') != full['source']['content_hash']
            or manifest.get('source_commits') != commits or full.get('source_commits') != commits
            or manifest.get('policy_hash') != digest(policy) or full['policy_hash'] != digest(policy)):
        raise ValueError('real patch, prepared bundle and full plan identities differ')
    collection = read_json(reviewed_collection)
    if collection.get('content_hash') != manifest.get('collection_hash'):
        raise ValueError('reviewed collection differs from frozen real patch inputs')
    observed = join_collections([collection], roots)
    prepared = join_collections([read_json(bundle / 'collection.json')], roots)
    if (observed['policy_hash'] != full['policy_hash']
            or observed['inventories'] != prepared['inventories']
            or {n: e['identity'] for n, e in observed['environments'].items()} !=
               {n: e['identity'] for n, e in full['environments'].items()}):
        raise ValueError('prepared campaign differs from independently reviewed collection')
    rows = _scenario_rows(manifest, declarations, policy['components'])
    output.mkdir(parents=True, exist_ok=False)
    write_once_json(output / 'full-plan.json', full)
    write_once_json(output / 'policy.json', policy)
    write_once_json(output / 'patch-manifest.json', manifest)
    write_once_json(output / 'scenarios.json', declarations)
    entries = {}
    for row in rows:
        relative = 'affected/' + row['id'] + '.json'
        plan = affected_from_ci(bundle, full, expected_bundle_hash=expected_bundle_hash,
            expected_full_hash=expected_full_hash, changes=[row['changed']],
            expected_owners=row['expected_owners'], output=output / relative)
        validate_pair_contract(policy, plan, full, affected_hash=plan['content_hash'],
            full_hash=expected_full_hash, expected_owners=row['expected_owners'])
        if plan['preparation_components'] != row['expected_preparation']:
            raise ValueError('affected preparation scope differs from reviewed real patch')
        entries[row['id']] = {'plan': relative, 'plan_hash': plan['content_hash'],
            'changed': row['changed'], 'expected_owners': row['expected_owners'],
            'preparation_components': plan['preparation_components'],
            'obligations': sum(len(t['nodeids']) for t in plan['tasks']),
            'tasks': [{'id': t['id'], 'count': len(t['nodeids']),
                       'nodeids_hash': t['nodeids_hash'], 'full_inventory_hash': t['full_inventory_hash'],
                       'reviewed_lock_hash': t['reviewed_lock_hash']} for t in plan['tasks']]}
    checkpoint = seal({'schema_id': CHECKPOINT_SCHEMA, 'bundle_hash': expected_bundle_hash,
        'patch_manifest_sha256': hash_file(output / 'patch-manifest.json'),
        'scenarios_sha256': hash_file(output / 'scenarios.json'),
        'reviewed_collection_hash': collection['content_hash'], 'policy_hash': digest(policy),
        'source_hash': full['source']['content_hash'], 'source_commits': commits,
        'full': {'plan': 'full-plan.json', 'plan_hash': expected_full_hash,
                 'obligations': sum(len(t['nodeids']) for t in full['tasks'])},
        'affected': entries, 'execution_performed': False, 'full_acceptance': False})
    write_once_json(output / 'checkpoint.json', checkpoint)
    return checkpoint


def load_checkpoint(path, expected_hash):
    """Revalidate all anchored plans, even when executing just one campaign."""
    checkpoint = read_json(path)
    verify(checkpoint, CHECKPOINT_SCHEMA)
    if checkpoint['content_hash'] != expected_hash:
        raise ValueError('checkpoint differs from independently reviewed identity')
    root = path.parent
    policy = read_json(evidence_path(root, 'policy.json'))
    manifest = _read_frozen(evidence_path(root, 'patch-manifest.json'), checkpoint['patch_manifest_sha256'])
    scenarios = _read_frozen(evidence_path(root, 'scenarios.json'), checkpoint['scenarios_sha256'])
    rows = _scenario_rows(manifest, scenarios, policy['components'])
    if (digest(policy) != checkpoint['policy_hash'] or
            set(checkpoint['affected']) != {row['id'] for row in rows}):
        raise ValueError('checkpoint omitted or added reviewed scenarios')
    full_entry = checkpoint['full']
    full = validate_plan(read_json(evidence_path(root, full_entry['plan'])),
                         expected_hash=full_entry['plan_hash'])
    if (full['source']['content_hash'] != checkpoint['source_hash']
            or full.get('source_commits') != checkpoint['source_commits']
            or manifest.get('source_hash') != checkpoint['source_hash']
            or manifest.get('source_commits') != checkpoint['source_commits']
            or manifest.get('collection_hash') != checkpoint['reviewed_collection_hash']
            or manifest.get('policy_hash') != checkpoint['policy_hash']
            or full_entry['obligations'] != sum(len(t['nodeids']) for t in full['tasks'])):
        raise ValueError('checkpoint candidate or reviewed identities differ')
    plans = {'full': full}
    for row in rows:
        key, entry = row['id'], checkpoint['affected'][row['id']]
        plan = validate_plan(read_json(evidence_path(root, entry['plan'])), expected_hash=entry['plan_hash'])
        if (entry['expected_owners'] != row['expected_owners'] or entry['changed'] != row['changed']
                or entry['preparation_components'] != row['expected_preparation']
                or plan['preparation_components'] != row['expected_preparation']
                or entry['obligations'] != sum(len(t['nodeids']) for t in plan['tasks'])):
            raise ValueError('checkpoint scenario scope differs from frozen real patch: ' + key)
        validate_pair_contract(policy, plan, full, affected_hash=entry['plan_hash'],
            full_hash=full_entry['plan_hash'], expected_owners=entry['expected_owners'])
        plans[key] = plan
    return checkpoint, policy, plans


def run_positive_campaign(*, checkpoint_path, expected_checkpoint_hash, campaign_id,
                          restored, output, run_id, jobs, max_parallel_shards, memory_mib):
    """Launch exactly one explicitly requested campaign; preserve failed evidence."""
    checkpoint, _, plans = load_checkpoint(checkpoint_path, expected_checkpoint_hash)
    if campaign_id not in plans:
        raise ValueError('unknown reviewed campaign id')
    if (type(jobs) is not int or not 1 <= jobs <= 2 or max_parallel_shards != 1
            or type(max_parallel_shards) is not int or type(memory_mib) is not int
            or not 64 <= memory_mib <= 6144):
        raise ValueError('positive campaign requires jobs 1..2, one parallel shard and memory 64..6144 MiB')
    observed = read_json(restored)
    plan = plans[campaign_id]
    roots = {n: Path(p) for n, p in observed['roots'].items()}
    ensure_external_output(output, roots)
    ensure_external_output(output, {n: Path(p) for n, p in plan['roots'].items()})
    if (observed.get('candidate_hash') != checkpoint['source_hash']
            or observed.get('source_commits') != checkpoint['source_commits']
            or observed.get('environment') != environment_snapshot()
            or Path(observed['executable']).resolve() != Path(sys.executable).resolve()
            or len(plan['environments']) != 1
            or next(iter(plan['environments'].values()))['identity'] != observed['environment']):
        raise ValueError('run must use the exact restored owner interpreter, source and commits')
    slot = next(iter(plan['environments']))
    binding = make_binding(plan, roots, {slot: observed['executable']})
    entry = checkpoint['full'] if campaign_id == 'full' else checkpoint['affected'][campaign_id]
    plan_path = evidence_path(checkpoint_path.parent, entry['plan'])
    run_campaign(plan, plan_path, output, run_id=run_id, binding=binding,
        jobs=jobs, max_parallel_shards=max_parallel_shards, memory_mib=memory_mib,
        build_commit=checkpoint['source_commits'].get('openpine'))
    result = aggregate_campaign(plan, output, expected_plan_hash=entry['plan_hash'], expected_run_id=run_id)
    write_once_json(output / 'aggregate.json', result)
    return result


def check_positive_campaign(*, checkpoint_path, expected_checkpoint_hash,
                            evidence_index, expected_evidence_index_sha256, output):
    checkpoint, policy, plans = load_checkpoint(checkpoint_path, expected_checkpoint_hash)
    index = _read_frozen(evidence_index, expected_evidence_index_sha256)
    if (set(index) != {'full', 'affected'} or set(index['affected']) != set(checkpoint['affected'])):
        raise ValueError('evidence index must retain every reviewed campaign')
    for plan in plans.values():
        ensure_external_output(output, {n: Path(p) for n, p in plan['roots'].items()})
    locations = [index['full'], *index['affected'].values()]
    identities = []
    for location in locations:
        if (set(location) != {'evidence', 'run_id'} or not isinstance(location['evidence'], str)
                or not Path(location['evidence']).is_absolute()
                or not isinstance(location['run_id'], str)
                or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', location['run_id'])):
            raise ValueError('evidence index requires explicit absolute paths and run IDs')
        identities.append((str(Path(location['evidence']).resolve()), location['run_id']))
    if len(set(identities)) != len(identities):
        raise ValueError('distinct reviewed campaigns cannot reuse the same raw execution')
    ensure_external_comparison_output(output, plans['full'], Path(index['full']['evidence']))
    for key, location in index['affected'].items():
        ensure_external_comparison_output(output, plans[key], Path(location['evidence']))
    comparisons = {}
    for key, entry in checkpoint['affected'].items():
        affected = index['affected'][key]
        comparisons[key] = check_pair(policy, plans[key], plans['full'],
            affected_evidence=Path(affected['evidence']), full_evidence=Path(index['full']['evidence']),
            affected_hash=entry['plan_hash'], full_hash=checkpoint['full']['plan_hash'],
            affected_run=affected['run_id'], full_run=index['full']['run_id'],
            expected_owners=entry['expected_owners'])
    result = {'checkpoint_hash': expected_checkpoint_hash,
              'evidence_index_sha256': expected_evidence_index_sha256,
              'comparisons': comparisons, 'full_acceptance': False}
    write_once_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    prepare = actions.add_parser('prepare', help='Freeze reviewed plans without test execution')
    for name in ('bundle', 'full-plan', 'patch-manifest', 'reviewed-collection', 'scenarios', 'output'):
        prepare.add_argument('--' + name, type=Path, required=True)
    for name in ('expected-bundle-hash', 'expected-full-hash', 'expected-patch-manifest-sha256',
                 'expected-scenarios-sha256'):
        prepare.add_argument('--' + name, required=True)
    run = actions.add_parser('run', help='Explicitly launch one existing campaign owner')
    check = actions.add_parser('check', help='Delegate all frozen pairs to the existing comparator')
    for command in (run, check):
        command.add_argument('--checkpoint', dest='checkpoint_path', type=Path, required=True)
        command.add_argument('--expected-checkpoint-hash', required=True)
        command.add_argument('--output', type=Path, required=True)
    run.add_argument('--campaign-id', required=True)
    run.add_argument('--restored', type=Path, required=True)
    run.add_argument('--run-id', required=True)
    run.add_argument('--jobs', type=int, required=True)
    run.add_argument('--max-parallel-shards', type=int, required=True)
    run.add_argument('--memory-mib', type=int, required=True)
    check.add_argument('--evidence-index', type=Path, required=True)
    check.add_argument('--expected-evidence-index-sha256', required=True)
    args = vars(parser.parse_args())
    action = args.pop('action')
    if action == 'prepare':
        result = prepare_positive_campaign(**args)
        print(result['content_hash'])
        return 0
    if action == 'run':
        result = run_positive_campaign(**args)
        print(result['content_hash'])
        return 0 if result['pytest_scope_passed'] else 1
    result = check_positive_campaign(**args)
    print(f"{len(result['comparisons'])} existing comparator results written; full acceptance is separate")
    return 0 if all(r['selection_trusted_for_this_scenario'] for r in result['comparisons'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
