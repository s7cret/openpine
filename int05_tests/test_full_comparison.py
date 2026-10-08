"""Negative verifier tests only; the two-owner fixture is not qualification."""
import copy
from pathlib import Path

import pytest

from int05_tests.check_full_comparison import (
    check_pair, ensure_external_comparison_output, validate_pair_contract,
)
from openpine.verification.execution_binding import make_binding
from openpine.verification.execution_campaign import run_campaign
from openpine.verification.execution_identity import source_snapshot, write_once_json
from openpine.verification.execution_plan import make_plan, validate_plan
from openpine.verification.identity import digest
from rc6_tests.test_rc6_execution_platform import lock, reseal, tiny_plan


@pytest.mark.parametrize('omitted_fails', [False, True])
def test_full_failure_outside_affected_scope_blocks_trust(tmp_path, omitted_fails):
    seed, _ = tiny_plan(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'test_other.py').write_text('def test_value():\n    assert ' + str(not omitted_fails) + '\n')
    roots = {n: Path(p) for n, p in seed['roots'].items()} | {'other': other}
    source = source_snapshot(roots)
    environments = seed['environments']
    policy = {'components': {'tiny': {}, 'other': {}},
              'required_gates': {'stage-full': ['foundation']}}
    inventories = {}
    for owner, nodes in [('tiny', seed['tasks'][0]['nodeids']), ('other', ['test_other.py::test_value'])]:
        inventories[owner + '@py'] = {
            'nodeids': nodes, 'reviewed_lock': lock(nodes),
            'source_hash': source['content_hash'],
            'environment_hash': environments['py']['identity']['content_hash'],
        }
    plans = {}
    for profile in ('affected', 'stage-full'):
        plan = make_plan(profile=profile, policy=policy, roots=roots, source=source,
            environments=environments, inventories=inventories, requested=['tiny'])
        plans[profile] = plan
        path = tmp_path / (profile + '.json')
        write_once_json(path, plan)
        run_campaign(plan, path, tmp_path / profile, jobs=1, run_id=profile)
    result = check_pair(policy, plans['affected'], plans['stage-full'],
        affected_evidence=tmp_path / 'affected', full_evidence=tmp_path / 'stage-full',
        affected_hash=plans['affected']['content_hash'], full_hash=plans['stage-full']['content_hash'],
        affected_run='affected', full_run='stage-full', expected_owners=['tiny'])
    assert result['selection_trusted_for_this_scenario'] is (not omitted_fails)
    assert result['full_failed_while_affected_passed'] is omitted_fails
    assert result['full']['required_obligations'] == 3
    assert result['affected']['required_obligations'] == 2
    assert not result['full_acceptance']


@pytest.mark.parametrize('mutation', ['coverage-package', 'untraced-markers', 'shard-assignment', 'plugins-order', 'source-commits'])
def test_valid_plan_instrumentation_drift_rejected_before_evidence(tmp_path, monkeypatch, mutation):
    seed, _ = tiny_plan(tmp_path, profile='stage-full')
    full = copy.deepcopy(seed)
    full['tasks'][0]['coverage'] = True
    full['tasks'][0]['untraced_markers'] = ['performance']
    first = full['tasks'][0]['nodeids'][0]
    full['tasks'][0]['node_markers'] = {n: ['performance'] if n == first else []
                                      for n in full['tasks'][0]['nodeids']}
    for shard in full['tasks'][0]['shards']:
        shard['coverage'] = first not in shard['nodeids']
    affected = copy.deepcopy(full)
    affected['profile'] = 'affected'
    affected['required_gates'] = []
    if mutation == 'coverage-package':
        full['tasks'][0]['coverage_package'] = 'another_package'
    elif mutation == 'untraced-markers':
        full['tasks'][0]['untraced_markers'] = []
        for shard in full['tasks'][0]['shards']:
            shard['coverage'] = True
    elif mutation == 'plugins-order':
        affected['tasks'][0]['plugins'] = ['pytest_asyncio.plugin', 'pytest_cov.plugin']
        full['tasks'][0]['plugins'] = list(reversed(affected['tasks'][0]['plugins']))
    elif mutation == 'shard-assignment':
        # Older structurally valid task records can omit marker evidence.
        # Their actual per-node coverage flags still belong to the contract.
        del full['tasks'][0]['node_markers']
        del affected['tasks'][0]['node_markers']
        full['tasks'][0]['shards'][0]['coverage'] = True
        affected['tasks'][0]['shards'][0]['coverage'] = False
    else:
        affected['source_commits'] = {name: str(index) * 40
                                     for index, name in enumerate(affected['source']['components'], start=1)}
        full['source_commits'] = dict(affected['source_commits'])
        full['source_commits']['tiny'] = 'f' * 40
    affected, full = reseal(affected), reseal(full)
    assert validate_plan(affected) == affected
    assert validate_plan(full) == full
    # Keep the original independently frozen policy identity from tiny_plan.
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': ['3.13'],
                'smoke': ['test_a.py::test_value'], 'timeout_seconds': 30}},
              'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']}}
    assert digest(policy) == affected['policy_hash'] == full['policy_hash']
    observations = []
    monkeypatch.setattr('int05_tests.check_full_comparison.aggregate_campaign',
        lambda *a, **k: observations.append(a) or {'pytest_scope_passed': True})
    with pytest.raises(ValueError, match='source_commits' if mutation == 'source-commits' else 'instrumentation'):
        check_pair(policy, affected, full, affected_evidence=tmp_path / 'affected',
            full_evidence=tmp_path / 'full', affected_hash=affected['content_hash'],
            full_hash=full['content_hash'], affected_run='affected', full_run='full',
            expected_owners=['tiny'])
    assert observations == []


