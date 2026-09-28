"""Positive and negative coordination/coverage/performance owner contracts."""
from __future__ import annotations
import copy
import os
import sys
from pathlib import Path
import pytest
from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_coverage import combine_task_coverage
from openpine.verification.execution_identity import source_snapshot, write_once_json
from openpine.verification.execution_performance import compare_campaigns, workload_identity
from openpine.verification.execution_preflight import optimizer_process_preflight
from openpine.verification.identity import read_json, seal
from rc6_tests.test_rc6_execution_platform import lock, reseal, tiny_plan

def collection(plan, env_id='py'):
    task = plan['tasks'][0]
    env = plan['environments']['py']
    return seal({'schema_id': 'openpine.test_collection_set.v1', 'roots': plan['roots'], 'source': plan['source'], 'policy_hash': plan['policy_hash'], 'environments': {env_id: env}, 'inventories': {'tiny@' + env_id: {'nodeids': task['nodeids'], 'deselected': 0, 'reviewed_lock': lock(task['nodeids']), 'source_hash': plan['source']['content_hash'], 'environment_hash': env['identity']['content_hash']}}, 'errors': [], 'ok': True, 'collect_only': True, 'execution_pass': False})

def test_join_collectors_retains_observed_interpreter_identities_not_fake_versions(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    first, second = (collection(plan), collection(plan, 'second-observation'))
    joined = join_collections([first, second], {n: Path(p) for n, p in plan['roots'].items()})
    assert joined['ok'] and joined['collect_only'] and (not joined['execution_pass'])
    assert len(joined['inventories']) == 2 and len(joined['origin_collection_hashes']) == 2
    assert {e['identity']['python'] for e in joined['environments'].values()} == {f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'}

@pytest.mark.parametrize('mutation', ['failed', 'wrong-policy', 'wrong-source', 'missing-root', 'empty-inventory', 'orphan-env', 'shrunk-nodes', 'duplicate-env', 'execution-pass'])
def test_join_rejects_unusable_collections(tmp_path, mutation):
    plan, _ = tiny_plan(tmp_path)
    receipt = collection(plan)
    if mutation == 'failed':
        receipt['ok'] = False
    elif mutation == 'wrong-policy':
        receipt['policy_hash'] = 'sha256:' + '0' * 64
    elif mutation == 'wrong-source':
        receipt['source']['content_hash'] = 'sha256:' + '0' * 64
    elif mutation == 'missing-root':
        receipt['roots'] = {'tiny': plan['roots']['tiny']}
    elif mutation == 'empty-inventory':
        receipt['inventories'] = {}
    elif mutation == 'orphan-env':
        receipt['inventories']['tiny@lost'] = receipt['inventories'].pop('tiny@py')
    elif mutation == 'shrunk-nodes':
        receipt['inventories']['tiny@py']['nodeids'] = receipt['inventories']['tiny@py']['nodeids'][:1]
    elif mutation == 'execution-pass':
        receipt['execution_pass'] = True
    roots = {n: Path(p) for n, p in plan['roots'].items()}
    baseline = collection(plan, 'baseline')
    if mutation == 'duplicate-env':
        baseline = collection(plan)
    with pytest.raises(ValueError):
        join_collections([baseline, reseal(receipt)], roots)

@pytest.mark.parametrize('children,pidfd,access,subreaper', [(None, 42, True, True), ([], None, False, True), ([], 42, False, True), ([], 42, True, False)])
def test_optimizer_preflight_rejects_missing_kernel_prerequisite(monkeypatch, children, pidfd, access, subreaper):
    from optimizer.core import process_containment as owner
    closed = []
    monkeypatch.setattr(owner, '_process_children', lambda pid: children)
    monkeypatch.setattr(owner, '_open_pidfd', lambda pid: pidfd)
    monkeypatch.setattr(owner, '_signal_process_handle', lambda fd, sig: access if sig == 0 else pytest.fail('no stop/kill in access probe'))
    monkeypatch.setattr(owner, 'enable_child_subreaper', lambda: subreaper)
    monkeypatch.setattr(owner, 'close_process_handles', lambda handles: closed.extend(handles))
    result = optimizer_process_preflight()
    assert not result['ok'] and result['errors']
    assert closed == ([(os.getpid(), 42)] if pidfd is not None else [])

def test_optimizer_preflight_accepts_supported_prerequisites_without_claiming_containment(monkeypatch):
    from optimizer.core import process_containment as owner
    monkeypatch.setattr(owner, '_process_children', lambda pid: [])
    monkeypatch.setattr(owner, '_open_pidfd', lambda pid: 42)
    monkeypatch.setattr(owner, '_signal_process_handle', lambda fd, sig: sig == 0)
    monkeypatch.setattr(owner, 'close_process_handles', lambda handles: None)
    monkeypatch.setattr(owner, 'enable_child_subreaper', lambda: True)
    result = optimizer_process_preflight()
    assert result['ok'] and (not result['errors'])
    assert 'does not prove trial containment' in result['scope']

@pytest.mark.parametrize('uncovered', [False, True])
def test_real_coverage_union_keeps_full_owner_threshold(tmp_path, uncovered):
    bodies = {'test_a.py': 'from tiny import left\ndef test_value():\n    assert left(1)==2\n', 'test_b.py': 'from tiny import right\ndef test_value():\n    assert right(2)==4\n'}
    plan, _ = tiny_plan(tmp_path, bodies=bodies)
    root = Path(plan['roots']['tiny'])
    (root / 'tiny').mkdir()
    code = 'def left(x):\n    return x+1\ndef right(x):\n    return x*2\n'
    if uncovered:
        code += 'def never_called():\n    return 99\n'
    (root / 'tiny/__init__.py').write_text(code)
    (root / 'pyproject.toml').write_text('[tool.coverage.run]\nsource=["tiny"]\n[tool.coverage.report]\nfail_under=100\n')
    plan['source'] = source_snapshot({n: Path(p) for n, p in plan['roots'].items()})
    plan['tasks'][0]['coverage'] = True
    for shard in plan['tasks'][0]['shards']:
        shard['coverage'] = True
    plan['tasks'][0]['coverage_package'] = 'tiny'
    plan = reseal(plan)
    path = tmp_path / 'coverage-plan.json'
    write_once_json(path, plan)
    run = run_campaign(plan, path, tmp_path / 'run', jobs=2, run_id='coverage')
    assert aggregate_campaign(plan, tmp_path / 'run', expected_plan_hash=plan['content_hash'], expected_run_id='coverage')['ok']
    result = combine_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'combined', run_id=run['run_id'])
    assert result['ok'] is (not uncovered), result
    totals = read_json(tmp_path / 'combined/coverage.json')['totals']
    assert (totals['percent_covered'] == 100) is (not uncovered)
    assert 'no lowered shard threshold' in result['threshold_policy']

def test_performance_rejects_fewer_than_five_samples():
    with pytest.raises(ValueError, match='five'):
        compare_campaigns([], [], reference_profile={'affinity_cpus': 2})

def test_ten_real_small_campaigns_prove_comparator_positive_path(tmp_path):
    plan, path = tiny_plan(tmp_path)
    before, after = ([], [])
    for index in range(10):
        output = tmp_path / ('run-' + str(index))
        run = run_campaign(plan, path, output, jobs=1 if index < 5 else 2, run_id='sample-' + str(index))
        (before if index < 5 else after).append((plan, output, run['run_id']))
    profile = {'affinity_cpus': len(os.sched_getaffinity(0)), 'fixture': 'synthetic verifier positive case'}
    result = compare_campaigns(before, after, reference_profile=profile)
    assert result['ok'] and result['metrics']['before']['n'] == 5
    assert len({r['run_hash'] for rows in result['samples'].values() for r in rows}) == 10
    assert result['p95'] is None and (not result['full_stage_accepted'])
    with pytest.raises(ValueError, match='same execution'):
        compare_campaigns(before, [after[0]] * 5, reference_profile=profile)
    with pytest.raises(ValueError, match='resource profile'):
        compare_campaigns(before, after, reference_profile={'affinity_cpus': 999})

def test_performance_workload_identity_includes_instrumentation(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    changed = copy.deepcopy(plan)
    changed['tasks'][0]['coverage'] = True
    for shard in changed['tasks'][0]['shards']:
        shard['coverage'] = True
    assert workload_identity(plan) != workload_identity(reseal(changed))
