"""INT05 boundary contracts. Small fixtures prove guards, not product qualification."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_plan import make_plan, select_components, validate_plan
from openpine.verification.identity import digest
from rc6_tests.test_rc6_execution_coordination import collection
from rc6_tests.test_rc6_execution_platform import lock, reseal, tiny_plan

HOST = Path(__file__).resolve().parents[1]
OWNERS = ['ast2python', 'backtest_engine', 'marketdata-provider', 'openpine',
          'openpine-contracts', 'optimizer', 'pine2ast', 'pinelib']


@pytest.fixture
def policy():
    return json.loads((HOST / 'verification/execution-policy.json').read_text())


@pytest.mark.parametrize('change,expected', [
    ('optimizer/core/objective.py', ['openpine', 'optimizer']),
    ('ast2python/compiler.py', ['ast2python', 'openpine']),
    ('pine2ast/pine2ast/parser.py', ['ast2python', 'openpine', 'pine2ast']),
    ('pinelib/pinelib/core/values.py', ['ast2python', 'backtest_engine', 'openpine', 'optimizer', 'pinelib']),
    ('backtest_engine/backtest_engine/core/fill_execution.py',
     ['backtest_engine', 'openpine', 'optimizer']),
    ('pinelib/tests/fixtures/ema_first_source_v1_checkpoints.json', OWNERS),
    ('openpine/tests/rc4_fixtures.py', OWNERS),
    ('openpine-contracts/openpine_contracts/schemas/openpine.execution_event.v1.json', OWNERS),
    ('pinelib/pinelib/abi/runtime_surface.json', OWNERS),
    ('pine2ast/pine2ast/catalog.py', OWNERS),
    ('pinelib/pinelib/state/checkpoint.py', OWNERS),
    ('ast2python/scripts/generate.py', OWNERS),
    ('openpine/openpine/verification/execution_plan.py', OWNERS),
    ('unknown/deleted.py', OWNERS),
])
def test_real_policy_boundaries_have_independent_expected_scope(policy, change, expected):
    # The expected lists are reviewed independently of the selection algorithm.
    assert select_components(policy, 'affected', [], [change])[0] == expected


@pytest.mark.parametrize('change', [
    'optimizer/', 'optimizer//core/objective.py', 'optimizer/./core/objective.py',
    'optimizer/../openpine/source.py', 'optimizer/C:/source.py',
    'optimizer/core\\objective.py', 'optimizer/core/objective.py\n',
    'optimizer/core/objective.py\x00', 42, None,
])
def test_ambiguous_changed_selector_cannot_narrow(policy, change):
    assert select_components(policy, 'affected', [], [change])[0] == OWNERS


@pytest.mark.parametrize('field,value', [
    ('requested', 'optimizer'), ('changes', 'optimizer/core/objective.py'),
    ('requested', None), ('changes', None),
])
def test_selector_containers_fail_closed(policy, field, value):
    args = {'requested': [], 'changes': ['optimizer/core/objective.py']}
    args[field] = value
    with pytest.raises(ValueError):
        select_components(policy, 'affected', **args)


@pytest.mark.parametrize('profile', ['smoke', 'component', 'affected'])
def test_scoped_profile_requires_selector(policy, profile):
    with pytest.raises(ValueError):
        select_components(policy, profile, [])


@pytest.mark.parametrize('profile', ['integration', 'stage-full', 'release-full'])
def test_full_boundaries_ignore_narrowing_request(policy, profile):
    assert select_components(policy, profile, ['optimizer'])[0] == OWNERS


@pytest.mark.parametrize('field,value', [
    ('dependencies', 'a'), ('dependencies', ['a', 'a']), ('dependencies', [None]),
])
def test_dependency_policy_cannot_silently_change_scope(field, value):
    candidate = {'components': {'a': {}, 'b': {field: value}}}
    with pytest.raises(ValueError):
        select_components(candidate, 'affected', ['b'])


def test_missing_changed_file_conservatively_selects_full_inventory(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    # The runner source is a real prerequisite root but not this policy's owner.
    policy = {'components': {'tiny': {}}, 'required_gates': {}}
    inventory = {'tiny@py': {
        'nodeids': plan['tasks'][0]['nodeids'],
        'reviewed_lock': lock(plan['tasks'][0]['nodeids']),
        'source_hash': plan['source']['content_hash'],
        'environment_hash': plan['environments']['py']['identity']['content_hash'],
    }}
    result = make_plan(profile='affected', policy=policy,
        roots={n: Path(p) for n, p in plan['roots'].items()}, source=plan['source'],
        inventories=inventory, environments=plan['environments'], changes=['tiny/deleted.py'])
    assert result['tasks'][0]['nodeids'] == plan['tasks'][0]['nodeids']
    assert any('missing' in reason for reason in result['selection_reasons'])


@pytest.mark.parametrize('profile', ['component', 'affected', 'integration', 'stage-full', 'release-full'])
def test_original_full_inventory_hash_rejects_shrunk_obligations(tmp_path, profile):
    plan, _ = tiny_plan(tmp_path, profile='stage-full' if profile in {'stage-full', 'release-full'} else 'component')
    plan['profile'] = profile
    task = plan['tasks'][0]
    task['nodeids'] = task['nodeids'][:1]
    task['nodeids_hash'] = digest(task['nodeids'])
    task['node_markers'] = {n: [] for n in task['nodeids']}
    task['shards'] = [task['shards'][0]]
    task['shards'][0]['nodeids'] = task['nodeids']
    with pytest.raises(ValueError, match='full inventory'):
        validate_plan(reseal(plan))


def test_coordinated_rehash_requires_the_independently_frozen_plan_anchor(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    expected = plan['content_hash']
    task = plan['tasks'][0]
    task['nodeids'] = task['nodeids'][:1]
    task['nodeids_hash'] = task['full_inventory_hash'] = digest(task['nodeids'])
    task['reviewed_lock_hash'] = digest(lock(task['nodeids']))
    task['node_markers'] = {n: [] for n in task['nodeids']}
    task['shards'] = [task['shards'][0]]
    task['shards'][0]['nodeids'] = task['nodeids']
    forged = reseal(plan)
    # Structural self-consistency is not independent inventory authenticity.
    assert validate_plan(forged) == forged
    with pytest.raises(ValueError, match='identity mismatch'):
        validate_plan(forged, expected_hash=expected)


def test_preparation_cannot_also_be_a_test_obligation(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    plan['preparation_components'] = ['tiny']
    with pytest.raises(ValueError, match='preparation'):
        validate_plan(reseal(plan))


def test_observed_runtime_boundary_is_preparation_for_component_and_obligation_for_producer(tmp_path):
    seed, _ = tiny_plan(tmp_path)
    from openpine.verification.execution_identity import source_snapshot
    roots = {}
    for owner in ('backtest_engine', 'pinelib'):
        root = tmp_path / owner
        root.mkdir()
        (root / 'source.py').write_text('VALUE = 1\n')
        roots[owner] = root
    source = source_snapshot(roots)
    nodes = ['test_value.py::test_value']
    inventories = {owner + '@py': {
        'nodeids': nodes, 'reviewed_lock': lock(nodes),
        'source_hash': source['content_hash'],
        'environment_hash': seed['environments']['py']['identity']['content_hash'],
    } for owner in roots}
    policy = {'components': {owner: {} for owner in roots}}
    component = make_plan(profile='component', policy=policy, roots=roots, source=source,
        inventories=inventories, environments=seed['environments'], requested=['backtest_engine'])
    assert [t['component'] for t in component['tasks']] == ['backtest_engine']
    assert component['preparation_components'] == ['pinelib']
    affected = make_plan(profile='affected', policy=policy, roots=roots, source=source,
        inventories=inventories, environments=seed['environments'], changes=['pinelib/source.py'])
    assert [t['component'] for t in affected['tasks']] == ['backtest_engine', 'pinelib']
    assert affected['preparation_components'] == []


@pytest.mark.parametrize('mutation', ['unused-environment', 'unsafe-node', 'markers', 'policy-hash'])
def test_join_does_not_hide_incomplete_collection_evidence(tmp_path, mutation):
    plan, _ = tiny_plan(tmp_path)
    receipt = collection(plan)
    if mutation == 'unused-environment':
        receipt['environments']['unused'] = copy.deepcopy(receipt['environments']['py'])
    elif mutation == 'policy-hash':
        receipt['policy_hash'] = None
    else:
        inventory = receipt['inventories']['tiny@py']
        if mutation == 'unsafe-node':
            inventory['nodeids'] = ['../test_escape.py::test_value']
            inventory['reviewed_lock'] = lock(inventory['nodeids'])
        else:
            inventory['node_markers'] = {}
    with pytest.raises(ValueError):
        join_collections([reseal(receipt)], {n: Path(p) for n, p in plan['roots'].items()})
