"""Bounded orchestration contracts; these tiny sources never qualify INT05."""
from __future__ import annotations

import copy
import shutil
import sys

import pytest

from int05_tests.positive_campaign import (
    check_positive_campaign, load_checkpoint, prepare_positive_campaign, run_positive_campaign,
)
from openpine.verification.execution_ci import (
    COMPONENTS, bundle_manifest, create_source_archive, make_ci_plan,
)
from openpine.verification.execution_identity import (
    environment_snapshot, hash_file, source_snapshot, write_once_json,
)
from openpine.verification.identity import digest, read_json, seal
from rc6_tests.test_rc6_execution_platform import HOST, lock, reseal


def campaign_inputs(tmp_path, *, fails=False):
    roots = {}
    for owner in COMPONENTS:
        root = tmp_path / 'stack' / owner
        root.mkdir(parents=True)
        (root / 'test_value.py').write_text('def test_value():\n    assert ' +
                                         str(not fails or owner != 'openpine') + '\n')
        roots[owner] = root
    # The launch plugin is an existing execution owner, copied without receipts.
    package = roots['openpine'] / 'openpine'
    package.mkdir()
    shutil.copy2(HOST / 'openpine/__init__.py', package / '__init__.py')
    shutil.copytree(HOST / 'openpine/verification', package / 'verification',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (roots['openpine'] / 'pyproject.toml').write_text('[tool.coverage.run]\nsource=["openpine"]\n')
    policy = {'components': {n: {} for n in COMPONENTS},
              'required_gates': {'stage-full': ['foundation']}}
    policy['components']['openpine']['dependencies'] = [n for n in COMPONENTS if n != 'openpine']
    write_once_json(roots['openpine'] / 'verification/execution-policy.json', policy)
    source, environment = source_snapshot(roots), environment_snapshot()
    commits = {owner: f'{index:040x}' for index, owner in enumerate(COMPONENTS, start=1)}
    node = 'test_value.py::test_value'
    collection = seal({'schema_id': 'openpine.test_collection_set.v1',
        'source': source, 'roots': {n: str(p) for n, p in roots.items()},
        'policy_hash': digest(policy), 'environments': {'py313': {
            'identity': environment, 'executable': sys.executable}},
        'inventories': {n + '@py313': {'nodeids': [node], 'node_markers': {node: []},
            'reviewed_lock': lock([node]), 'source_hash': source['content_hash'],
            'environment_hash': environment['content_hash'], 'deselected': 0} for n in COMPONENTS},
        'ok': True, 'errors': [], 'collect_only': True, 'execution_pass': False})
    collection_path = tmp_path / 'reviewed-collection.json'
    write_once_json(collection_path, collection)
    full_path = tmp_path / 'full.json'
    full = make_ci_plan([collection_path], roots, full_path, commits)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    shutil.copy2(collection_path, bundle / 'collection.json')
    create_source_archive(roots, bundle / 'sources.tar.gz')
    prepared = bundle_manifest(bundle, source=source, environment=environment,
        source_pins={n: commits[n] for n in COMPONENTS if n != 'openpine'}, host_commit=commits['openpine'])
    scenarios = [{'id': 'narrow-local', 'owner': 'openpine', 'path': 'test_value.py',
                  'oracle': node, 'expected_owners': ['openpine']},
                 {'id': 'shared-fixture', 'owner': 'pinelib', 'path': 'tests/fixture.json',
                  'oracle': node, 'expected_owners': 'all'}]
    scenarios_path = tmp_path / 'scenarios.json'
    write_once_json(scenarios_path, scenarios)
    rows = []
    for scenario in scenarios:
        owners = sorted(COMPONENTS) if scenario['expected_owners'] == 'all' else scenario['expected_owners']
        rows.append({'id': scenario['id'], 'owner': scenario['owner'],
            'changed': scenario['owner'] + '/' + scenario['path'], 'oracle_owner': scenario['owner'],
            'oracle': node, 'expected_owners': owners,
            'expected_preparation': sorted(set(COMPONENTS) - set(owners)),
            'oracle_in_reviewed_inventory': True, 'patch_replay_matches_after': True})
    manifest_path = tmp_path / 'patch-manifest.json'
    manifest = {'source_hash': source['content_hash'], 'policy_hash': digest(policy),
                'source_commits': commits, 'collection_hash': collection['content_hash'], 'rows': rows}
    write_once_json(manifest_path, manifest)
    restored = tmp_path / 'restored.json'
    write_once_json(restored, {'candidate_hash': source['content_hash'], 'roots': collection['roots'],
        'executable': sys.executable, 'environment': environment, 'source_commits': commits})
    return {'bundle': bundle, 'full_plan': full_path, 'patch_manifest': manifest_path,
            'reviewed_collection': collection_path, 'scenarios': scenarios_path,
            'expected_bundle_hash': prepared['content_hash'], 'expected_full_hash': full['content_hash'],
            'expected_patch_manifest_sha256': hash_file(manifest_path),
            'expected_scenarios_sha256': hash_file(scenarios_path), 'output': tmp_path / 'checkpoint'}, restored


def test_prepare_uses_attested_owners_and_records_every_scenario_without_execution(tmp_path):
    inputs, _ = campaign_inputs(tmp_path)
    checkpoint = prepare_positive_campaign(**inputs)
    loaded, _, plans = load_checkpoint(inputs['output'] / 'checkpoint.json', checkpoint['content_hash'])
    assert loaded == checkpoint
    assert set(plans) == {'full', 'narrow-local', 'shared-fixture'}
    assert checkpoint['full']['obligations'] == 8
    assert checkpoint['affected']['narrow-local']['obligations'] == 1
    assert checkpoint['affected']['shared-fixture']['obligations'] == 8
    assert not list(inputs['output'].rglob('run.json'))
    assert not checkpoint['execution_performed'] and not checkpoint['full_acceptance']


@pytest.mark.parametrize('mutation', ['omitted-scenario', 'scope', 'source', 'collection', 'stale-anchor'])
def test_prepare_rejects_review_drift_before_writing_plans(tmp_path, mutation):
    inputs, _ = campaign_inputs(tmp_path)
    path = inputs['patch_manifest']
    manifest = read_json(path)
    if mutation == 'omitted-scenario':
        manifest['rows'].pop()
    elif mutation == 'scope':
        manifest['rows'][0]['expected_owners'] = list(COMPONENTS)
    elif mutation == 'source':
        manifest['source_commits']['pinelib'] = 'f' * 40
    elif mutation == 'collection':
        manifest['collection_hash'] = 'sha256:' + '0' * 64
    else:
        inputs['expected_patch_manifest_sha256'] = 'sha256:' + '0' * 64
    if mutation != 'stale-anchor':
        path.unlink()
        write_once_json(path, manifest)
        inputs['expected_patch_manifest_sha256'] = hash_file(path)
    with pytest.raises(ValueError):
        prepare_positive_campaign(**inputs)
    assert not inputs['output'].exists()


@pytest.mark.parametrize('mutation', ['unknown-id', 'jobs', 'parallel', 'memory', 'commits'])
def test_explicit_execution_admission_fails_without_launch_or_evidence(tmp_path, mutation, monkeypatch):
    inputs, restored = campaign_inputs(tmp_path)
    checkpoint = prepare_positive_campaign(**inputs)
    args = dict(checkpoint_path=inputs['output'] / 'checkpoint.json',
        expected_checkpoint_hash=checkpoint['content_hash'], campaign_id='narrow-local',
        restored=restored, output=tmp_path / 'campaign', run_id='bounded', jobs=1,
        max_parallel_shards=1, memory_mib=512)
    if mutation == 'unknown-id':
        args['campaign_id'] = 'missing'
    elif mutation == 'jobs':
        args['jobs'] = 3
    elif mutation == 'parallel':
        args['max_parallel_shards'] = 2
    elif mutation == 'memory':
        args['memory_mib'] = 6145
    else:
        observed = read_json(restored)
        observed['source_commits']['pinelib'] = 'f' * 40
        restored.unlink()
        write_once_json(restored, observed)
    monkeypatch.setattr('int05_tests.positive_campaign.run_campaign',
                        lambda *a, **k: pytest.fail('invalid campaign must not launch'))
    with pytest.raises(ValueError):
        run_positive_campaign(**args)
    assert not args['output'].exists()


def test_explicit_tiny_affected_launch_delegates_binding_runner_and_raw_aggregation(tmp_path):
    inputs, restored = campaign_inputs(tmp_path)
    checkpoint = prepare_positive_campaign(**inputs)
    output = tmp_path / 'actual-tiny-affected'
    report = run_positive_campaign(checkpoint_path=inputs['output'] / 'checkpoint.json',
        expected_checkpoint_hash=checkpoint['content_hash'], campaign_id='narrow-local',
        restored=restored, output=output, run_id='bounded-positive-adapter', jobs=1,
        max_parallel_shards=1, memory_mib=512)
    assert report['pytest_scope_passed'], report
    assert report['required_obligations'] == report['executed_obligations'] == 1
    binding = read_json(output / 'binding.json')
    assert set(binding['roots']) == set(COMPONENTS)
    assert read_json(output / 'aggregate.json') == report
    assert not report['full_stage_accepted']


def test_failed_explicit_launch_retains_existing_raw_evidence_and_failed_aggregate(tmp_path):
    inputs, restored = campaign_inputs(tmp_path, fails=True)
    checkpoint = prepare_positive_campaign(**inputs)
    output = tmp_path / 'actual-failing-tiny-affected'
    report = run_positive_campaign(checkpoint_path=inputs['output'] / 'checkpoint.json',
        expected_checkpoint_hash=checkpoint['content_hash'], campaign_id='narrow-local',
        restored=restored, output=output, run_id='bounded-failed-adapter', jobs=1,
        max_parallel_shards=1, memory_mib=512)
    assert not report['pytest_scope_passed']
    assert read_json(output / 'aggregate.json') == report
    assert (output / 'run.json').is_file()
    assert 'FAILED test_value.py::test_value' in (
        output / 'openpine@py313/s000/a001/stdout.log').read_text()


def test_all_pair_check_preserves_failed_full_comparator_results(tmp_path, monkeypatch):
    inputs, _ = campaign_inputs(tmp_path)
    checkpoint = prepare_positive_campaign(**inputs)
    index = {'full': {'evidence': str(tmp_path / 'full-evidence'), 'run_id': 'full'},
             'affected': {key: {'evidence': str(tmp_path / key), 'run_id': key}
                          for key in checkpoint['affected']}}
    index_path = tmp_path / 'index.json'
    write_once_json(index_path, index)
    calls = []

    def existing_comparator(*args, **kwargs):
        calls.append(kwargs)
        return {'selection_trusted_for_this_scenario': False,
                'full_failed_while_affected_passed': True, 'full_acceptance': False}

    monkeypatch.setattr('int05_tests.positive_campaign.check_pair', existing_comparator)
    result = check_positive_campaign(checkpoint_path=inputs['output'] / 'checkpoint.json',
        expected_checkpoint_hash=checkpoint['content_hash'], evidence_index=index_path,
        expected_evidence_index_sha256=hash_file(index_path), output=tmp_path / 'comparison.json')
    assert len(calls) == 2
    assert all(c['full_hash'] == checkpoint['full']['plan_hash'] for c in calls)
    assert all(not c['selection_trusted_for_this_scenario'] for c in result['comparisons'].values())
    assert not result['full_acceptance']


@pytest.mark.parametrize('mutation', ['missing-pair', 'plan-drift', 'checkpoint-anchor'])
def test_all_pair_check_rejects_missing_or_stale_inputs_before_comparison(tmp_path, mutation, monkeypatch):
    inputs, _ = campaign_inputs(tmp_path)
    checkpoint = prepare_positive_campaign(**inputs)
    anchor = checkpoint['content_hash']
    index = {'full': {'evidence': str(tmp_path / 'full-evidence'), 'run_id': 'full'},
             'affected': {key: {'evidence': str(tmp_path / key), 'run_id': key}
                          for key in checkpoint['affected']}}
    if mutation == 'missing-pair':
        index['affected'].pop('shared-fixture')
    elif mutation == 'checkpoint-anchor':
        anchor = 'sha256:' + '0' * 64
    else:
        plan_path = inputs['output'] / checkpoint['affected']['narrow-local']['plan']
        plan = copy.deepcopy(read_json(plan_path))
        plan['tasks'][0]['timeout_seconds'] = 60
        plan_path.unlink()
        write_once_json(plan_path, reseal(plan))
    index_path = tmp_path / 'index.json'
    write_once_json(index_path, index)
    monkeypatch.setattr('int05_tests.positive_campaign.check_pair',
                        lambda *a, **k: pytest.fail('invalid evidence set must not reach comparison'))
    with pytest.raises(ValueError):
        check_positive_campaign(checkpoint_path=inputs['output'] / 'checkpoint.json',
            expected_checkpoint_hash=anchor, evidence_index=index_path,
            expected_evidence_index_sha256=hash_file(index_path), output=tmp_path / 'comparison.json')
    assert not (tmp_path / 'comparison.json').exists()
