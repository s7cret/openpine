"""Bounded process shards and exact aggregation for OpenPine verification.

No process, phase receipt, JUnit, or external owner gate is inferred from a
summary count. A successful scoped campaign is not stage or release acceptance.
"""
from __future__ import annotations
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from openpine.verification.execution_identity import clean_environment, ensure_external_output, evidence_path, hash_file, read_artifact, source_snapshot, write_once_json
from openpine.verification.execution_plan import validate_plan
from openpine.verification.execution_binding import locations, validate_binding
from openpine.verification.identity import read_json, seal, verify
from openpine.verification.pytest_gate import collection_hash, validate_phase_reports
from openpine.verification.execution_process import _stop_group, process_family_launch_error, start_declared_process, validate_process_family
from openpine.verification.execution_disk import INLINE_LIMIT, ObservationWriter, iter_observations
RUN_SCHEMA = 'openpine.test_campaign_run.v1'
AGGREGATE_SCHEMA = 'openpine.test_campaign_aggregate.v1'
DISK_INLINE_LIMIT = INLINE_LIMIT

def compiler_commit_environment(source_commits: dict[str, str]) -> str:
    """Bind compiler identities to the verified plan, not the test process HOME."""
    names = ('pine2ast', 'ast2python', 'pinelib', 'openpine-contracts')
    if not isinstance(source_commits, dict) or any(
        not isinstance(source_commits.get(name), str)
        or re.fullmatch('[0-9a-f]{40}', source_commits[name]) is None
        for name in names
    ):
        raise ValueError('exact plan producer commits are required')
    return json.dumps({name: source_commits[name] for name in names}, sort_keys=True, separators=(',', ':'))

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def descriptor(root: Path, path: Path) -> dict[str, str]:
    relative = path.relative_to(root).as_posix()
    evidence_path(root, relative)
    return {'path': relative, 'sha256': hash_file(path)}

def validate_junit(path: Path, nodeids: list[str]) -> dict:
    data = path.read_bytes()
    if not data or len(data) > 32 * 1024 * 1024 or b'<!DOCTYPE' in data.upper() or (b'<!ENTITY' in data.upper()):
        raise ValueError('empty/oversized/DTD JUnit is not admitted')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError('malformed JUnit') from error
    if root.tag not in {'testsuites', 'testsuite'}:
        raise ValueError('invalid JUnit root')
    cases = list(root.iter('testcase'))
    actual = []
    for case in cases:
        values = [p.get('value') for p in case.findall('./properties/property') if p.get('name') == 'openpine.nodeid']
        if len(values) != 1 or not values[0]:
            raise ValueError('missing/duplicate JUnit node identity')
        actual.append(values[0])
        if any((case.find(tag) is not None for tag in ('failure', 'error', 'skipped'))):
            raise ValueError('JUnit contains unsuccessful required test')
    if not actual or len(set(actual)) != len(actual) or sorted(actual) != sorted(nodeids):
        raise ValueError('JUnit test inventory is incomplete/duplicate/unexpected')
    for suite in root.iter('testsuite'):
        if suite.get('tests') != str(len(list(suite.iter('testcase')))):
            raise ValueError('JUnit summary test count mismatch')
        for key in ('failures', 'errors', 'skipped'):
            if suite.get(key) != '0':
                raise ValueError('JUnit has a missing/nonzero failure/error/skip count')
    return {'tests': len(actual), 'nodeids_hash': collection_hash(actual)}

def _wait_for_process_group_exit(pgid: int, grace_seconds: float = 2.0) -> bool:
    """Allow an exiting test's same-session children to finish, never waive a live orphan."""
    deadline = time.monotonic() + grace_seconds
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        members = _remaining_group_members(pgid)
        if members and all(member['status'] in {'zombie', 'dead'} for member in members):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _remaining_group_members(pgid: int) -> list[dict[str, object]]:
    """Report process identity/state only; never collect arguments or payloads."""
    try:
        import psutil
    except ImportError:
        return []
    members = []
    for process in psutil.process_iter():
        try:
            if os.getpgid(process.pid) == pgid:
                members.append({'pid': process.pid, 'name': process.name(), 'status': process.status()})
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
        except (PermissionError, psutil.AccessDenied):
            return []  # Incomplete snapshot must never waive the process guard.
    return sorted(members, key=lambda row: row['pid'])


