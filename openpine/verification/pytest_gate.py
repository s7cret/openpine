"""Opt-in pytest gate: exact collection identity; skip/xfail never counts as pass.

Used explicitly with -p, including when third-party plugin auto-loading is off.
Collect-only can write a proposed inventory but cannot satisfy execution acceptance.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest

def collection_hash(nodeids: list[str]) -> str:
    data = json.dumps(sorted(nodeids), ensure_ascii=False, separators=(',', ':')).encode()
    return 'sha256:' + hashlib.sha256(data).hexdigest()

def validate_inventory(nodeids: list[str], expected: dict, deselected: int) -> None:
    if not nodeids or any((not isinstance(n, str) or not n for n in nodeids)) or len(set(nodeids)) != len(nodeids) or (not isinstance(expected, dict)) or (type(expected.get('count')) is not int) or (type(deselected) is not int) or (type(expected.get('deselected', 0)) is not int) or (deselected < 0):
        raise ValueError('empty or duplicate test inventory')
    if expected.get('identity_mode') == 'reviewed_addition_to_hashed_baseline':
        added = expected.get('added_nodeids')
        baseline = expected.get('baseline')
        if not isinstance(added, list) or not added or any((not isinstance(node, str) or not node for node in added)) or (len(set(added)) != len(added)) or (not isinstance(baseline, dict)) or (baseline.get('identity_mode') is not None) or ('nodeid_aliases' in baseline) or (not set(added).issubset(nodeids)) or (expected.get('count') != len(nodeids)) or (expected.get('count') != baseline.get('count', -1) + len(added)) or (expected.get('deselected', 0) != deselected):
            raise ValueError('invalid or incomplete reviewed additive inventory')
        original_nodes = [node for node in nodeids if node not in set(added)]
        aliases = expected.get('nodeid_aliases', {})
        # Reviewed display-ID changes retain the exact historical denominator and
        # hash. Every current ID must map once to an absent historical ID of the
        # same parametrized test; receipt nodeids and phase obligations stay real.
        if (not isinstance(aliases, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v
                       or '[' not in k or '[' not in v or not k.endswith(']') or not v.endswith(']')
                       or k.rsplit('[', 1)[0] != v.rsplit('[', 1)[0] for k, v in aliases.items())
                or len(set(aliases.values())) != len(aliases)
                or not set(aliases).issubset(original_nodes)
                or set(aliases.values()).intersection(nodeids)):
            raise ValueError('invalid or incomplete reviewed test ID aliases')
        original_nodes = [aliases.get(node, node) for node in original_nodes]
        validate_inventory(original_nodes, baseline, deselected)
        return
    if 'nodeid_aliases' in expected:
        raise ValueError('test ID aliases require an explicit reviewed additive inventory')
    if expected.get('count') != len(nodeids) or expected.get('sha256') != collection_hash(nodeids) or expected.get('deselected', 0) != deselected:
        raise ValueError('test inventory changed; explicit review/rebaseline required')

def validate_phase_reports(nodeids: list[str], reports: dict) -> list[str]:
    """Validate exact execution, including all phases and unexpected reports."""
    import math
    errors = []
    if not nodeids or len(set(nodeids)) != len(nodeids):
        errors.append('empty or duplicate executed inventory')
    for node in sorted(set(reports) - set(nodeids)):
        errors.append('unexpected test report: ' + node)
    for node in nodeids:
        rows = reports.get(node, [])
        valid = isinstance(rows, list) and len(rows) == 3 and all((isinstance(row, dict) for row in rows))
        if valid:
            valid = [row.get('when') for row in rows] == ['setup', 'call', 'teardown'] and all((row.get('outcome') == 'passed' and row.get('xfail') is False for row in rows))
        if not valid:
            errors.append('required test did not pass all phases: ' + node)
            continue
        for row in rows:
            duration = row.get('duration', 0.0)
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or (not math.isfinite(duration)) or (duration < 0):
                errors.append('invalid phase duration: ' + node)
    return errors

def _option(config, name, default=None):
    try:
        value = config.getoption(name)
    except (ValueError, AttributeError):
        return default
    return default if value is None else value

def pytest_addoption(parser):
    group = parser.getgroup('openpine-verification')
    for option in ('lock', 'suite', 'output', 'plan', 'plan-hash', 'task', 'shard', 'run-id', 'attempt-id', 'binding'):
        group.addoption('--verification-' + option, default=None)

def pytest_configure(config):
    output = _option(config, '--verification-output')
    if _option(config, '--verification-plan') and (not output):
        raise pytest.UsageError('a planned shard requires an immutable output')
    if output:
        if _option(config, 'numprocesses', 0):
            raise pytest.UsageError('use planned process/CI shards, not nested pytest-xdist')
        if Path(output).exists() or Path(output).is_symlink():
            raise pytest.UsageError('verification output already exists; use a new attempt')
        config.pluginmanager.register(InventoryGate(config), 'openpine-inventory-gate')

class InventoryGate:

    def __init__(self, config):
        import time
        self.config = config
        self.nodes = []
        self.deselected = 0
        self.reports = {}
        self.errors = []
        self.fixtures = {}
        self.node_markers = {}
        self.coverage_enabled = False
        self.started = time.perf_counter()
        self.collection_seconds = 0.0
        self.plan = self.task = self.shard = None
        self.before = self.environment = None
        self.execution_roots = None
        self.binding_hash = None

    def pytest_sessionstart(self, session):
        from openpine.verification.execution_identity import environment_snapshot, source_snapshot
        from openpine.verification.execution_plan import task_shard, validate_plan
        from openpine.verification.identity import read_json
        plan_path = _option(self.config, '--verification-plan')
        if not plan_path:
            return
        required = ('plan-hash', 'task', 'shard', 'run-id', 'attempt-id')
        if any((not _option(self.config, '--verification-' + key) for key in required)):
            raise pytest.UsageError('planned run needs plan-hash/task/shard/run-id/attempt-id')
        try:
            self.plan = validate_plan(read_json(Path(plan_path)), expected_hash=_option(self.config, '--verification-plan-hash'))
            self.task, self.shard = task_shard(self.plan, _option(self.config, '--verification-task'), _option(self.config, '--verification-shard'))
            if _option(self.config, '--verification-suite') != self.task['component']:
                raise ValueError('suite does not match the planned task')
            from openpine.verification.execution_binding import locations
            binding_path = _option(self.config, '--verification-binding')
            binding = read_json(Path(binding_path)) if binding_path else None
            self.execution_roots, executables = locations(self.plan, binding)
            if self.task['environment'] not in executables:
                raise ValueError('binding does not locate this task interpreter')
            self.binding_hash = binding['content_hash'] if binding else None
            try:
                import coverage
                self.coverage_enabled = coverage.Coverage.current() is not None
            except ImportError:
                self.coverage_enabled = False
            if self.coverage_enabled != self.shard.get('coverage', self.task.get('coverage', False)):
                raise ValueError('actual coverage instrumentation differs from the frozen shard')
            self.environment = environment_snapshot()
            expected_env = self.plan['environments'][self.task['environment']]['identity']
            if self.environment['content_hash'] != expected_env['content_hash']:
                raise ValueError('interpreter/dependency environment differs from the plan')
            self.before = source_snapshot({k: Path(v) for k, v in (self.execution_roots or self.plan['roots']).items()})
            if self.before['content_hash'] != self.plan['source']['content_hash']:
                raise ValueError('candidate source changed before execution')
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.errors.append(str(error))
            pytest.exit('verification preflight failed: ' + str(error), returncode=4)

    def pytest_deselected(self, items):
        self.deselected += len(items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append('collection failed: ' + report.nodeid)
        elif report.skipped:
            self.errors.append('collection skipped: ' + report.nodeid)

    def pytest_collection_finish(self, session):
        import time
        from openpine.verification.identity import read_json
        self.collection_seconds = time.perf_counter() - self.started
        self.nodes = [item.nodeid for item in session.items]
        self.node_markers = {item.nodeid: sorted({mark.name for mark in item.iter_markers()}) for item in session.items}
        lock = _option(self.config, '--verification-lock')
        try:
            if self.plan:
                if sorted(self.nodes) != self.shard['nodeids'] or self.deselected:
                    raise ValueError('shard collection differs from frozen assignment')
                if 'node_markers' in self.task and self.node_markers != {node: self.task['node_markers'][node] for node in self.shard['nodeids']}:
                    raise ValueError('collected test markers differ from the frozen plan')
            elif lock:
                expected = read_json(Path(lock))[_option(self.config, '--verification-suite')]
                validate_inventory(self.nodes, expected, self.deselected)
            elif not self.nodes or len(set(self.nodes)) != len(self.nodes):
                raise ValueError('empty or duplicate test inventory')
        except (KeyError, ValueError, TypeError, OSError) as error:
            self.errors.append(str(error))
        if self.errors and (not self.config.option.collectonly):
            pytest.exit('verification collection rejected: ' + '; '.join(self.errors), returncode=4)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        import time
        started = time.perf_counter()
        outcome = (yield)
        key = '::'.join((fixturedef.baseid, fixturedef.argname, fixturedef.scope))
        row = self.fixtures.setdefault(key, {'calls': 0, 'setup_seconds': 0.0, 'errors': 0})
        row['calls'] += 1
        row['setup_seconds'] += time.perf_counter() - started
        row['errors'] += int(outcome.excinfo is not None)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report):
        properties = list(getattr(report, 'user_properties', []))
        prior = [v for k, v in properties if k == 'openpine.nodeid']
        if prior and prior != [report.nodeid]:
            self.errors.append('conflicting JUnit node identity: ' + report.nodeid)
        report.user_properties = [(k, v) for k, v in properties if k != 'openpine.nodeid'] + [('openpine.nodeid', report.nodeid)]
        self.reports.setdefault(report.nodeid, []).append({'when': report.when, 'outcome': report.outcome, 'xfail': bool(getattr(report, 'wasxfail', False)), 'duration': float(getattr(report, 'duration', 0.0))})

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        import time
        from openpine.verification.execution_identity import source_snapshot, write_once_json
        from openpine.verification.identity import seal
        collect = bool(self.config.option.collectonly)
        if not collect:
            self.errors.extend(validate_phase_reports(self.nodes, self.reports))
        after_hash = None
        if self.plan:
            try:
                after_hash = source_snapshot({k: Path(v) for k, v in (self.execution_roots or self.plan['roots']).items()})['content_hash']
                if after_hash != self.plan['source']['content_hash']:
                    self.errors.append('candidate source changed during execution')
            except (ValueError, OSError) as error:
                self.errors.append(str(error))
        result = {'schema_id': 'openpine.test_inventory.v2' if self.plan else 'openpine.test_inventory.v1', 'suite': _option(self.config, '--verification-suite'), 'count': len(self.nodes), 'sha256': collection_hash(self.nodes), 'nodeids': sorted(self.nodes), 'node_markers': self.node_markers, 'coverage': self.coverage_enabled, 'deselected': self.deselected, 'collect_only': collect, 'errors': self.errors, 'ok': not collect and (not self.errors) and (exitstatus == 0), 'exitstatus': int(exitstatus), 'reports': self.reports, 'timing': {'wall_seconds': time.perf_counter() - self.started, 'collection_and_preflight_seconds': self.collection_seconds, 'fixtures': self.fixtures, 'fixture_teardown_accounting': 'included in per-test teardown; not separately attributed'}}
        try:
            import resource
            own = resource.getrusage(resource.RUSAGE_SELF)
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            result['resources'] = {'cpu_seconds': own.ru_utime + own.ru_stime, 'children_cpu_seconds': children.ru_utime + children.ru_stime, 'peak_process_rss_native': own.ru_maxrss, 'rss_unit': 'bytes' if __import__('sys').platform == 'darwin' else 'KiB', 'peak_rss_includes_children': False}
        except ImportError:
            result['resources'] = {'available': False}
        if self.plan:
            result.update({'plan_hash': self.plan['content_hash'], 'candidate_hash': self.plan['source']['content_hash'], 'binding_hash': self.binding_hash, 'source_before': self.before['content_hash'] if self.before else None, 'source_after': after_hash, 'environment_hash': self.environment['content_hash'] if self.environment else None, 'task': _option(self.config, '--verification-task'), 'shard': _option(self.config, '--verification-shard'), 'run_id': _option(self.config, '--verification-run-id'), 'attempt_id': _option(self.config, '--verification-attempt-id'), 'variant': self.task['variant'] if self.task else None, 'execution_path': self.task['execution_path'] if self.task else None, 'mode': self.task['mode'] if self.task else None})
            result = seal(result)
        path = Path(_option(self.config, '--verification-output'))
        write_once_json(path, result)
        if self.errors:
            session.exitstatus = 1
