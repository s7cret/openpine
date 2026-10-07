"""Focused disk admission/cancellation checks with genuine sealed tiny plans."""
import copy
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.verification import execution_campaign as campaign
from openpine.verification.execution_plan import validate_plan
from openpine.verification.execution_performance import workload_identity
from openpine.verification.identity import seal
from openpine.verification.execution_identity import write_once_json
from rc6_tests import test_rc6_execution_platform as platform


def planned(tmp_path, monkeypatch, guard=None, bodies=None, retention='preserve'):
    original = platform.make_plan
    def configured(**kwargs):
        policy = copy.deepcopy(kwargs['policy'])
        if guard is not None:
            policy['disk_free_guard'] = guard
        policy['components']['tiny']['private_retention'] = retention
        return original(**{**kwargs, 'policy': policy})
    with monkeypatch.context() as patch:
        patch.setattr(platform, 'make_plan', configured)
        return platform.tiny_plan(tmp_path, bodies=bodies)


def observe(monkeypatch, values):
    calls = []
    iterator = iter(values)
    last = values[-1]
    def sample(path):
        calls.append(Path(path))
        value = next(iterator, last)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(f_bavail=value, f_frsize=1, f_bfree=10**18)
    monkeypatch.setattr(os, 'statvfs', sample)
    return calls


def aggregate(plan, output, run):
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    write_once_json(output / 'aggregate.json', report)
    return report


def test_declared_floor_blocks_real_dispatch(tmp_path, monkeypatch):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    calls = observe(monkeypatch, [99])
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='disk-low')
    assert run['attempts'] == [], 'unsafe dispatch below independently declared disk floor'
    assert run['errors'] and calls == [output]
    assert run['disk_free_guard']['minimum_observed_free_bytes'] == 99
    assert not aggregate(plan, output, run)['ok']


@pytest.mark.parametrize('failure', ['low', 'unavailable'])
def test_live_floor_cancels_owned_process_preserves_private(tmp_path, monkeypatch, failure):
    marker = tmp_path / 'owned-started'
    body = f"import time, os\nfrom pathlib import Path\ndef test_value(tmp_path):\n    (tmp_path / 'valuable').write_text('evidence')\n    Path({str(marker)!r}).write_text(str(os.getpid()))\n    time.sleep(1.5)\n"
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100}, {'test_a.py': body, 'test_b.py': 'def test_value():\n    assert True\n'}, retention='delete-on-success')
    calls = []
    def sample(output):
        calls.append(str(output))
        if len(calls) <= 2:
            return SimpleNamespace(f_bavail=200, f_frsize=1)
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if failure == 'unavailable':
            raise OSError('disk observation denied')
        return SimpleNamespace(f_bavail=99, f_frsize=1)
    monkeypatch.setattr(os, 'statvfs', sample)
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='disk-active')
    assert marker.exists(), 'genuine owned child did not start'
    assert len(run['attempts']) == 1 and run['attempts'][0]['status'] == 'cancelled'
    import psutil
    assert not psutil.pid_exists(int(marker.read_text())), 'owned child remains alive after join'
    assert run['attempts'][0]['returncode'] < 0
    assert run['errors'] and len(calls) >= 3
    assert list(output.rglob('valuable')), 'cancelled private evidence was removed'
    assert list(output.rglob('stdout.log')) and list(output.rglob('execution.json'))
    assert not aggregate(plan, output, run)['ok']
    assert 'disk_free_guard' not in run['resource_profile']


@pytest.mark.parametrize('guard', [True, '100', {}, {'minimum_free_bytes': True}, {'minimum_free_bytes': '100'}, {'minimum_free_bytes': 0}, {'minimum_free_bytes': -1}, {'minimum_free_bytes': 1.5}, {'minimum_free_bytes': 100, 'extra': 1}])
def test_policy_rejects_malformed_guard(tmp_path, monkeypatch, guard):
    with pytest.raises(ValueError, match='disk'):
        planned(tmp_path, monkeypatch, guard)


def test_guard_frozen_validated_and_workload_bound(tmp_path, monkeypatch):
    plan, _ = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    assert plan['disk_free_guard'] == {'minimum_free_bytes': 100}
    changed = copy.deepcopy(plan)
    changed['disk_free_guard']['minimum_free_bytes'] = 101
    with pytest.raises(ValueError):
        validate_plan(changed)
    changed = seal({k: v for k, v in changed.items() if k != 'content_hash'})
    assert workload_identity(changed) != workload_identity(plan)
    changed['disk_free_guard'] = {'minimum_free_bytes': False}
    with pytest.raises(ValueError, match='disk'):
        validate_plan(seal({k: v for k, v in changed.items() if k != 'content_hash'}))


def test_absent_guard_preserves_legacy_no_probe(tmp_path, monkeypatch):
    plan, path = planned(tmp_path, monkeypatch)
    assert 'disk_free_guard' not in plan
    def forbidden(*args):
        raise AssertionError('legacy campaign probed disk')
    monkeypatch.setattr(os, 'statvfs', forbidden)
    run = campaign.run_campaign(plan, path, tmp_path / 'run', run_id='legacy')
    assert 'disk_free_guard' not in run and not run['errors']
    assert aggregate(plan, tmp_path / 'run', run)['ok']