def _validate_primary_artifacts(plan: dict, evidence_root: Path, task: dict, shard: dict, attempt: dict, expected_binding: str | None) -> dict:
    """Use the aggregate's primary checks before any opt-in fixture disposal."""
    expected_source = plan['source']['content_hash']
    expected_run_id = attempt['run_id']
    artifacts = attempt['artifacts']
    if 'process-family' not in artifacts:
        raise ValueError('missing primary process-family evidence')
    for desc in artifacts.values():
        if hash_file(evidence_path(evidence_root, desc['path'])) != desc['sha256']:
            raise ValueError('attempt artifact checksum mismatch')
    validate_process_family(evidence_path(evidence_root, artifacts['process-family']['path']),
                            argv=attempt.get('argv'), cwd=attempt.get('cwd'))
    if shard.get('coverage', task.get('coverage', False)) and 'coverage' not in artifacts:
        raise ValueError('required coverage data is missing')
    phases = read_artifact(evidence_root, artifacts['phases'])
    verify(phases, 'openpine.test_inventory.v2')
    binding = {'task': task['id'], 'shard': shard['id'], 'suite': task['component'], 'plan_hash': plan['content_hash'], 'run_id': expected_run_id, 'attempt_id': attempt['attempt_id'], 'candidate_hash': expected_source, 'source_before': expected_source, 'source_after': expected_source, 'environment_hash': plan['environments'][task['environment']]['identity']['content_hash'], 'variant': task['variant'], 'execution_path': task['execution_path'], 'mode': task['mode']}
    if 'node_markers' in task and phases.get('node_markers') != {n: task['node_markers'][n] for n in shard['nodeids']}:
        raise ValueError('phase marker evidence differs from plan')
    if phases.get('coverage', False) != shard.get('coverage', task.get('coverage', False)):
        raise ValueError('phase instrumentation differs from plan')
    if phases.get('binding_hash') != expected_binding:
        raise ValueError('phase binding identity mismatch')
    if any((phases.get(k) != v for k, v in binding.items())):
        raise ValueError('source/environment/task/path/attempt identity mismatch')
    if phases.get('ok') is not True or phases.get('collect_only') is not False or phases.get('exitstatus') != 0 or (phases.get('errors') != []) or (phases.get('deselected') != 0) or (phases.get('nodeids') != shard['nodeids']) or (type(phases.get('count')) is not int) or (phases['count'] != len(shard['nodeids'])) or (phases.get('sha256') != collection_hash(shard['nodeids'])):
        raise ValueError('unsuccessful/incomplete phase receipt')
    phase_errors = validate_phase_reports(shard['nodeids'], phases['reports'])
    if phase_errors:
        raise ValueError('; '.join(phase_errors))
    junit_path = evidence_path(evidence_root, artifacts['junit']['path'])
    if hash_file(junit_path) != artifacts['junit']['sha256']:
        raise ValueError('JUnit checksum mismatch')
    validate_junit(junit_path, shard['nodeids'])
    for artifact_name in ('stdout', 'selectors'):
        path = evidence_path(evidence_root, artifacts[artifact_name]['path'])
        if hash_file(path) != artifacts[artifact_name]['sha256']:
            raise ValueError(artifact_name + ' checksum mismatch')
    selector_path = evidence_path(evidence_root, artifacts['selectors']['path'])
    if selector_path.read_text(encoding='utf-8').splitlines() != shard['nodeids']:
        raise ValueError('selector contents differ from frozen assignment')
    return phases


def _clear_owned_private(output: Path, folder: Path, artifacts: dict) -> None:
    """Delete only a new attempt's private tree; primary references veto disposal."""
    private = folder / 'private'
    if '..' in private.parts or any(path.is_symlink() for path in (private, *private.parents)):
        raise ValueError('unsafe private cleanup root')
    relative = folder.relative_to(output)
    if len(relative.parts) != 3 or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+/s[0-9]{3}/a001', relative.as_posix()):
        raise ValueError('private cleanup is not a newly owned shard attempt')
    private = private.resolve(strict=True)
    if not private.is_dir():
        raise ValueError('unsafe private cleanup tree')
    # rmtree unlinks interior symlinks without following their targets; pytest
    # itself creates current-fixture links inside its basetemp.
    for desc in artifacts.values():
        primary = evidence_path(output, desc['path']).resolve(strict=True)
        if primary == private or private in primary.parents or primary in private.parents:
            raise ValueError('private cleanup overlaps a primary artifact')
    shutil.rmtree(private)


