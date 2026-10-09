"""Opt-in raw pytest timing observations; no coverage or acceptance shortcut.

Load explicitly with -p openpine.verification.timing_progress. Unbuffered writes
flush each record to the kernel; fsync happens at finish/close. Header time begins
at pytest configuration, after coverage/pytest imports, not at process launch.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

import pytest


def pytest_configure(config):
    output = config.getoption('--verification-output', default=None)
    if not output:
        raise pytest.UsageError('timing progress requires the existing verification output')
    observer = TimingProgress(Path(output).with_name('timing-progress.jsonl'))
    observer.record({'event': 'header', 'schema': 'openpine.raw_timing_progress.v1',
            **{name: config.getoption('--verification-' + name, default=None)
               for name in ('task', 'shard', 'run-id', 'attempt-id', 'plan-hash')},
            'clock_origin': 'pytest_configure_after_coverage_and_pytest_imports',
            'observational_plugin_overhead_present': True,
            'production_defaults_changed': False,
            'production5_gate_pass': False, 'full_qualification_accepted': False})
    if observer.io_errors:
        observer.close()
        raise pytest.UsageError('timing header could not be persisted')
    config.pluginmanager.register(observer, 'openpine-timing-progress')


class TimingProgress:
    def __init__(self, path: Path):
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        self.sequence = 0
        self.io_errors: list[str] = []

    def error(self, error: Exception):
        if len(self.io_errors) < 16:
            self.io_errors.append(type(error).__name__)

    def record(self, event):
        """Record without replacing an original test/hook exception on IO failure."""
        try:
            value = {**event, 'sequence': self.sequence, 'pid': os.getpid(),
                     'monotonic': time.monotonic(), 'utc': datetime.now(timezone.utc).isoformat()}
            self.sequence += 1
            raw = (json.dumps(value, sort_keys=True, separators=(',', ':'),
                              ensure_ascii=True, allow_nan=False) + '\n').encode()
            if len(raw) > 262144:
                raise ValueError('timing record exceeds size bound')
            view = memoryview(raw)
            while view:
                written = os.write(self.fd, view)
                if written <= 0:
                    raise OSError('timing progress write failed')
                view = view[written:]
        except (OSError, ValueError, TypeError) as error:
            self.error(error)

    def end(self, stage, outcome, **fields):
        self.record({'event': 'stage_end', 'stage': stage, **fields,
                     'exception_type': outcome.excinfo[0].__name__ if outcome.excinfo else None})

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionstart(self, session):
        self.record({'event': 'stage_begin', 'stage': 'preflight'})
        outcome = yield
        self.end('preflight', outcome)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection(self, session):
        self.record({'event': 'stage_begin', 'stage': 'collection'})
        outcome = yield
        self.end('collection', outcome)

    def pytest_collection_finish(self, session):
        self.record({'event': 'collection', 'selected_nodes': len(session.items)})

    def pytest_runtest_logstart(self, nodeid, location):
        self.record({'event': 'node_start', 'nodeid': nodeid})

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item):
        self.record({'event': 'stage_begin', 'stage': 'runtest_call', 'nodeid': item.nodeid})
        outcome = yield
        self.end('runtest_call', outcome, nodeid=item.nodeid)

    def pytest_runtest_logreport(self, report):
        try:
            duration = float(report.duration)
        except (ValueError, TypeError, AttributeError) as error:
            self.error(error)
            return
        if not math.isfinite(duration) or duration < 0:
            self.error(ValueError('invalid phase duration'))
            return
        self.record({'event': 'phase', 'nodeid': report.nodeid, 'when': report.when,
                     'outcome': report.outcome, 'xfail': bool(getattr(report, 'wasxfail', False)),
                     'duration': duration})

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        outcome = yield
        if self.io_errors and session.exitstatus == 0 and not outcome.excinfo:
            session.exitstatus = 4  # Observation infrastructure failed; original failures keep their status.
        self.record({'event': 'finish', 'exitstatus': int(session.exitstatus),
                     'io_errors': list(self.io_errors),
                     'exitstatus_is_provisional_until_sync_and_close': True,
                     'exception_type': outcome.excinfo[0].__name__ if outcome.excinfo else None})
        # No records follow this close. A late IO failure is authoritative in
        # the actual pytest returncode; a raw provisional finish cannot attest
        # its own successful sync. Keep an existing failure/interrupt unchanged.
        self.close()
        if self.io_errors and session.exitstatus == 0 and not outcome.excinfo:
            session.exitstatus = 4

    def close(self):
        if self.fd < 0:
            return
        try:
            os.fsync(self.fd)
        except OSError as error:
            self.error(error)
        try:
            os.close(self.fd)
        except OSError as error:
            self.error(error)
        finally:
            self.fd = -1

    def pytest_unconfigure(self, config):
        self.close()
