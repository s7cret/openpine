"""Bounded process shards and exact aggregation for OpenPine verification.

No process, phase receipt, JUnit, or external owner gate is inferred from a
summary count. A successful scoped campaign is not stage or release acceptance.
"""
from __future__ import annotations
import concurrent.futures
import json
import os
import re
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
from openpine.verification.execution_process import _stop_group
RUN_SCHEMA = 'openpine.test_campaign_run.v1'
AGGREGATE_SCHEMA = 'openpine.test_campaign_aggregate.v1'

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

def _execute_shard(plan: dict, plan_path: Path, output: Path, task: dict, shard: dict, run_id: str, cancellation: threading.Event, binding: dict | None=None, build_commit: str | None=None) -> dict:
    attempt = 'a001'
    relative = task['id'] + '/' + shard['id'] + '/' + attempt
    folder = output / relative
    folder.mkdir(parents=True, exist_ok=False)
    execution_roots, executables = locations(plan, binding)
    env = clean_environment(execution_roots, folder / 'private', build_commit=build_commit if task['component'] == 'openpine' else None)
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
                process = subprocess.Popen(argv, cwd=execution_roots[task['component']], env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=os.name == 'posix')  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
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
    except (OSError, ValueError) as caught:
        status, error = ('infrastructure_error', str(caught))
    finally:
        if process is not None and process.poll() is None:
            _stop_group(process)
        elif process is not None and os.name == 'posix':
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:  # noqa: S110 -- absent process/resource is already cleaned up
                pass
            else:
                _stop_group(process)
                if status == 'completed':
                    status, error = ('failed', 'orphan child process after pytest exit')
    artifacts = {}
    for key, filename in (('phases', 'phases.json'), ('junit', 'junit.xml'), ('stdout', 'stdout.log'), ('selectors', 'nodeids.args'), ('coverage', '.coverage')):
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
    result = {'task': task['id'], 'shard': shard['id'], 'attempt_id': attempt, 'run_id': run_id, 'status': status, 'returncode': returncode, 'error': error, 'started_at': started, 'finished_at': utc_now(), 'wall_seconds': time.perf_counter() - tick, 'argv': argv, 'cwd': execution_roots[task['component']], 'artifacts': artifacts, 'binding_hash': binding['content_hash'] if binding else None}
    write_once_json(folder / 'execution.json', result)
    return result

def run_campaign(plan: dict, plan_path: Path, output: Path, *, jobs: int=1, run_id: str | None=None, shard_keys: list[tuple[str, str]] | None=None, binding: dict | None=None, memory_mib: int | None=None, build_commit: str | None=None) -> dict:
    validate_plan(plan)
    if build_commit is not None and build_commit != plan.get('source_commits', {}).get('openpine'):
        raise ValueError('campaign build commit differs from frozen source plan')
    from openpine.verification.execution_resources import resource_profile
    observed_resources = resource_profile()
    available = observed_resources['cpu_slots']
    if type(jobs) is not int or not 1 <= jobs <= available:
        raise ValueError(f'jobs must be between 1 and the observed CPU budget ({available})')
    all_keys = {(t['id'], s['id']) for t in plan['tasks'] for s in t['shards']}
    selected = all_keys if shard_keys is None else set(shard_keys)
    if not selected or not selected.issubset(all_keys) or (shard_keys is not None and len(selected) != len(shard_keys)):
        raise ValueError('fragment selects empty, unknown or duplicate shards')
    execution_roots, executables = locations(plan, binding)
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
                used = sum((t['cpu_slots'] for t, _ in running.values()))
                reserved_memory = sum((t.get('memory_mib', 256) for t, _ in running.values()))
                groups = {t['exclusive_group'] for t, _ in running.values() if t['exclusive_group']}
                for task, shard in list(queued):
                    if used + task['cpu_slots'] > jobs or (memory_mib is not None and reserved_memory + task.get('memory_mib', 256) > memory_mib) or (task['exclusive_group'] and task['exclusive_group'] in groups):
                        continue
                    future = pool.submit(_execute_shard, plan, plan_path.resolve(), output.resolve(), task, shard, run_id, cancellation, binding, build_commit)
                    running[future] = (task, shard)
                    queued.remove((task, shard))
                    used += task['cpu_slots']
                    reserved_memory += task.get('memory_mib', 256)
                    if task['exclusive_group']:
                        groups.add(task['exclusive_group'])
                done, _ = concurrent.futures.wait(running, timeout=0.1, return_when=concurrent.futures.FIRST_COMPLETED)
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
    if memory_mib is not None and sample_count == 0:
        errors.append('memory-budget monitoring produced no valid observations')
    after_hash = None
    try:
        after_hash = source_snapshot(roots)['content_hash']
        if after_hash != before['content_hash']:
            errors.append('source changed during campaign')
    except (OSError, ValueError) as error:
        errors.append(str(error))
    run = seal({'schema_id': RUN_SCHEMA, 'run_id': run_id, 'plan_hash': plan['content_hash'], 'candidate_hash': plan['source']['content_hash'], 'source_before': before['content_hash'], 'source_after': after_hash, 'started_at': started, 'finished_at': utc_now(), 'wall_seconds': time.perf_counter() - tick, 'jobs': jobs, 'affinity_cpus': observed_resources['affinity_cpus'], 'resource_profile': observed_resources, 'binding': descriptor(output, output / 'binding.json') if binding is not None else None, 'selection': [list(key) for key in sorted(selected)], 'is_fragment': selected != all_keys, 'memory_budget_mib': memory_mib, 'memory_enforcement': 'reservation plus sampled RSS cancellation; not a kernel hard limit' if memory_mib else 'not_requested', 'sampled_process_tree_peak_rss_bytes': peak_total_rss if sample_count else None, 'rss_samples': sample_count, 'rss_sampling_errors': rss_sampling_errors, 'rss_sampling_is_exact_peak': False, 'attempts': sorted(results, key=lambda r: (r['task'], r['shard'])), 'errors': errors})
    write_once_json(output / 'run.json', run)
    return run

def aggregate_campaign(plan: dict, evidence_root: Path, *, expected_plan_hash: str, expected_run_id: str, expected_shards: list[tuple[str, str]] | None=None) -> dict:
    validate_plan(plan, expected_hash=expected_plan_hash)
    run = read_json(evidence_path(evidence_root, 'run.json'))
    verify(run, RUN_SCHEMA)
    errors = list(run['errors'])
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
            artifacts = attempt['artifacts']
            for desc in artifacts.values():
                if hash_file(evidence_path(evidence_root, desc['path'])) != desc['sha256']:
                    raise ValueError('attempt artifact checksum mismatch')
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
