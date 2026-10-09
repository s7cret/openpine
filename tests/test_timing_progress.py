from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from openpine.verification import timing_progress as timing


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_are_unbuffered_and_keep_clocks_and_pid(tmp_path):
    path = tmp_path / 'timing.jsonl'
    observer = timing.TimingProgress(path)
    try:
        observer.record({'event': 'header'})
        observer.record({'event': 'test'})
        rows = events(path)  # Observable before finish/close, no buffered flush needed.
        assert [r['sequence'] for r in rows] == [0, 1]
        assert all(r['pid'] == os.getpid() for r in rows)
        assert rows[0]['monotonic'] <= rows[1]['monotonic']
        assert all(datetime.fromisoformat(r['utc']).utcoffset().total_seconds() == 0 for r in rows)
    finally:
        observer.close()


@pytest.mark.parametrize('error_type', [AssertionError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('stage', ['preflight', 'collection', 'runtest_call'])
def test_hookwrappers_keep_original_exception_outcome(tmp_path, error_type, stage):
    path = tmp_path / 'timing.jsonl'
    observer = timing.TimingProgress(path)
    error = error_type('original exception')
    outcome = SimpleNamespace(excinfo=(error_type, error, None))
    generator = {'preflight': observer.pytest_sessionstart,
                 'collection': observer.pytest_collection,
                 'runtest_call': observer.pytest_runtest_call}[stage](SimpleNamespace(nodeid='test.py::n'))
    try:
        next(generator)
        with pytest.raises(StopIteration):
            generator.send(outcome)
        rows = events(path)
        assert [r['event'] for r in rows] == ['stage_begin', 'stage_end']
        assert rows[-1]['exception_type'] == error_type.__name__
        assert outcome.excinfo[1] is error  # No force_result or replacement outcome.
    finally:
        observer.close()


def test_write_failure_does_not_mask_original_exception(tmp_path, monkeypatch):
    observer = timing.TimingProgress(tmp_path / 'timing.jsonl')
    def fail_write(*args):
        raise OSError('disk full')
    monkeypatch.setattr(timing.os, 'write', fail_write)
    error = AssertionError('original assertion')
    outcome = SimpleNamespace(excinfo=(AssertionError, error, None))
    generator = observer.pytest_runtest_call(SimpleNamespace(nodeid='n'))
    try:
        next(generator)
        with pytest.raises(StopIteration):
            generator.send(outcome)
        assert outcome.excinfo[1] is error and observer.io_errors == ['OSError', 'OSError']
    finally:
        observer.close()


@pytest.mark.parametrize('status', [0, 1, 2])
def test_late_sync_failure_preserves_failure_and_fails_success(tmp_path, monkeypatch, status):
    path = tmp_path / 'timing.jsonl'
    observer = timing.TimingProgress(path)
    def fail_sync(fd):
        raise OSError('sync failed')
    monkeypatch.setattr(timing.os, 'fsync', fail_sync)
    session = SimpleNamespace(exitstatus=status)
    generator = observer.pytest_sessionfinish(session, status)
    next(generator)
    with pytest.raises(StopIteration):
        generator.send(SimpleNamespace(excinfo=None))
    assert session.exitstatus == (4 if status == 0 else status)
    assert observer.fd == -1
    assert events(path)[-1]['exitstatus_is_provisional_until_sync_and_close'] is True


def test_invalid_report_duration_is_observation_failure(tmp_path):
    observer = timing.TimingProgress(tmp_path / 'timing.jsonl')
    try:
        observer.pytest_runtest_logreport(SimpleNamespace(duration=object()))
        assert observer.io_errors == ['TypeError']
    finally:
        observer.close()


@pytest.mark.parametrize('body,expected_code,exception_type', [
    ('assert False, "original_assertion"', 1, 'AssertionError'),
    ('raise KeyboardInterrupt("original_interrupt")', 1, 'KeyboardInterrupt'),
    ('raise SystemExit("original_exit")', 1, 'SystemExit'),
])
def test_real_pytest_phase_markers_preserve_original_failures(tmp_path, body, expected_code, exception_type):
    (tmp_path / 'test_case.py').write_text('def test_case():\n    ' + body + '\n')
    repo = Path(__file__).resolve().parents[1]
    output = tmp_path / 'phases.json'
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(repo)}
    command = [sys.executable, '-B', '-m', 'pytest', '--noconftest', '-c', '/dev/null',
               '--rootdir=' + str(tmp_path), '-p', 'no:cacheprovider',
               '-p', 'openpine.verification.pytest_gate', '-p', 'openpine.verification.timing_progress',
               '--verification-output=' + str(output), '-q', 'test_case.py']
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    baseline = list(command)
    plugin_index = baseline.index('openpine.verification.timing_progress')
    del baseline[plugin_index - 1:plugin_index + 1]
    baseline[baseline.index('--verification-output=' + str(output))] = '--verification-output=' + str(tmp_path / 'baseline-phases.json')
    original = subprocess.run(baseline, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    # The existing inventory gate marks interrupted/incomplete phases failed;
    # compare its original status rather than pytest's unguarded interrupt code.
    assert result.returncode == original.returncode
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert {'AssertionError': 'original_assertion', 'KeyboardInterrupt': 'original_interrupt',
            'SystemExit': 'original_exit'}[exception_type] in result.stdout + result.stderr
    rows = events(tmp_path / 'timing-progress.jsonl')
    header = rows[0]
    assert header['clock_origin'] == 'pytest_configure_after_coverage_and_pytest_imports'
    assert header['production5_gate_pass'] is header['full_qualification_accepted'] is False
    begin = [r['stage'] for r in rows if r['event'] == 'stage_begin']
    assert begin == ['preflight', 'collection', 'runtest_call']
    call_end = next(r for r in rows if r['event'] == 'stage_end' and r['stage'] == 'runtest_call')
    assert call_end['exception_type'] == exception_type
    if exception_type != 'KeyboardInterrupt':
        assert any(r['event'] == 'phase' and r['when'] == 'call' and r['outcome'] == 'failed' for r in rows)
    assert rows[-1]['exitstatus'] == expected_code


def test_plugin_is_absent_unless_explicitly_loaded(tmp_path):
    (tmp_path / 'test_case.py').write_text('def test_case(): pass\n')
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '--noconftest', '-c', '/dev/null',
                            '-p', 'no:cacheprovider', '-q', 'test_case.py'], cwd=tmp_path,
                           env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONPATH': str(repo),
                                'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                           capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert not (tmp_path / 'timing-progress.jsonl').exists()
