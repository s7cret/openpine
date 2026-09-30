"""Independent-review admission regressions, using real primary receipts."""
import copy
import json
import os
import sys

import pytest

from openpine.verification import stabilization_evidence as owner
from openpine.verification.execution_identity import hash_file
from openpine.verification.execution_process import run_logged
from rc6_tests.test_rc6_execution_platform import tiny_plan, reseal
from rc6_tests.test_rc6_stabilization import full_fixture, replay


@pytest.mark.parametrize('mutation', ['missing-provenance', 'changed-capture', 'missing-capture'])
def test_frozen_helper_primary_bytes_are_required(tmp_path, mutation):
    helper = tmp_path / 'helper.py'
    helper.write_text('print(42)\n')
    inputs = {'helper': {'path': str(helper), 'sha256': hash_file(helper)}}
    argv = [sys.executable, str(helper)]
    folder = tmp_path / 'receipt'
    receipt = run_logged(argv, cwd=tmp_path, output=folder, env={'PATH': os.environ['PATH']}, inputs=inputs)
    spec = {'argv': argv, 'cwd': str(tmp_path), 'inputs': inputs}
    assert owner.verify_command(folder, spec)['ok']
    capture = folder / receipt['input_provenance']['helper']['captured']['path']
    if mutation == 'missing-provenance': receipt.pop('input_provenance')
    elif mutation == 'changed-capture':
        capture.write_text('print(41)\n')
        receipt['files'][capture.name] = hash_file(capture)
        receipt['input_provenance']['helper']['captured']['sha256'] = hash_file(capture)
    else: capture.unlink()
    (folder / 'command.json').write_text(json.dumps(reseal(receipt)))
    with pytest.raises((ValueError, OSError)):
        owner.verify_command(folder, spec)


@pytest.mark.parametrize('mutation', ['plan', 'candidate', 'staged-source', 'foreign-artifact', 'unbound-producer'])
def test_frontend_rejects_stale_or_foreign_primary(full_fixture, mutation):
    host, plan, evidence, packet = full_fixture
    entry = copy.deepcopy(packet['gates']['frontend'])
    policy = json.loads((host / 'verification/execution-policy.json').read_text())['stabilization']['frontend']
    originals = {}
    if mutation in {'plan', 'candidate'}:
        entry['binding'][mutation + '_hash'] = 'foreign'
    elif mutation == 'staged-source':
        source = host / 'openpine-ui/package.json'
        originals[source] = source.read_bytes()
        source.write_text('{"name":"stale-ui"}')
    elif mutation == 'foreign-artifact':
        entry['outputs'][0] = entry['tests']
    else:
        path = evidence / entry['commands'][0]['path']
        originals[path] = path.read_bytes()
        command = json.loads(path.read_text())
        command['binding']['plan_hash'] = 'foreign'
        path.write_text(json.dumps(reseal(command)))
        entry['commands'][0]['sha256'] = hash_file(path)
    try:
        with pytest.raises(ValueError, match='frontend'):
            owner.verify_frontend(plan, evidence, entry, policy)
    finally:
        for path, data in originals.items(): path.write_bytes(data)


def test_resealed_broken_rebuilt_wheel_is_rejected(full_fixture):
    import zipfile
    _, _, evidence, packet = full_fixture
    entry = copy.deepcopy(packet)
    package = next(iter(entry['gates']['packages'].values()))['rebuilt']
    desc = package['artifacts']['openpine']['wheel']
    path = evidence / desc['path']
    original = path.read_bytes()
    packet_path = evidence / 'stabilization-inputs.json'
    original_packet = packet_path.read_bytes()
    try:
        with zipfile.ZipFile(path) as archive:
            members = {n: archive.read(n) for n in archive.namelist()}
        members['openpine/__init__.py'] = b'raise RuntimeError("broken rebuilt wheel")\n'
        with zipfile.ZipFile(path, 'w') as archive:
            for n, data in members.items(): archive.writestr(n, data)
        desc['sha256'] = hash_file(path)
        packet_path.write_text(json.dumps(entry))
        verdict = replay(full_fixture)
        assert verdict['stabilization']['gates']['packages']['status'] == 'blocked', verdict
        assert not verdict['ok']
    finally:
        path.write_bytes(original)
        packet_path.write_bytes(original_packet)