def test_observation_unavailable_fails_closed_before_dispatch(tmp_path, monkeypatch):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    observe(monkeypatch, [OSError('unavailable')])
    run = campaign.run_campaign(plan, path, tmp_path / 'run', run_id='disk-error')
    assert not run['attempts'] and run['errors']
    assert run['disk_free_guard']['observation_errors'] == 1


def test_floor_equality_uses_available_blocks_and_repeats(tmp_path, monkeypatch):
    from openpine.verification.identity import read_json

    policy = read_json(platform.HOST / 'verification/execution-policy.json')
    guard = policy['disk_free_guard']
    floor = guard['minimum_free_bytes']
    assert type(floor) is int and floor == 3_000_000_000
    for available in (floor - 1, floor, floor + 1):
        case = tmp_path / str(available)
        case.mkdir()
        plan, path = planned(case, monkeypatch, guard)
        calls = observe(monkeypatch, [available])
        output = case / 'run'
        run = campaign.run_campaign(plan, path, output, run_id='disk-' + str(available))
        assert plan['disk_free_guard'] == guard
        assert run['disk_free_guard']['minimum_free_bytes'] == floor
        assert run['disk_free_guard']['samples'] == len(calls)
        assert run['disk_free_guard']['minimum_observed_free_bytes'] == available
        if available < floor:
            assert not run['attempts'] and run['errors'] and len(calls) == 1
            assert run['disk_free_guard']['tripped']
        else:
            assert run['attempts'] and not run['errors'] and len(calls) >= 4
            assert not run['disk_free_guard']['tripped']
        assert aggregate(plan, output, run)['ok'] is (available >= floor)


@pytest.mark.parametrize('mutation', ['missing', 'floor', 'tripped', 'no-samples'])
def test_aggregate_rejects_invalid_guard_diagnostics(tmp_path, monkeypatch, mutation):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    observe(monkeypatch, [200])
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='disk-diagnostics')
    changed = copy.deepcopy(run)
    if mutation == 'missing':
        del changed['disk_free_guard']
    elif mutation == 'floor':
        changed['disk_free_guard']['minimum_free_bytes'] = 1
    elif mutation == 'tripped':
        changed['disk_free_guard']['tripped'] = True
    else:
        changed['disk_free_guard']['samples'] = 0
    changed = seal({k: v for k, v in changed.items() if k != 'content_hash'})
    # Read injection isolates admission of malformed receipts; no immutable file overwritten.
    original = campaign.read_json
    monkeypatch.setattr(campaign, 'read_json', lambda p: changed if Path(p) == output / 'run.json' else original(p))
    report = aggregate(plan, output, changed)
    assert not report['ok'] and any('disk' in error for error in report['errors'])


def test_each_dispatch_rechecks_floor(tmp_path, monkeypatch):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    observe(monkeypatch, [200, 200, 99])
    monkeypatch.setattr('openpine.verification.execution_resources.resource_profile', lambda: {'cpu_slots': 2, 'memory_limit_bytes': None, 'affinity_cpus': [0, 1]})
    run = campaign.run_campaign(plan, path, tmp_path / 'run', jobs=2, run_id='disk-burst')
    assert len(run['attempts']) == 1 and run['errors']
    assert run['disk_free_guard']['samples'] == 3


def test_statvfs_multiplies_fragment_size(tmp_path, monkeypatch):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    monkeypatch.setattr(os, 'statvfs', lambda p: SimpleNamespace(f_bavail=25, f_frsize=4, f_bfree=0))
    run = campaign.run_campaign(plan, path, tmp_path / 'run', run_id='disk-blocks')
    assert not run['errors'] and run['disk_free_guard']['minimum_observed_free_bytes'] == 100


@pytest.mark.parametrize('observation', [SimpleNamespace(f_bavail=-1, f_frsize=4), SimpleNamespace(f_bavail=100, f_frsize=0), SimpleNamespace(f_bavail=True, f_frsize=4), SimpleNamespace()])
def test_invalid_observation_fails_closed(tmp_path, monkeypatch, observation):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100})
    monkeypatch.setattr(os, 'statvfs', lambda p: observation)
    run = campaign.run_campaign(plan, path, tmp_path / 'run', run_id='disk-malformed')
    assert not run['attempts'] and run['errors']
    assert run['disk_free_guard']['observation_errors'] == 1


def test_explicit_null_policy_guard_rejected(tmp_path, monkeypatch):
    original = platform.make_plan
    def configured(**kwargs):
        policy = copy.deepcopy(kwargs['policy'])
        policy['disk_free_guard'] = None
        return original(**{**kwargs, 'policy': policy})
    monkeypatch.setattr(platform, 'make_plan', configured)
    with pytest.raises(ValueError, match='disk'):
        platform.tiny_plan(tmp_path)
