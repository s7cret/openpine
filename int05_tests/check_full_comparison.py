"""Read-only INT05 qualification check over existing frozen campaign artifacts.

No runner, schema, rebaseline, or acceptance owner is introduced. Both campaigns
must pass the existing raw-evidence aggregator before narrowing can be trusted.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from openpine.verification.execution_binding import validate_binding
from openpine.verification.execution_campaign import aggregate_campaign, compiler_commit_environment
from openpine.verification.execution_identity import ensure_external_output, evidence_path, write_once_json
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import digest, read_json


def instrumentation_contract(task):
    """Compare instrumentation per obligation, independent of shard grouping."""
    excluded = task.get('untraced_markers', [])
    if (not isinstance(excluded, list) or any(not isinstance(v, str) for v in excluded)
            or len(set(excluded)) != len(excluded)):
        raise ValueError('invalid comparison instrumentation marker contract')
    traced = task.get('coverage', False)
    return {
        'coverage': traced,
        'coverage_package': task.get('coverage_package', task['component'].replace('-', '_')),
        'untraced_markers': sorted(excluded),
        'node_markers': task.get('node_markers'),
        'plugins': task.get('plugins', []),
        'node_coverage': {node: shard.get('coverage', traced)
                          for shard in task['shards'] for node in shard['nodeids']},
    }


def ensure_external_comparison_output(output, plan, evidence):
    """Historical plans and relocated campaign bindings both remain source roots."""
    ensure_external_output(output, {n: Path(p) for n, p in plan['roots'].items()})
    if (Path(evidence) / 'binding.json').exists():
        binding = read_json(evidence_path(Path(evidence), 'binding.json'))
        validate_binding(plan, binding)
        ensure_external_output(output, {n: Path(p) for n, p in binding['roots'].items()})


def validate_pair_contract(policy, affected, full, *, affected_hash, full_hash,
                           expected_owners):
    """Reject incomplete owner/interpreter boundaries before reading receipts."""
    validate_plan(affected, expected_hash=affected_hash)
    validate_plan(full, expected_hash=full_hash)
    if affected['profile'] != 'affected' or full['profile'] not in {'stage-full', 'release-full'}:
        raise ValueError('comparison requires affected and full plans')
    if affected['policy_hash'] != digest(policy) or full['policy_hash'] != digest(policy):
        raise ValueError('comparison policy differs from independently frozen policy')
    for field in ('source', 'environments', 'source_commits', 'owner_launch'):
        if affected.get(field) != full.get(field):
            raise ValueError('comparison inputs differ: ' + field)
    if any(t['component'] == 'openpine' for t in full['tasks']):
        # The existing runner injects these producer identities into compiler
        # execution. Planning-only output without attestation is not executable.
        compiler_commit_environment(full.get('source_commits', {}))
    for plan in (affected, full):
        if plan['required_gates'] != policy.get('required_gates', {}).get(plan['profile'], []):
            raise ValueError('comparison dropped owner gates')
    full_tasks = {t['id']: t for t in full['tasks']}
    required_tasks = set()
    for owner, settings in policy['components'].items():
        required_versions = settings.get('pythons', ['3.13'])
        if (not isinstance(required_versions, list) or not required_versions
                or any(not isinstance(v, str) for v in required_versions)
                or len(set(required_versions)) != len(required_versions)):
            raise ValueError('invalid comparison interpreter policy')
        seen_versions = set()
        for name, environment in full['environments'].items():
            version = '.'.join(environment['identity']['python'].split('.')[:2])
            if version in required_versions:
                required_tasks.add(owner + '@' + name)
                seen_versions.add(version)
        if seen_versions != set(required_versions):
            raise ValueError('full comparison omitted a required interpreter: ' + owner)
    if set(full_tasks) != required_tasks:
        raise ValueError('full comparison omitted a required owner/interpreter task')
    if (not isinstance(expected_owners, (list, tuple)) or not expected_owners
            or any(not isinstance(v, str) or v not in policy['components'] for v in expected_owners)
            or len(set(expected_owners)) != len(expected_owners)):
        raise ValueError('independent scenario owners must be unique known owners')
    selected = sorted({t['component'] for t in affected['tasks']})
    if selected != sorted(expected_owners):
        raise ValueError('affected scope differs from independent scenario expectation')
    if {t['id'] for t in affected['tasks']} != {
            key for key, task in full_tasks.items() if task['component'] in selected}:
        raise ValueError('affected comparison omitted a selected owner/interpreter task')
    for task in affected['tasks']:
        reference = full_tasks.get(task['id'])
        if reference is None or any(task[field] != reference[field] for field in (
                'nodeids', 'nodeids_hash', 'full_inventory_hash', 'reviewed_lock_hash',
                'deselected', 'variant', 'execution_path', 'mode')):
            raise ValueError('affected obligations differ from full comparison')
        if instrumentation_contract(task) != instrumentation_contract(reference):
            raise ValueError('comparison instrumentation differs: ' + task['id'])
        for field, default in (('timeout_seconds', None), ('cpu_slots', None),
                               ('memory_mib', 256), ('exclusive_group', None),
                               ('private_retention', 'preserve')):
            if task.get(field, default) != reference.get(field, default):
                raise ValueError('comparison execution settings differ: ' + task['id'])
    return selected


def check_pair(policy, affected, full, *, affected_evidence, full_evidence,
               affected_hash, full_hash, affected_run, full_run, expected_owners):
    selected = validate_pair_contract(policy, affected, full,
        affected_hash=affected_hash, full_hash=full_hash, expected_owners=expected_owners)
    scoped = aggregate_campaign(affected, Path(affected_evidence),
        expected_plan_hash=affected_hash, expected_run_id=affected_run)
    complete = aggregate_campaign(full, Path(full_evidence),
        expected_plan_hash=full_hash, expected_run_id=full_run)
    # A failing/missing full obligation can never be hidden behind a green
    # affected run. Crashes, drift and incomplete raw reports also block trust.
    trusted = scoped['pytest_scope_passed'] and complete['pytest_scope_passed']
    return {
        'selection_trusted_for_this_scenario': trusted,
        'full_failed_while_affected_passed': scoped['pytest_scope_passed'] and not complete['pytest_scope_passed'],
        'affected_plan_hash': affected_hash, 'full_plan_hash': full_hash,
        'source_hash': full['source']['content_hash'], 'policy_hash': digest(policy),
        'test_obligation_components': selected,
        'preparation_components': affected['preparation_components'],
        'affected': scoped, 'full': complete,
        'scope': 'this pair of real patch campaigns on the frozen interpreter and inventory',
        'full_acceptance': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('policy', 'affected-plan', 'full-plan', 'affected-evidence', 'full-evidence', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('affected-hash', 'full-hash', 'affected-run', 'full-run'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--expected-owner', action='append', required=True)
    args = parser.parse_args()
    affected, full = read_json(args.affected_plan), read_json(args.full_plan)
    for plan, evidence in ((affected, args.affected_evidence), (full, args.full_evidence)):
        ensure_external_comparison_output(args.output, plan, evidence)
    result = check_pair(read_json(args.policy), affected, full,
        affected_evidence=args.affected_evidence, full_evidence=args.full_evidence,
        affected_hash=args.affected_hash, full_hash=args.full_hash,
        affected_run=args.affected_run, full_run=args.full_run,
        expected_owners=args.expected_owner)
    write_once_json(args.output, result)
    return 0 if result['selection_trusted_for_this_scenario'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