def _execute_shard(plan: dict, plan_path: Path, output: Path, task: dict, shard: dict, run_id: str, cancellation: threading.Event, binding: dict | None=None, build_commit: str | None=None) -> dict:
    attempt = 'a001'
    relative = task['id'] + '/' + shard['id'] + '/' + attempt
    folder = output / relative
    folder.mkdir(parents=True, exist_ok=False)
    execution_roots, executables = locations(plan, binding)
    env = clean_environment(execution_roots, folder / 'private', build_commit=build_commit if task['component'] == 'openpine' else None, primary_component=task['component'])
    if task['component'] == 'openpine':
        env['OPENPINE_PRODUCER_COMMITS_JSON'] = compiler_commit_environment(plan.get('source_commits', {}))
    env['OPENPINE_STAGE1_EVIDENCE'] = str(folder / 'owner-evidence')
    selectors = folder / 'nodeids.args'
    selectors.write_text('\n'.join(shard['nodeids']) + '\n', encoding='utf-8')
    executable = executables[task['environment']]
    argv = [executable, '-m']
    if shard.get('coverage', task.get('coverage', False)):
        argv += ['coverage', 'run', '--data-file=' + str(folder / '.coverage'), '--rcfile=' + str(Path(execution_roots[task['component']]) / 'pyproject.toml'), '--source=' + task['coverage_package'], '-m']
    argv += ['pytest', '-q', '--durations=30', '-p', 'openpine.verification.pytest_gate']
    for plugin in task['plugins']:
        argv.extend(['-p', plugin])
    argv.extend(['--verification-plan=' + str(plan_path), '--verification-plan-hash=' + plan['content_hash'], '--verification-suite=' + task['component'], '--verification-task=' + task['id'], '--verification-shard=' + shard['id'], '--verification-run-id=' + run_id, '--verification-attempt-id=' + attempt, '--verification-output=' + str(folder / 'phases.json'), '--junitxml=' + str(folder / 'junit.xml'), '--basetemp=' + str(folder / 'private' / 'pytest'), '@' + str(selectors)])
    if binding is not None:
        argv.append('--verification-binding=' + str(output / 'binding.json'))
    started, tick = (utc_now(), time.perf_counter())
    status, returncode, error = ('not_run', None, None)
    process = None
    try:
        if cancellation.is_set():
            status = 'cancelled'
        else:
            with (folder / 'stdout.log').open('xb') as stream:
                process = start_declared_process(argv, family_evidence=folder / 'process-family.json', cwd=execution_roots[task['component']], env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name == 'posix')
                while process.poll() is None:
                    if cancellation.is_set():
                        status = 'cancelled'
                        _stop_group(process)
                        break
                    if time.perf_counter() - tick > task['timeout_seconds']:
                        status = 'timeout'
                        _stop_group(process)
                        break
                    time.sleep(0.05)
                returncode = process.poll()
                if status not in {'timeout', 'cancelled'}:
                    status = 'completed' if returncode == 0 else 'failed'
                    launch_error = process_family_launch_error(folder / 'process-family.json')
                    if launch_error is not None:
                        status, error = ('infrastructure_error', launch_error)
    except (OSError, ValueError) as caught:
        status, error = ('infrastructure_error', str(caught))
    finally:
        if process is not None and process.poll() is None:
            _stop_group(process)
        elif process is not None and os.name == 'posix':
            if not _wait_for_process_group_exit(process.pid, 2.0 if status == 'completed' else 0.0):
                members = _remaining_group_members(process.pid)
                _stop_group(process)
                if status == 'completed':
                    status, error = ('failed', f'orphan child process after pytest exit; group members: {members[:16]!r}')
    coverage_files = sorted(folder.glob('.coverage.*'))
    coverage_combine = None
    if coverage_files and status == 'completed':
        try:
            if any(path.is_symlink() or not path.is_file() for path in coverage_files):
                raise ValueError('unsafe subprocess coverage data')
            from openpine.verification.execution_process import run_logged
            coverage_combine = run_logged(
                [executable, '-m', 'coverage', 'combine', '--keep',
                 '--rcfile=' + str(Path(execution_roots[task['component']]) / 'pyproject.toml'),
                 '--data-file=' + str(folder / '.coverage'),
                 *[str(path) for path in coverage_files]],
                cwd=execution_roots[task['component']], output=folder / 'coverage-combine',
                env=env, timeout=180,
            )
            if not coverage_combine['ok']:
                raise ValueError('subprocess coverage union failed')
        except (OSError, ValueError) as caught:
            status, error = ('infrastructure_error', str(caught))
    artifacts = {}
    for path in coverage_files:
        if path.is_file() and not path.is_symlink():
            artifacts['coverage-process:' + path.name] = descriptor(output, path)
    if coverage_combine is not None:
        artifacts['coverage-combine'] = descriptor(output, folder / 'coverage-combine/command.json')
        for path in sorted((folder / 'coverage-combine').iterdir()):
            if path.is_file() and not path.is_symlink():
                artifacts['coverage-combine:' + path.name] = descriptor(output, path)
    for key, filename in (('phases', 'phases.json'), ('junit', 'junit.xml'), ('stdout', 'stdout.log'), ('selectors', 'nodeids.args'), ('coverage', '.coverage'), ('process-family', 'process-family.json')):
        path = folder / filename
        if path.is_file() and (not path.is_symlink()):
            artifacts[key] = descriptor(output, path)
    owner_root = folder / 'owner-evidence'
    if owner_root.exists():
        for path in sorted(owner_root.rglob('*')):
            if path.is_symlink():
                status, error = ('failed', 'owner evidence contains a symlink')
                break
            if path.is_file():
                artifacts['owner:' + path.relative_to(owner_root).as_posix()] = descriptor(output, path)
    if status == 'completed' and task.get('private_retention', 'preserve') == 'delete-on-success':
        try:
            if cancellation.is_set():
                status = 'cancelled'
            else:
                _validate_primary_artifacts(plan, output, task, shard, {'artifacts': artifacts, 'run_id': run_id, 'attempt_id': attempt}, binding['content_hash'] if binding else None)
                if cancellation.is_set():
                    status = 'cancelled'
                else:
                    _clear_owned_private(output, folder, artifacts)
        except (OSError, ValueError, KeyError, TypeError) as caught:
            status, error = ('infrastructure_error', 'private retention: ' + str(caught))
    result = {'task': task['id'], 'shard': shard['id'], 'attempt_id': attempt, 'run_id': run_id, 'status': status, 'returncode': returncode, 'error': error, 'started_at': started, 'finished_at': utc_now(), 'wall_seconds': time.perf_counter() - tick, 'argv': argv, 'cwd': execution_roots[task['component']], 'artifacts': artifacts, 'binding_hash': binding['content_hash'] if binding else None}
    write_once_json(folder / 'execution.json', result)
    return result

