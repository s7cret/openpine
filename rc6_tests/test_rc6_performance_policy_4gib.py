"""Bounded synthetic owner fixtures, NOT measurements or execution admission.

The real planner and raw-primary comparison/admission readers run, but no
campaign, pytest worker, benchmark, calibration or package installation runs.
Only newly owned fixture receipts are synthesized/mutated, never history.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from openpine.verification.execution_campaign import descriptor
from openpine.verification.execution_identity import environment_snapshot, source_snapshot
from openpine.verification.execution_plan import make_plan
from openpine.verification.identity import seal
from openpine.verification.pytest_gate import collection_hash
from openpine.verification import stabilization_evidence as owner

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024**2


def policy():
    return json.loads((ROOT / 'verification/execution-policy.json').read_text())


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n')


def synthesize(plan, root, name, organization):
    """Handwritten raw-primary fixture costs; no command is executed."""
    limits = {'jobs': 4, 'max_parallel_shards': 1 if organization == 'before' else 4}
    profile = {'cpu_quota': 4.0, 'memory_limit_bytes': 4096*MIB,
               'address_space_limit_bytes': 3072*MIB,
               'cpu_frequency_max_khz': [1800000]*4, 'affinity_cpus': 4,
               'fixture': 'SYNTHETIC_NOT_MEASURED'}
    attempts = []
    index = 0
    for task in plan['tasks']:
        for shard in task['shards']:
            folder = root / task['id'] / shard['id'] / 'a001'
            folder.mkdir(parents=True)
            binding = {'task': task['id'], 'shard': shard['id'], 'suite': task['component'],
                       'plan_hash': plan['content_hash'], 'run_id': name, 'attempt_id': 'a001',
                       'candidate_hash': plan['source']['content_hash'],
                       'source_before': plan['source']['content_hash'],
                       'source_after': plan['source']['content_hash'],
                       'environment_hash': plan['environments'][task['environment']]['identity']['content_hash'],
                       **{k: task[k] for k in ('variant', 'execution_path', 'mode')}}
            phases = seal({'schema_id': 'openpine.test_inventory.v2', **binding,
                           'binding_hash': None, 'coverage': False,
                           'node_markers': {n: task['node_markers'][n] for n in shard['nodeids']},
                           'ok': True, 'collect_only': False, 'exitstatus': 0, 'errors': [],
                           'deselected': 0, 'nodeids': shard['nodeids'], 'count': len(shard['nodeids']),
                           'sha256': collection_hash(shard['nodeids']),
                           'reports': {n: [{'when': p, 'outcome': 'passed', 'xfail': False,
                                            'duration': 0.001} for p in ('setup', 'call', 'teardown')]
                                       for n in shard['nodeids']},
                           'resources': {'cpu_seconds': 0.01, 'children_cpu_seconds': 0.0}})
            put(folder/'phases.json', phases)
            suite = ET.Element('testsuite', tests=str(len(shard['nodeids'])), failures='0', errors='0', skipped='0')
            for n in shard['nodeids']:
                case = ET.SubElement(suite, 'testcase', name=n)
                props = ET.SubElement(case, 'properties')
                ET.SubElement(props, 'property', name='openpine.nodeid', value=n)
            ET.ElementTree(suite).write(folder/'junit.xml')
            (folder/'stdout.log').write_text('SYNTHETIC_NOT_MEASURED; no command executed\n')
            (folder/'nodeids.args').write_text('\n'.join(shard['nodeids'])+'\n')
            argv = [sys.executable, '-m', 'pytest', '-p', 'openpine.verification.pytest_gate']
            argv += ['--verification-'+k.replace('_','-')+'='+str(v) for k,v in binding.items()
                     if k in ('plan_hash', 'suite', 'task', 'shard', 'run_id', 'attempt_id')]
            attempt = {'task': task['id'], 'shard': shard['id'], 'attempt_id': 'a001',
                       'run_id': name, 'binding_hash': None, 'status': 'completed', 'returncode': 0,
                       'cwd': plan['roots'][task['component']], 'argv': argv,
                       'started_at': f'2026-01-01T00:{index:02}:00+00:00',
                       'finished_at': f'2026-01-01T00:{index:02}:01+00:00',
                       'artifacts': {k: descriptor(root, folder/v) for k,v in
                                     [('phases','phases.json'),('junit','junit.xml'),
                                      ('stdout','stdout.log'),('selectors','nodeids.args')]}}
            put(folder/'execution.json', attempt)
            attempts.append(attempt)
            index += 1
    floor = plan['disk_free_guard']['minimum_free_bytes']
    run = seal({'schema_id': 'openpine.test_campaign_run.v1', 'run_id': name,
                'plan_hash': plan['content_hash'],
                **{k: plan['source']['content_hash'] for k in ('candidate_hash','source_before','source_after')},
                **limits, 'resource_profile': profile, 'affinity_cpus': 4,
                'sampled_process_tree_peak_rss_bytes': 1, 'attempts': attempts, 'errors': [],
                'wall_seconds': 20.0 if organization == 'before' else 5.0,
                'disk_free_guard': {'minimum_free_bytes': floor, 'minimum_observed_free_bytes': floor+1,
                                    'samples': 1, 'observation_errors': 0, 'tripped': False,
                                    'storage_error': None,
                                    'observations': [{'observed_at': 'synthetic', 'free_bytes': floor+1, 'error': None}]}})
    put(root/'run.json', run)


@pytest.fixture
def case(tmp_path):
    original = policy()
    # Test-only miniature input projection: one actual interpreter and four
    # inert node IDs per component. Reservations/guard/retention remain real.
    bounded = copy.deepcopy(original)
    bounded['schema_id'] = 'openpine.execution_policy.v1'
    bounded.pop('owner_environment_slots', None)
    env = environment_snapshot()
    roots = {}
    nodes = [f'test_{i}.py::test_value' for i in range(4)]
    for name, settings in bounded['components'].items():
        settings['pythons'] = ['.'.join(env['python'].split('.')[:2])]
        root = tmp_path/'source'/name
        root.mkdir(parents=True)
        (root/'fixture.txt').write_text('SYNTHETIC_NOT_MEASURED\n')
        roots[name] = root
    source = source_snapshot(roots)
    inventories = {name+'@py': {'nodeids': nodes, 'deselected': 0,
                               'reviewed_lock': {'count': 4, 'sha256': collection_hash(nodes), 'deselected': 0},
                               'source_hash': source['content_hash'], 'environment_hash': env['content_hash']}
                   for name in roots}
    plans = {organization: make_plan(profile='stage-full', policy=bounded, roots=roots,
                                    source=source, inventories=inventories,
                                    environments={'py': {'identity': env, 'executable': sys.executable}},
                                    coverage=False, shard_count=count)
             for organization,count in [('before',1),('after',4)]}
    supplied = {'before': [], 'after': []}
    evidence = tmp_path/'evidence'
    for organization, plan in plans.items():
        plan_path = evidence/(organization+'-plan.json')
        put(plan_path, plan)
        for i in range(5):
            name = organization+str(i)
            root = evidence/name
            synthesize(plan, root, name, organization)
            supplied[organization].append({'plan': descriptor(evidence,plan_path), 'campaign': name, 'run_id': name})
    return plans, evidence, supplied, original['stabilization']['test-performance']


def test_current_policy_freezes_equal_4096_mib():
    spec = policy()['stabilization']['test-performance']
    assert {v['memory_limit_bytes'] for v in spec['organization_limits'].values()} == {4096*MIB}
    assert '4096MiB MemoryMax' in spec['organizations']['resource_policy']
    assert spec['target_speedup'] == 2.0
    for organization, parallel in [('before', 1), ('after', 4)]:
        assert spec['organization_limits'][organization] == {
            'address_space_limit_bytes': 3072*MIB,
            'cpu_frequency_max_khz': 1800000, 'cpu_quota': 4.0,
            'jobs': 4, 'max_parallel_shards': parallel,
            'max_shards_per_task': parallel, 'memory_limit_bytes': 4096*MIB,
        }
    assert spec['reference_profile'] == {'affinity_cpus': 4}


def test_real_planner_and_owner_accept_exact_4gib_synthetic_packet(case):
    plans, root, supplied, spec = case
    before_bytes = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    report = owner.verify_performance(plans['after'], root, supplied, spec)
    assert report['target_met'] and report['wall_speedup'] == 4.0
    assert not report['full_stage_accepted']
    assert all(report['metrics'][o]['n'] == 5 for o in supplied)
    assert len({r['run_hash'] for rows in report['samples'].values() for r in rows}) == 10
    assert {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before_bytes
    for plan in plans.values():
        optimizer = next(t for t in plan['tasks'] if t['component']=='optimizer')
        assert (optimizer['cpu_slots'], optimizer['exclusive_group']) == (2, 'optimizer-trials')
        assert all(t['private_retention']=='delete-on-success' for t in plan['tasks'])
        assert plan['disk_free_guard'] == {'minimum_free_bytes': 2147483648}


@pytest.mark.parametrize('organization', ['before','after'])
@pytest.mark.parametrize('field,value', [('memory_limit_bytes',2048*MIB),
                                         ('address_space_limit_bytes',4096*MIB),
                                         ('cpu_quota',2.0), ('cpu_frequency_max_khz',[2000000])])
def test_owner_rejects_old_memory_and_unchanged_resource_drift(case, organization, field, value):
    plans, root, supplied, spec = case
    path = root/supplied[organization][0]['campaign']/'run.json'
    raw = json.loads(path.read_text())
    raw['resource_profile'][field] = value
    raw.pop('content_hash')
    put(path, seal(raw))
    with pytest.raises(ValueError, match='resource'):
        owner.verify_performance(plans['after'], root, supplied, spec)


@pytest.mark.parametrize('fault', ['four-before','four-after','duplicate','slow','incomplete','traced',
                                  'guard','retention','parallel','jobs','affinity','shard-layout','before-shard-layout',
                                  'cross-component','optimizer-exclusive'])
def test_owner_keeps_existing_admission_obligations(case, fault):
    plans, root, supplied, spec = case
    candidate = plans['after']
    if fault.startswith('four-'):
        supplied[fault.split('-')[1]].pop()
    elif fault == 'duplicate':
        supplied['after'][-1] = supplied['after'][0]
    elif fault == 'before-shard-layout':
        supplied['before'][0]['plan'] = supplied['after'][0]['plan']
    elif fault == 'slow':
        for row in supplied['after']:
            path = root/row['campaign']/'run.json'
            raw = json.loads(path.read_text()); raw.pop('content_hash'); raw['wall_seconds']=15.0
            put(path,seal(raw))
    elif fault in {'traced','retention','shard-layout'}:
        plan = copy.deepcopy(plans['after']); plan.pop('content_hash')
        if fault == 'traced':
            for t in plan['tasks']:
                t['coverage']=True
                for s in t['shards']: s['coverage']=True
        elif fault == 'retention':
            plan['tasks'][0]['private_retention']='preserve'
        else:
            spec = copy.deepcopy(spec); spec['organization_limits']['after']['max_shards_per_task']=1
        if fault != 'shard-layout':
            path = root/'after-plan.json'; put(path,seal(plan))
            for row in supplied['after']: row['plan']=descriptor(root,path)
    else:
        path = root/supplied['after'][0]['campaign']/'run.json'
        raw = json.loads(path.read_text()); raw.pop('content_hash')
        if fault == 'incomplete': raw['attempts'].pop()
        elif fault == 'guard': raw.pop('disk_free_guard')
        elif fault == 'parallel': raw['max_parallel_shards']=1
        elif fault == 'jobs': raw['jobs']=3
        elif fault == 'affinity': raw['affinity_cpus']=2
        else:
            if fault == 'optimizer-exclusive':
                attempts = [a for a in raw['attempts'] if a['task'].startswith('optimizer@')][:2]
            else:
                attempts = [raw['attempts'][0], next(a for a in raw['attempts'] if a['task'] != raw['attempts'][0]['task'])]
            for a in attempts:
                a['started_at']='2026-01-01T00:00:00+00:00'
                a['finished_at']='2026-01-01T00:00:01+00:00'
        put(path,seal(raw))
    with pytest.raises(ValueError):
        owner.verify_performance(candidate, root, supplied, spec)
