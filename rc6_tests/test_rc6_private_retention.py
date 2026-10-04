"""Success-only retention uses real gated pytest shards, never synthetic success."""
import copy
import threading
from pathlib import Path

import pytest

from openpine.verification import execution_campaign as campaign
from openpine.verification.execution_identity import hash_file, write_once_json
from openpine.verification.execution_performance import workload_identity, validate_performance_scope
from openpine.verification.execution_plan import make_plan, validate_plan
from openpine.verification.identity import seal
from rc6_tests.test_rc6_execution_platform import tiny_plan, lock

def reseal(value):
    return seal({k: v for k, v in value.items() if k != 'content_hash'})


BODY = '''import os
from pathlib import Path
def test_value(tmp_path):
    (tmp_path / 'fixture').write_bytes(b'private fixture')
    owner = Path(os.environ['OPENPINE_STAGE1_EVIDENCE'])
    owner.mkdir()
    (owner / 'owner.json').write_bytes(b'{"owner": "independent"}')
'''


def opted_plan(tmp_path, *, body=BODY, timeout=30):
    plan, _ = tiny_plan(tmp_path, bodies={'test_a.py': body}, shards=1, timeout=timeout)
    plan['tasks'][0]['private_retention'] = 'delete-on-success'
    plan = reseal(plan)
    path = tmp_path / 'opted-plan.json'
    write_once_json(path, plan)
    return plan, path