def component_window(queued: list, running: dict) -> str | None:
    """Serialize component campaigns while allowing bounded same-owner shards."""
    components = {task['component'] for task, _ in running.values()}
    if len(components) > 1:
        raise ValueError('mixed component workers violate the serial component window')
    return next(iter(components)) if components else (queued[0][0]['component'] if queued else None)


def run_campaign(plan: dict, plan_path: Path, output: Path, *, jobs: int=1, max_parallel_shards: int | None=None, run_id: str | None=None, shard_keys: list[tuple[str, str]] | None=None, binding: dict | None=None, memory_mib: int | None=None, build_commit: str | None=None) -> dict:
    validate_plan(plan)
    if build_commit is not None and build_commit != plan.get('source_commits', {}).get('openpine'):
        raise ValueError('campaign build commit differs from frozen source plan')
    from openpine.verification.execution_resources import resource_profile
    observed_resources = resource_profile()
    available = observed_resources['cpu_slots']
    if type(jobs) is not int or not 1 <= jobs <= available:
        raise ValueError(f'jobs must be between 1 and the observed CPU budget ({available})')
    max_parallel_shards = jobs if max_parallel_shards is None else max_parallel_shards
    if type(max_parallel_shards) is not int or not 1 <= max_parallel_shards <= jobs:
        raise ValueError('parallel shard organization exceeds campaign CPU budget')
    all_keys = {(t['id'], s['id']) for t in plan['tasks'] for s in t['shards']}
    selected = all_keys if shard_keys is None else set(shard_keys)
    if not selected or not selected.issubset(all_keys) or (shard_keys is not None and len(selected) != len(shard_keys)):
        raise ValueError('fragment selects empty, unknown or duplicate shards')
    from openpine.verification.execution_binding import checked_locations
    execution_roots, executables = checked_locations(plan, binding)
    selected_tasks = [t for t in plan['tasks'] if any((key[0] == t['id'] for key in selected))]
    if any((t['environment'] not in executables for t in selected_tasks)):
        raise ValueError('missing bound interpreter for selected task')
    if any((t['cpu_slots'] > jobs for t in selected_tasks)):
        raise ValueError('task CPU reservation exceeds campaign budget')
    roots = {k: Path(v) for k, v in execution_roots.items()}
    if memory_mib is not None:
        if type(memory_mib) is not int or memory_mib < 64:
            raise ValueError('memory budget must be an integer of at least 64 MiB')
        memory_limit = observed_resources['memory_limit_bytes']
        if memory_limit is not None and memory_mib*1024*1024 > memory_limit:
            raise ValueError('requested memory budget exceeds the observed host/cgroup limit')
        if any((t.get('memory_mib', 256) > memory_mib for t in selected_tasks)):
            raise ValueError('task memory reservation exceeds campaign budget')
        try:
            import psutil
        except ImportError as error:
            raise ValueError('memory-budget monitoring requires psutil') from error
    ensure_external_output(output, roots)
    if output.exists():
        raise ValueError('campaign output exists; prior evidence cannot be overwritten')
    if read_json(plan_path) != plan:
        raise ValueError('supplied plan bytes differ from frozen plan')
    before = source_snapshot(roots)
    if before['content_hash'] != plan['source']['content_hash']:
        raise ValueError('candidate changed before campaign')
    run_id = run_id or uuid.uuid4().hex
    if not __import__('re').fullmatch('[a-zA-Z0-9_-]{1,80}', run_id):
        raise ValueError('invalid run ID')
    output.mkdir(parents=True)
    if binding is not None:
        write_once_json(output / 'binding.json', binding)
    cancellation = threading.Event()
    queued = [(t, s) for t in plan['tasks'] for s in t['shards'] if (t['id'], s['id']) in selected]
    running = {}
    results, errors = ([], [])
    disk_diagnostics = None
    disk_writer = None
    if 'disk_free_guard' in plan:
        disk_writer = ObservationWriter(output, DISK_INLINE_LIMIT)
        disk_diagnostics = {**plan['disk_free_guard'], 'minimum_observed_free_bytes': None,
                            'samples': 0, 'observation_errors': 0, 'observations': disk_writer.observations,
                            'tripped': False, 'enforcement': 'sampled cancellation; not a hard disk-size or stop-latency bound'}

    def check_disk_free() -> bool:
        if disk_diagnostics is None:
            return True
        if disk_diagnostics['tripped']:
            return False
        observation = {'observed_at': utc_now(), 'free_bytes': None, 'error': None}
        try:
            stat = os.statvfs(output)
            if type(stat.f_bavail) is not int or stat.f_bavail < 0 or type(stat.f_frsize) is not int or stat.f_frsize <= 0:
                raise ValueError('invalid statvfs available-block observation')
            free = stat.f_bavail * stat.f_frsize
            observation['free_bytes'] = free
            disk_diagnostics['samples'] += 1
            minimum = disk_diagnostics['minimum_observed_free_bytes']
            disk_diagnostics['minimum_observed_free_bytes'] = free if minimum is None else min(minimum, free)
            if free < disk_diagnostics['minimum_free_bytes']:
                observation['error'] = 'observed available disk bytes below frozen floor'
        except (OSError, ValueError, TypeError, AttributeError) as error:
            disk_diagnostics['observation_errors'] += 1
            observation['error'] = 'disk free observation unavailable: ' + str(error)[:512]
        try:
            disk_writer.append(observation)
        except (OSError, ValueError, TypeError) as error:
            observation['error'] = 'disk observation storage unavailable: ' + str(error)[:512]
            disk_diagnostics['storage_error'] = observation['error']
        if observation['error'] is not None:
            # Only this scheduler admits work; trip before any further submit.
            disk_diagnostics['tripped'] = True
            errors.append(observation['error'])
            cancellation.set()
            queued.clear()
            return False
        return True

    tick, started = (time.perf_counter(), utc_now())
    peak_total_rss = 0
    sample_count = 0
    rss_sampling_errors = 0
    try:
        import psutil
        process = psutil.Process()
    except ImportError:
        process = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        try:
            while queued or running:
                check_disk_free()
                used = sum((t['cpu_slots'] for t, _ in running.values()))
                reserved_memory = sum((t.get('memory_mib', 256) for t, _ in running.values()))
                groups = {t['exclusive_group'] for t, _ in running.values() if t['exclusive_group']}
                component = component_window(queued, running)
                for task, shard in list(queued):
                    if task['component'] != component:
                        continue
                    if len(running) >= max_parallel_shards or used + task['cpu_slots'] > jobs or (memory_mib is not None and reserved_memory + task.get('memory_mib', 256) > memory_mib) or (task['exclusive_group'] and task['exclusive_group'] in groups):
                        continue
                    if cancellation.is_set() or not check_disk_free():
                        break
                    future = pool.submit(_execute_shard, plan, plan_path.resolve(), output.resolve(), task, shard, run_id, cancellation, binding, build_commit)
                    running[future] = (task, shard)
                    queued.remove((task, shard))
                    used += task['cpu_slots']
                    reserved_memory += task.get('memory_mib', 256)
                    if task['exclusive_group']:
                        groups.add(task['exclusive_group'])
                done, _ = concurrent.futures.wait(running, timeout=0.1, return_when=concurrent.futures.FIRST_COMPLETED)
                check_disk_free()
                if process is not None:
                    try:
                        peak_total_rss = max(peak_total_rss, sum((p.memory_info().rss for p in [process, *process.children(recursive=True)])))
                        sample_count += 1
                        if memory_mib is not None and peak_total_rss > memory_mib * 1024 * 1024:
                            if not cancellation.is_set():
                                errors.append('sampled process-tree RSS exceeded campaign memory budget')
                            cancellation.set()
                            queued.clear()
                    except psutil.NoSuchProcess:
                        rss_sampling_errors += 1
                    except psutil.AccessDenied:
                        rss_sampling_errors += 1
                        if memory_mib is not None:
                            errors.append('cannot enforce sampled memory budget: process observation denied')
                            cancellation.set()
                            queued.clear()
                for future in done:
                    task, shard = running.pop(future)
                    try:
                        results.append(future.result())
                    except Exception as error:  # noqa: BLE001 -- supervision boundary records the real failure
                        errors.append(f"{task['id']}/{shard['id']}: {type(error).__name__}: {error}")
        except KeyboardInterrupt:
            cancellation.set()
            errors.append('campaign cancelled')
            for future in running:
                try:
                    results.append(future.result())
                except Exception as error:  # noqa: BLE001 -- supervision boundary records the real failure
                    errors.append('cancel cleanup: ' + str(error))
    if disk_writer is not None:
        try:
            stream_descriptor = disk_writer.finish() if disk_diagnostics.get('storage_error') is None else None
            if stream_descriptor is not None:
                disk_diagnostics['observation_stream'] = stream_descriptor
        except (OSError, ValueError, TypeError) as error:
            disk_diagnostics['tripped'] = True
            disk_diagnostics['storage_error'] = 'disk observation publication failed: ' + str(error)[:512]
            errors.append(disk_diagnostics['storage_error'])
            cancellation.set()
        finally:
            try:
                disk_writer.close_partial()
            except OSError as error:
                disk_diagnostics['tripped'] = True
                errors.append('disk observation close failed: ' + str(error)[:512])
    if memory_mib is not None and sample_count == 0:
        errors.append('memory-budget monitoring produced no valid observations')
    after_hash = None
    try:
        after_hash = source_snapshot(roots)['content_hash']
        if after_hash != before['content_hash']:
            errors.append('source changed during campaign')
    except (OSError, ValueError) as error:
        errors.append(str(error))
    run = seal({'schema_id': RUN_SCHEMA, 'run_id': run_id, 'plan_hash': plan['content_hash'], 'candidate_hash': plan['source']['content_hash'], 'source_before': before['content_hash'], 'source_after': after_hash, 'started_at': started, 'finished_at': utc_now(), 'wall_seconds': time.perf_counter() - tick, 'jobs': jobs, 'max_parallel_shards': max_parallel_shards, 'affinity_cpus': observed_resources['affinity_cpus'], 'resource_profile': observed_resources, 'binding': descriptor(output, output / 'binding.json') if binding is not None else None, 'selection': [list(key) for key in sorted(selected)], 'is_fragment': selected != all_keys, 'memory_budget_mib': memory_mib, 'memory_enforcement': 'reservation plus sampled RSS cancellation; not a kernel hard limit' if memory_mib else 'not_requested', 'sampled_process_tree_peak_rss_bytes': peak_total_rss if sample_count else None, 'rss_samples': sample_count, 'rss_sampling_errors': rss_sampling_errors, 'rss_sampling_is_exact_peak': False, 'attempts': sorted(results, key=lambda r: (r['task'], r['shard'])), 'errors': errors})
    if disk_diagnostics is not None:
        run = seal({**{k: v for k, v in run.items() if k != 'content_hash'}, 'disk_free_guard': disk_diagnostics})
    write_once_json(output / 'run.json', run)
    return run

