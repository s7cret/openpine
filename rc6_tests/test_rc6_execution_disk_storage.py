"""Bounded disk receipts: real scheduler/subprocess plus adversarial readback."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.verification import execution_campaign as campaign
from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import seal
from rc6_tests.test_rc6_execution_disk import planned, observe, aggregate


@pytest.mark.parametrize('field', ['minimum_free_bytes', 'minimum_observed_free_bytes'])
@pytest.mark.parametrize('value', [1.0, True], ids=['float', 'bool'])
def test_receipt_integer_fields_are_strict(tmp_path, monkeypatch, field, value):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 1})
    observe(monkeypatch, [1])
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='strict-fields')
    assert campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])['ok']
    original_hash = hash_file(output / 'run.json')
    changed = copy.deepcopy(run)
    changed['disk_free_guard'][field] = value
    changed = seal({k: v for k, v in changed.items() if k != 'content_hash'})
    original = campaign.read_json
    monkeypatch.setattr(campaign, 'read_json', lambda p: changed if Path(p) == output / 'run.json' else original(p))
    report = aggregate(plan, output, changed)
    assert not report['ok'], 'numeric equality admitted non-integer disk receipt'
    assert any('disk' in error for error in report['errors'])
    assert hash_file(output / 'run.json') == original_hash


def bounded_run(tmp_path, monkeypatch, failure=None):
    marker = tmp_path / 'owned-started'
    body = f"import time, os, json\nfrom pathlib import Path\ndef test_value(tmp_path):\n    from openpine.verification import execution_campaign as child_campaign\n    roots = json.loads(os.environ['OPENPINE_SOURCE_ROOTS'])\n    assert Path(child_campaign.__file__).resolve() == Path(roots['openpine']) / 'openpine/verification/execution_campaign.py'\n    (tmp_path / 'valuable').write_text('evidence')\n    Path({str(marker)!r}).write_text(str(os.getpid()))\n    time.sleep(1.2)\n"
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100}, {'test_a.py': body}, retention='delete-on-success' if failure else 'preserve')
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2, raising=False)
    calls, sizes = [], []
    writer_class = campaign.ObservationWriter
    class TrackedWriter(writer_class):
        def append(self, row):
            super().append(row)
            assert len(self.observations) <= 2, 'live supervisor list grew beyond its bound'
    monkeypatch.setattr(campaign, 'ObservationWriter', TrackedWriter)
    original_seal = campaign.seal
    def bounded_seal(value):
        if 'disk_free_guard' in value:
            sizes.append(len(value['disk_free_guard']['observations']))
        return original_seal(value)
    monkeypatch.setattr(campaign, 'seal', bounded_seal)
    def sample(output):
        calls.append(str(output))
        if failure and marker.exists() and len(calls) > 6:
            if failure == 'unavailable':
                raise OSError('unavailable after overflow')
            return SimpleNamespace(f_bavail=99, f_frsize=1)
        return SimpleNamespace(f_bavail=200, f_frsize=1)
    monkeypatch.setattr(os, 'statvfs', sample)
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='streamed')
    assert len(calls) > 6 and marker.exists()
    assert sizes and max(sizes) <= 2, 'run diagnostics still embed unbounded observations'
    assert len(json.dumps(run['disk_free_guard'])) < 1200
    disk = run['disk_free_guard']
    assert disk['observations'] == []
    desc = disk['observation_stream']
    assert hash_file(output / desc['path']) == desc['sha256']
    records = [json.loads(line) for line in (output / desc['path']).read_text().splitlines()]
    assert len(records) == len(calls)
    values = [row['free_bytes'] for row in records if row['free_bytes'] is not None]
    assert disk['samples'] == len(values)
    assert disk['minimum_observed_free_bytes'] == min(values)
    assert disk['observation_errors'] == sum(row['free_bytes'] is None for row in records)
    if failure:
        import psutil
        assert run['attempts'][0]['status'] == 'cancelled'
        from rc6_tests.cancelled_family import assert_cancelled_family
        assert_cancelled_family(output, run['attempts'][0])
        assert not psutil.pid_exists(int(marker.read_text()))
        assert list(output.rglob('valuable'))
        assert list(output.rglob('stdout.log')) and list(output.rglob('execution.json'))
        assert disk['tripped'] and run['errors']
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert report['ok'] is (failure is None)
    return plan, output, run


def test_stream_overflow_success_exact_readback(tmp_path, monkeypatch):
    bounded_run(tmp_path, monkeypatch)


@pytest.mark.parametrize('failure', ['low', 'unavailable'])
def test_stream_overflow_continues_owned_cancellation(tmp_path, monkeypatch, failure):
    bounded_run(tmp_path, monkeypatch, failure)


@pytest.mark.parametrize('damage', ['missing', 'hash', 'truncated', 'duplicate', 'nonfinite', 'oversize', 'symlink', 'traversal'])
def test_stream_rejects_unsafe_or_malformed_evidence(tmp_path, monkeypatch, damage):
    plan, output, run = bounded_run(tmp_path, monkeypatch)
    changed = copy.deepcopy(run)
    desc = changed['disk_free_guard']['observation_stream']
    original_path = output / desc['path']
    if damage == 'missing':
        desc['path'] = 'missing.jsonl'
    elif damage == 'hash':
        desc['sha256'] = 'sha256:' + '0' * 64
    elif damage == 'traversal':
        desc['path'] = '../run/run.json'
    else:
        target = output / ('damage-' + damage + '.jsonl')
        if damage == 'symlink':
            target.symlink_to(original_path)
        else:
            content = {'truncated': b'{"observed_at":"x"', 'duplicate': b'{"observed_at":"x","free_bytes":200,"free_bytes":200,"error":null}\n', 'nonfinite': b'{"observed_at":"x","free_bytes":NaN,"error":null}\n', 'oversize': b' ' * 8193 + b'\n'}[damage]
            target.write_bytes(content)
        desc['path'] = target.name
        if damage != 'symlink':
            desc['sha256'] = hash_file(target)
    changed = seal({k: v for k, v in changed.items() if k != 'content_hash'})
    original = campaign.read_json
    monkeypatch.setattr(campaign, 'read_json', lambda p: changed if Path(p) == output / 'run.json' else original(p))
    report = aggregate(plan, output, changed)
    assert not report['ok'] and any('disk' in error for error in report['errors'])


def test_writer_rejects_oversized_inline_record(tmp_path):
    from openpine.verification.execution_disk import ObservationWriter
    writer = ObservationWriter(tmp_path, 2)
    with pytest.raises(ValueError, match='oversized'):
        writer.append({'observed_at': 'x' * 5000, 'free_bytes': 200, 'error': None})
    assert not writer.observations


def test_stream_helper_volume_is_constant_memory(tmp_path):
    import tracemalloc
    from openpine.verification.execution_disk import ObservationWriter, iter_observations
    row = {'observed_at': '2026-10-04T00:00:00+00:00', 'free_bytes': 200, 'error': None}
    tracemalloc.start()
    try:
        writer = ObservationWriter(tmp_path, 2)
        for _ in range(20000):
            writer.append(row)
            assert len(writer.observations) <= 2
        desc = writer.finish()
        assert writer.observations == []
        assert hash_file(tmp_path / desc['path']) == desc['sha256']
        disk = {'observations': [], 'observation_stream': desc}
        count, minimum = 0, None
        for record in iter_observations(tmp_path, disk):
            count += 1
            minimum = record['free_bytes'] if minimum is None else min(minimum, record['free_bytes'])
        assert count == 20000 and minimum == 200
        assert tracemalloc.get_traced_memory()[1] < 4 * 1024 * 1024
    finally:
        tracemalloc.stop()


def test_stream_write_error_cancels_owned_preserves_partial(tmp_path, monkeypatch):
    import time
    import psutil
    marker = tmp_path / 'started'
    body = f"import time, os\nfrom pathlib import Path\ndef test_value(tmp_path):\n    (tmp_path / 'valuable').write_text('evidence')\n    Path({str(marker)!r}).write_text(str(os.getpid()))\n    time.sleep(2)\n"
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100}, {'test_a.py': body}, retention='delete-on-success')
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2)
    observe(monkeypatch, [200])
    writer_class = campaign.ObservationWriter
    class FailingWriter(writer_class):
        def _write(self, row):
            if marker.exists():
                raise OSError('stream write denied')
            super()._write(row)
    monkeypatch.setattr(campaign, 'ObservationWriter', FailingWriter)
    output = tmp_path / 'run'
    run = campaign.run_campaign(plan, path, output, run_id='storage-error')
    assert marker.exists()
    assert run['attempts'][0]['status'] == 'cancelled'
    from rc6_tests.cancelled_family import assert_cancelled_family
    assert_cancelled_family(output, run['attempts'][0])
    assert not psutil.pid_exists(int(marker.read_text()))
    assert list(output.rglob('valuable')) and list(output.rglob('stdout.log'))
    assert list(output.rglob('execution.json'))
    assert (output / 'disk-observations.partial').exists()
    assert run['disk_free_guard']['storage_error'] and run['errors']
    assert not campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])['ok']