@pytest.mark.parametrize('mutation', ['duplicate', 'traversal', 'symlink', 'oversize'])
def test_wheel_member_reader_rejects_unsafe_archive(tmp_path, mutation):
    import stat
    import zipfile
    path = tmp_path / 'unsafe.whl'
    with zipfile.ZipFile(path, 'w') as archive:
        if mutation == 'duplicate':
            archive.writestr('module.py', b'one')
            archive.writestr('module.py', b'two')
        elif mutation == 'traversal': archive.writestr('../module.py', b'one')
        elif mutation == 'symlink':
            info = zipfile.ZipInfo('module.py'); info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'/outside')
        else: archive.writestr('module.py', b'123456789')
    with pytest.raises(ValueError, match='wheel'):
        owner.wheel_members(path, max_bytes=8)



def test_deleted_live_input_produces_a_failed_primary_receipt(tmp_path):
    helper = tmp_path / 'helper.py'
    helper.write_text('from pathlib import Path; Path(__file__).unlink(); print(42)\n')
    inputs = {'helper': {'path': str(helper), 'sha256': hash_file(helper)}}
    folder = tmp_path / 'receipt'
    result = run_logged([sys.executable, str(helper)], cwd=tmp_path, output=folder,
                        env={'PATH': os.environ['PATH']}, inputs=inputs)
    assert result['status'] == 'failed' and not result['ok']
    assert 'input provenance changed' in result['error']
    assert json.loads((folder / 'command.json').read_text()) == result


@pytest.mark.parametrize('field,value', [('cpu_slots', 2), ('memory_mib', 999), ('exclusive_group', 'foreign')])
def test_reservation_drift_is_not_the_same_workload(tmp_path, field, value):
    from openpine.verification.execution_performance import validate_performance_scope
    plan, _ = tiny_plan(tmp_path)
    changed = copy.deepcopy(plan)
    changed['tasks'][0][field] = value
    with pytest.raises(ValueError):
        validate_performance_scope(plan, reseal(changed))


def test_unbound_helper_is_not_admitted(tmp_path):
    helper = tmp_path / 'helper.py'
    helper.write_text('print(42)\n')
    argv = [sys.executable, str(helper)]
    folder = tmp_path / 'receipt'
    run_logged(argv, cwd=tmp_path, output=folder, env={'PATH': os.environ['PATH']})
    spec = {'argv': argv, 'cwd': str(tmp_path), 'inputs': {'helper': {'path': str(helper), 'sha256': hash_file(helper)}}}
    with pytest.raises(ValueError, match='input provenance'):
        owner.verify_command(folder, spec)


def test_missing_rebuilt_installation_is_not_admitted():
    with pytest.raises(ValueError, match='normal and rebuilt'):
        owner.verify_packages({}, None, {'commands': []}, {'commands': []})


@pytest.mark.parametrize('mutation', ['jobs', 'shards', 'memory', 'quota', 'address-space', 'frequency', 'missing'])
def test_performance_organization_rejects_budget_drift(tmp_path, mutation):
    plan, _ = tiny_plan(tmp_path)
    profile = {'cpu_quota': 4.0, 'memory_limit_bytes': 2048 * 1024**2,
               'address_space_limit_bytes': 3072 * 1024**2, 'cpu_frequency_max_khz': [1800000]}
    raw = {'jobs': 4, 'max_parallel_shards': 4, 'resource_profile': profile}
    spec = {'jobs': 4, 'max_parallel_shards': 4, 'max_shards_per_task': 2, 'cpu_quota': 4.0,
            'memory_limit_bytes': 2048 * 1024**2, 'address_space_limit_bytes': 3072 * 1024**2,
            'cpu_frequency_max_khz': 1800000}
    if mutation == 'jobs': raw['jobs'] = 3
    elif mutation == 'shards': spec['max_shards_per_task'] = 1
    elif mutation == 'memory': profile['memory_limit_bytes'] *= 2
    elif mutation == 'quota': profile['cpu_quota'] = None
    elif mutation == 'address-space': profile['address_space_limit_bytes'] = None
    elif mutation == 'frequency': profile['cpu_frequency_max_khz'] = [2000000]
    else: raw.pop('resource_profile')
    with pytest.raises(ValueError, match='organization|resource'):
        owner.validate_organization(plan, raw, spec)


def test_frontend_requires_plan_binding(tmp_path):
    # Empty commands were already rejected, but an unbound candidate must be
    # rejected first, independently of successful-looking command inventories.
    with pytest.raises(ValueError, match='frontend candidate'):
        owner.verify_frontend({'content_hash': 'current'}, tmp_path, {'commands': []}, {'commands': []})