def aggregate_campaign(plan: dict, evidence_root: Path, *, expected_plan_hash: str, expected_run_id: str, expected_shards: list[tuple[str, str]] | None=None) -> dict:
    validate_plan(plan, expected_hash=expected_plan_hash)
    run = read_json(evidence_path(evidence_root, 'run.json'))
    verify(run, RUN_SCHEMA)
    errors = list(run['errors'])
    if 'disk_free_guard' in plan:
        try:
            disk = run.get('disk_free_guard')
            floor = plan['disk_free_guard']['minimum_free_bytes']
            if (not isinstance(disk, dict) or type(disk.get('minimum_free_bytes')) is not int
                    or disk['minimum_free_bytes'] <= 0 or disk['minimum_free_bytes'] != floor):
                raise ValueError('disk guard receipt differs from frozen floor')
            samples, observation_errors, minimum, rows = 0, 0, None, 0
            for observation in iter_observations(evidence_root, disk):
                rows += 1
                if not isinstance(observation, dict) or not isinstance(observation.get('observed_at'), str):
                    raise ValueError('invalid disk observation evidence')
                free = observation.get('free_bytes')
                if free is None:
                    observation_errors += 1
                    if not isinstance(observation.get('error'), str) or not observation['error']:
                        raise ValueError('disk observation failure lacks diagnostics')
                elif type(free) is not int or free < 0:
                    raise ValueError('invalid disk available byte observation')
                else:
                    samples += 1
                    minimum = free if minimum is None else min(minimum, free)
                    if free < floor or observation.get('error') is not None:
                        raise ValueError('disk free guard tripped in observation evidence')
            observed_minimum = disk.get('minimum_observed_free_bytes')
            if (type(disk.get('samples')) is not int or disk['samples'] != samples
                    or not rows or not samples or type(observed_minimum) is not int
                    or observed_minimum < 0 or observed_minimum != minimum
                    or type(disk.get('observation_errors')) is not int
                    or disk['observation_errors'] != observation_errors or observation_errors
                    or disk.get('tripped') is not False or disk.get('storage_error') is not None):
                raise ValueError('disk guard tripped or inconsistent monitoring counters')
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append('invalid disk free guard evidence: ' + str(error))
    if run.get('run_id') != expected_run_id or run.get('plan_hash') != plan['content_hash']:
        errors.append('wrong campaign or plan identity')
    expected_source = plan['source']['content_hash']
    if any((run.get(k) != expected_source for k in ('candidate_hash', 'source_before', 'source_after'))):
        errors.append('campaign candidate identity mismatch')
    expected = {(t['id'], s['id']): (t, s) for t in plan['tasks'] for s in t['shards']}
    all_expected = dict(expected)
    if expected_shards is not None:
        selected = set(expected_shards)
        if not selected or len(selected) != len(expected_shards) or (not selected.issubset(expected)):
            raise ValueError('invalid expected fragment assignment')
        expected = {k: v for k, v in expected.items() if k in selected}
    seen, executed = (set(), set())
    binding_hash = None
    runtime_binding = None
    if run.get('binding') is not None:
        runtime_binding = read_artifact(evidence_root, run['binding'])
        validate_binding(plan, runtime_binding)
        binding_hash = runtime_binding['content_hash']
    selection = run.get('selection')
    if selection is not None and sorted(selection) != [list(k) for k in sorted(expected)]:
        errors.append('fragment is not a complete campaign; merge all planned fragments')
    if run.get('merged_fragments') is not None:
        from openpine.verification.execution_fragments import verify_fragment_provenance
        try:
            verify_fragment_provenance(plan, evidence_root, run)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append('invalid fragment provenance: ' + str(error))
    cpu_seconds = 0.0
    cpu_available = True
    for attempt in run['attempts']:
        key = (attempt.get('task'), attempt.get('shard'))
        if key in seen:
            errors.append('duplicate/retried shard is not implicitly accepted: ' + str(key))
            continue
        seen.add(key)
        if key not in expected:
            errors.append('unexpected shard: ' + str(key))
            continue
        task, shard = expected[key]
        try:
            attempt_id = attempt.get('attempt_id')
            if not isinstance(attempt_id, str) or not __import__('re').fullmatch('a[0-9]{3}', attempt_id):
                raise ValueError('invalid attempt ID')
            execution_file = evidence_path(evidence_root, f"{task['id']}/{shard['id']}/{attempt_id}/execution.json")
            if read_json(execution_file) != attempt:
                raise ValueError('execution receipt differs from campaign summary')
            expected_binding = attempt.get('binding_hash') if run.get('merged_fragments') else binding_hash
            if not run.get('merged_fragments') and attempt.get('binding_hash') != binding_hash:
                raise ValueError('attempt binding differs from campaign binding')
            if attempt.get('status') != 'completed' or type(attempt.get('returncode')) is not int or attempt['returncode'] != 0:
                raise ValueError('shard failed/cancelled/crashed/timed out or never ran')
            if attempt.get('run_id') != expected_run_id:
                raise ValueError('attempt belongs to another campaign')
            expected_roots, expected_executables = locations(plan, runtime_binding if run.get('binding') is not None else None)
            if run.get('merged_fragments') is None:
                if attempt.get('cwd') != expected_roots[task['component']] or not isinstance(attempt.get('argv'), list) or not attempt['argv'] or attempt['argv'][0] != expected_executables[task['environment']]:
                    raise ValueError('execution command interpreter/cwd differs from frozen plan')
                argv = attempt['argv']
                required_args = ['--verification-plan-hash=' + plan['content_hash'], '--verification-suite=' + task['component'], '--verification-task=' + task['id'], '--verification-shard=' + shard['id'], '--verification-run-id=' + expected_run_id, '--verification-attempt-id=' + attempt_id]
                if any(argv.count(arg) != 1 for arg in required_args) or argv.count('openpine.verification.pytest_gate') != 1:
                    raise ValueError('execution argv is not the planned verification command')
            phases = _validate_primary_artifacts(plan, evidence_root, task, shard, attempt, expected_binding)
            for node in shard['nodeids']:
                obligation = (task['id'], node, task['variant'], task['execution_path'], task['mode'])
                if obligation in executed:
                    raise ValueError('duplicate executed obligation')
                executed.add(obligation)
            resources = phases.get('resources', {})
            if resources.get('available') is False or not {'cpu_seconds', 'children_cpu_seconds'}.issubset(resources):
                cpu_available = False
            else:
                import math
                costs = [resources['cpu_seconds'], resources['children_cpu_seconds']]
                if any((type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in costs)):
                    raise ValueError('invalid measured CPU resources')
                cpu_seconds += sum(costs)
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f'{key[0]}/{key[1]}: {error}')
    for key in sorted(set(expected) - seen):
        errors.append('missing shard: ' + str(key))
    required = {(t['id'], n, t['variant'], t['execution_path'], t['mode']) for t, s in expected.values() for n in s['nodeids']}
    if executed != required:
        errors.append('executed obligation union differs from required plan')
    missing_gates = list(plan['required_gates'])
    report = seal({'schema_id': AGGREGATE_SCHEMA, 'plan_hash': plan['content_hash'], 'candidate_hash': expected_source, 'run_id': expected_run_id, 'run_hash': run['content_hash'], 'profile': plan['profile'], 'scope': 'fragment' if set(expected) != set(all_expected) else 'selected pytest obligations on the explicitly observed interpreters', 'is_fragment': set(expected) != set(all_expected), 'interpreters': sorted({v['identity']['python'] for v in plan['environments'].values()}), 'required_obligations': len(required), 'executed_obligations': len(executed), 'required_shards': len(expected), 'observed_shards': len(seen), 'errors': errors, 'pytest_scope_passed': not errors, 'ok': not errors and (not missing_gates), 'owner_gates_required_separately': missing_gates, 'full_stage_accepted': False, 'full_release_accepted': False, 'wall_seconds': run['wall_seconds'], 'cpu_seconds': cpu_seconds if cpu_available else None, 'sampled_process_tree_peak_rss_bytes': run['sampled_process_tree_peak_rss_bytes']})
    return report

