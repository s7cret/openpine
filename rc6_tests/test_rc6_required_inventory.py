"""Native scope must retain separately required external test obligations."""
from __future__ import annotations

import json
import sys

import pytest

from openpine.verification.execution_identity import environment_snapshot, hash_file, source_snapshot
from openpine.verification.execution_plan import make_plan
from openpine.verification.identity import seal
from openpine.verification.pytest_gate import collection_hash


def fixture(tmp_path, mutation=None):
    root = tmp_path / 'tiny'
    root.mkdir()
    tests = root / 'test_a.py'
    tests.write_text('def test_native(): pass\ndef test_live(): pass\n')
    native = 'test_a.py::test_native'
    live = 'test_a.py::test_live'
    full = sorted([native, live])
    manifest = {'schema_id': 'openpine.required_external_inventory.v1',
                'component': 'tiny', 'gate': 'data', 'full_nodeids': full,
                'full_lock': {'count': 2, 'deselected': 0, 'sha256': collection_hash(full)},
                'pending_nodeids': [live], 'status': 'NOT_EXECUTED',
                'node_sources': {live: {'path': 'test_a.py', 'sha256': hash_file(tests)}}}
    if mutation == 'missing-live':
        manifest['pending_nodeids'] = []
    elif mutation == 'overlap-native':
        manifest['pending_nodeids'] = [native]
    elif mutation == 'foreign-live-source':
        manifest['node_sources'][live]['path'] = 'foreign.py'
    elif mutation == 'source-hash':
        manifest['node_sources'][live]['sha256'] = 'sha256:' + 'a' * 64
    elif mutation == 'reviewed-lock-shrink':
        manifest['full_lock'] = {'count': 1, 'deselected': 0, 'sha256': collection_hash([native])}
    elif mutation == 'claimed-success':
        manifest['status'] = 'PASS'
    path = root / 'required.json'
    path.write_text(json.dumps(seal(manifest)))
    descriptor = {'owner': 'tiny', 'path': 'required.json', 'sha256': hash_file(path)}
    if mutation == 'descriptor-traversal':
        descriptor['path'] = '../tiny/required.json'
    elif mutation == 'manifest-tamper':
        path.write_text(path.read_text() + ' ')
    env = environment_snapshot()
    source = source_snapshot({'tiny': root})
    policy = {'components': {'tiny': {'required_inventory': descriptor}},
              'required_gates': {'release-full': ['data']}}
    inventories = {'tiny@py': {'nodeids': [native], 'deselected': 1,
        'reviewed_lock': {'count': 1, 'deselected': 1, 'sha256': collection_hash([native])},
        'source_hash': source['content_hash'], 'environment_hash': env['content_hash']}}
    return dict(profile='component', policy=policy, roots={'tiny': root}, source=source,
                inventories=inventories, environments={'py': {'identity': env, 'executable': sys.executable}},
                requested=['tiny']), native, live


def test_nonlive_plan_retains_required_external_obligation_without_pass_credit(tmp_path):
    kwargs, native, live = fixture(tmp_path)
    plan = make_plan(**kwargs)
    assert plan['tasks'][0]['nodeids'] == [native]
    pending = plan['pending_required_inventories']['tiny']
    assert pending['nodeids'] == [live]
    assert pending['full_count'] == 2
    assert pending['status'] == 'NOT_EXECUTED'
    assert pending['gate'] == 'data'
    assert pending['execution_pass'] is False


@pytest.mark.parametrize('mutation', ['manifest-tamper', 'descriptor-traversal',
    'missing-live', 'overlap-native', 'foreign-live-source', 'source-hash',
    'reviewed-lock-shrink', 'claimed-success'])
def test_required_inventory_mutations_cannot_hide_external_obligations(tmp_path, mutation):
    kwargs, _, _ = fixture(tmp_path, mutation)
    with pytest.raises(ValueError):
        make_plan(**kwargs)