@pytest.mark.parametrize('omitted_from', ['full', 'affected', 'both'])
def test_each_selected_owner_retains_all_interpreter_tasks(tmp_path, omitted_from):
    seed, _ = tiny_plan(tmp_path, profile='stage-full')
    full = copy.deepcopy(seed)
    full['environments']['second'] = copy.deepcopy(full['environments']['py'])
    task = copy.deepcopy(full['tasks'][0])
    task.update(id='tiny@second', environment='second')
    full['tasks'].append(task)
    full = reseal(full)
    affected = copy.deepcopy(full)
    affected.update(profile='affected', required_gates=[])
    if omitted_from in {'full', 'both'}:
        full['tasks'].pop()
        full = reseal(full)
    if omitted_from in {'affected', 'both'}:
        affected['tasks'].pop()
    affected = reseal(affected)
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': ['3.13'],
                'smoke': ['test_a.py::test_value'], 'timeout_seconds': 30}},
              'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']}}
    with pytest.raises(ValueError, match='owner/interpreter task'):
        validate_pair_contract(policy, affected, full, affected_hash=affected['content_hash'],
            full_hash=full['content_hash'], expected_owners=['tiny'])


def test_required_interpreter_absent_from_both_plans_is_rejected(tmp_path):
    full, _ = tiny_plan(tmp_path, profile='stage-full')
    policy = {'components': {'tiny': {'pythons': ['3.13', '3.14']}},
              'required_gates': {'stage-full': ['foundation']}}
    full['policy_hash'] = digest(policy)
    full = reseal(full)
    affected = reseal({**full, 'profile': 'affected', 'required_gates': []})
    with pytest.raises(ValueError, match='required interpreter'):
        validate_pair_contract(policy, affected, full, affected_hash=affected['content_hash'],
            full_hash=full['content_hash'], expected_owners=['tiny'])


def test_declared_library_python312_scope_remains_comparable(tmp_path):
    full, _ = tiny_plan(tmp_path, profile='stage-full')
    policy = {'components': {'tiny': {'pythons': ['3.12']}},
              'required_gates': {'stage-full': ['foundation']}}
    full['policy_hash'] = digest(policy)
    identity = dict(full['environments']['py']['identity'])
    identity.update(python='3.12.9')
    full['environments']['py']['identity'] = reseal(identity)
    full = reseal(full)
    affected = reseal({**full, 'profile': 'affected', 'required_gates': []})
    assert validate_pair_contract(policy, affected, full, affected_hash=affected['content_hash'],
        full_hash=full['content_hash'], expected_owners=['tiny']) == ['tiny']


def test_comparison_output_cannot_write_into_relocated_source_binding(tmp_path):
    import shutil
    import sys

    full, _ = tiny_plan(tmp_path, profile='stage-full')
    roots = {n: Path(p) for n, p in full['roots'].items()}
    relocated = {}
    for name, root in roots.items():
        target = tmp_path / 'relocated' / name
        shutil.copytree(root, target)
        relocated[name] = target
    binding = make_binding(full, relocated, {'py': sys.executable})
    evidence = tmp_path / 'evidence'
    write_once_json(evidence / 'binding.json', binding)
    with pytest.raises(ValueError, match='outside source roots'):
        ensure_external_comparison_output(relocated['tiny'] / 'comparison.json', full, evidence)
    assert not (relocated['tiny'] / 'comparison.json').exists()


@pytest.mark.parametrize('field,value', [('timeout_seconds', 300), ('memory_mib', 512),
                                        ('private_retention', 'delete-on-success')])
def test_different_execution_settings_rejected_before_evidence(tmp_path, field, value):
    full, _ = tiny_plan(tmp_path, profile='stage-full')
    affected = copy.deepcopy(full)
    affected.update(profile='affected', required_gates=[])
    affected['tasks'][0][field] = value
    affected = reseal(affected)
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': ['3.13'],
                'smoke': ['test_a.py::test_value'], 'timeout_seconds': 30}},
              'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']}}
    with pytest.raises(ValueError, match='execution settings'):
        validate_pair_contract(policy, affected, full, affected_hash=affected['content_hash'],
            full_hash=full['content_hash'], expected_owners=['tiny'])