def export_suite_receipts(plan: dict, evidence_root: Path, output: Path, *, expected_plan_hash: str, expected_run_id: str) -> dict:
    """Bridge verified FULL component shards to the unchanged foundation owner.

    These receipts cannot replace source-pin validation, corpus observations,
    capability checks or protected-worker execution in run_stage_gate().
    """
    report = aggregate_campaign(plan, evidence_root, expected_plan_hash=expected_plan_hash, expected_run_id=expected_run_id)
    if not report['pytest_scope_passed']:
        raise ValueError('cannot export unsuccessful pytest evidence')
    if plan['profile'] == 'smoke' or any((t['nodeids_hash'] != t['full_inventory_hash'] for t in plan['tasks'])):
        raise ValueError('a subset cannot be exported as a full component receipt')
    ensure_external_output(output, {k: Path(v) for k, v in plan['roots'].items()})
    if output.exists():
        raise ValueError('suite export already exists')
    output.mkdir(parents=True)
    run = read_json(evidence_root / 'run.json')
    exports = {}
    for task in plan['tasks']:
        reports = {}
        for attempt in run['attempts']:
            if attempt['task'] == task['id']:
                receipt = read_artifact(evidence_root, attempt['artifacts']['phases'])
                reports.update(receipt['reports'])
        if validate_phase_reports(task['nodeids'], reports):
            raise ValueError('unexpected phase inconsistency on export')
        receipt = {'schema_id': 'openpine.test_inventory.v1', 'suite': task['component'], 'count': len(task['nodeids']), 'nodeids': task['nodeids'], 'sha256': collection_hash(task['nodeids']), 'deselected': task['deselected'], 'collect_only': False, 'ok': True, 'errors': [], 'reports': reports, 'source_candidate_hash': plan['source']['content_hash'], 'environment_hash': plan['environments'][task['environment']]['identity']['content_hash'], 'plan_hash': plan['content_hash'], 'run_id': expected_run_id, 'verified_aggregate_hash': report['content_hash'], 'scope': 'full component pytest inventory, not foundation or stage acceptance'}
        path = output / task['environment'] / (task['component'] + '.inventory.json')
        write_once_json(path, receipt)
        exports[task['id']] = descriptor(output, path)
    result = seal({'schema_id': 'openpine.test_suite_export.v1', 'ok': True, 'plan_hash': plan['content_hash'], 'exports': exports, 'full_stage_accepted': False})
    write_once_json(output / 'exports.json', result)
    return result