def test_component_policy_freezes_retention_per_task(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    task = plan['tasks'][0]
    policy = {'components': {'tiny': {'pythons': ['3.13'], 'private_retention': 'delete-on-success'}}}
    inv = {'tiny@py': {'nodeids': task['nodeids'], 'reviewed_lock': lock(task['nodeids']), 'source_hash': plan['source']['content_hash'], 'environment_hash': plan['environments']['py']['identity']['content_hash']}}
    new = make_plan(profile='component', policy=policy, roots={k: Path(v) for k, v in plan['roots'].items()}, source=plan['source'], inventories=inv, environments=plan['environments'], requested=['tiny'])
    assert new['tasks'][0]['private_retention'] == 'delete-on-success'


@pytest.mark.parametrize('value', [True, None, 'delete-always'])
def test_invalid_retention_rejected(tmp_path, value):
    plan, _ = tiny_plan(tmp_path)
    plan['tasks'][0]['private_retention'] = value
    with pytest.raises(ValueError, match='private retention'):
        validate_plan(reseal(plan))


def test_performance_identity_binds_retention(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    opted = copy.deepcopy(plan)
    opted['tasks'][0]['private_retention'] = 'delete-on-success'
    opted = reseal(opted)
    assert workload_identity(plan) != workload_identity(opted)
    with pytest.raises(ValueError, match='full workload'):
        validate_performance_scope(plan, opted)
    explicit = copy.deepcopy(plan)
    explicit['tasks'][0]['private_retention'] = 'preserve'
    assert workload_identity(plan) == workload_identity(reseal(explicit))


def test_real_success_cleans_only_private_preserving_primary_readback(tmp_path, monkeypatch):
    plan, path = opted_plan(tmp_path)
    output = tmp_path / 'run'
    captured = {}
    cleanup = getattr(campaign, '_clear_owned_private', None)
    assert cleanup is not None, 'success-only owned private cleanup is missing'
    def observe(root, folder, artifacts):
        captured.update({k: (root / d['path']).read_bytes() for k, d in artifacts.items()})
        assert (folder / 'private' / 'pytest').is_dir()
        (folder / 'private' / 'external-link').symlink_to(sentinel.parent, target_is_directory=True)
        receipt = {'artifacts': artifacts, 'run_id': 'retention-success', 'attempt_id': 'a001'}
        task = plan['tasks'][0]
        before = campaign._validate_primary_artifacts(plan, root, task, task['shards'][0], receipt, None)
        cleanup(root, folder, artifacts)
        after = campaign._validate_primary_artifacts(plan, root, task, task['shards'][0], receipt, None)
        assert after == before
        assert {k: (root / d['path']).read_bytes() for k, d in artifacts.items()} == captured
    monkeypatch.setattr(campaign, '_clear_owned_private', observe)
    sentinel = tmp_path / 'external-sentinel'
    sentinel.write_bytes(b'untouched')
    run = campaign.run_campaign(plan, path, output, jobs=1, run_id='retention-success')
    attempt = run['attempts'][0]
    assert attempt['status'] == 'completed', attempt
    assert not (output / 'tiny@py/s000/a001/private').exists()
    assert sentinel.read_bytes() == b'untouched'
    assert 'owner:owner.json' in captured
    for desc in attempt['artifacts'].values():
        assert hash_file(output / desc['path']) == desc['sha256']
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert report['pytest_scope_passed'], report['errors']
    again = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert again == report


def test_default_preserves_real_success(tmp_path):
    plan, path = tiny_plan(tmp_path, bodies={'test_a.py': BODY}, shards=1)
    run = campaign.run_campaign(plan, path, tmp_path / 'run', jobs=1)
    assert run['attempts'][0]['status'] == 'completed'
    assert (tmp_path / 'run/tiny@py/s000/a001/private/pytest').is_dir()


@pytest.mark.parametrize('outcome', ['failed', 'timeout', 'cancelled'])
def test_unsuccessful_real_attempt_preserves_private(tmp_path, outcome):
    body = BODY + ('    assert False\n' if outcome == 'failed' else '    import time\n    time.sleep(20)\n')
    plan, path = opted_plan(tmp_path, body=body, timeout=2)
    output = tmp_path / 'run'
    task = plan['tasks'][0]
    event = threading.Event()
    timer = None
    if outcome == 'cancelled':
        def cancel_after_fixture():
            import time
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if list(output.glob('**/fixture')):
                    event.set()
                    return
                time.sleep(.02)
        timer = threading.Thread(target=cancel_after_fixture)
        timer.start()
    try:
        result = campaign._execute_shard(plan, path, output, task, task['shards'][0], 'failure-retention', event)
    finally:
        if timer:
            timer.join()
    assert result['status'] == outcome
    assert list(output.glob('**/private/pytest/**/fixture'))
    assert (output / 'tiny@py/s000/a001/execution.json').is_file()


def test_cleanup_failure_is_not_success(tmp_path, monkeypatch):
    plan, path = opted_plan(tmp_path)
    def denied(*args):
        raise OSError('cleanup denied')
    monkeypatch.setattr(campaign, '_clear_owned_private', denied, raising=False)
    run = campaign.run_campaign(plan, path, tmp_path / 'run', jobs=1)
    assert run['attempts'][0]['status'] != 'completed'
    assert 'cleanup denied' in run['attempts'][0]['error']
    assert (tmp_path / 'run/tiny@py/s000/a001/private/pytest').is_dir()


def test_invalid_primary_preserves_private(tmp_path, monkeypatch):
    plan, path = opted_plan(tmp_path)
    def invalid(*args):
        raise ValueError('invalid primary JUnit')
    monkeypatch.setattr(campaign, 'validate_junit', invalid)
    run = campaign.run_campaign(plan, path, tmp_path / 'run', jobs=1)
    assert run['attempts'][0]['status'] != 'completed'
    assert (tmp_path / 'run/tiny@py/s000/a001/private/pytest').is_dir()


@pytest.mark.parametrize('unsafe', ['private-symlink', 'ancestor-symlink', 'traversal', 'primary-reference', 'directory-reference'])
def test_cleanup_rejects_unsafe_roots_and_primary_references(tmp_path, unsafe):
    output = tmp_path / 'run'
    folder = output / 'tiny@py/s000/a001'
    private = folder / 'private'
    private.mkdir(parents=True)
    sentinel = tmp_path / 'external' / 'sentinel'
    sentinel.parent.mkdir()
    sentinel.write_bytes(b'preserved')
    fixture = private / 'fixture'
    fixture.write_bytes(b'preserved')
    artifacts = {}
    if unsafe == 'private-symlink':
        fixture.unlink()
        private.rmdir()
        private.symlink_to(sentinel.parent, target_is_directory=True)
    elif unsafe == 'ancestor-symlink':
        alias = tmp_path / 'alias'
        alias.symlink_to(output, target_is_directory=True)
        output = alias
        folder = alias / 'tiny@py/s000/a001'
    elif unsafe == 'traversal':
        folder = folder / '..' / 'a001'
    elif unsafe == 'primary-reference':
        artifacts['owner'] = {'path': fixture.relative_to(output).as_posix(), 'sha256': hash_file(fixture)}
    else:
        artifacts['owner'] = {'path': private.relative_to(output).as_posix(), 'sha256': 'unused'}
    cleanup = getattr(campaign, '_clear_owned_private', None)
    assert cleanup is not None, 'owned private cleanup guard is missing'
    with pytest.raises(ValueError):
        cleanup(output, folder, artifacts)
    assert sentinel.read_bytes() == b'preserved'
    if unsafe != 'private-symlink':
        assert fixture.read_bytes() == b'preserved'
