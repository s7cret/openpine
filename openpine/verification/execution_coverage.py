"""Combine real coverage from complete component shards, keeping owner thresholds.

A single partial shard is not subject to a reduced threshold: `coverage run`
only records data. The unchanged pyproject threshold is applied to the union.
"""
from __future__ import annotations
import sys
from pathlib import Path
from openpine.verification.execution_binding import locations
from openpine.verification.execution_campaign import aggregate_campaign, descriptor
from openpine.verification.execution_identity import clean_environment, environment_snapshot, evidence_path, source_snapshot, write_once_json
from openpine.verification.identity import read_json, seal, verify
from openpine.verification.execution_process import run_logged

def combine_task_coverage(plan: dict, root: Path, task_id: str, output: Path, *, run_id: str, binding: dict | None=None) -> dict:
    tasks = [t for t in plan['tasks'] if t['id'] == task_id]
    if len(tasks) != 1:
        raise ValueError('unknown coverage task')
    task = tasks[0]
    if not task.get('coverage') or task['nodeids_hash'] != task['full_inventory_hash']:
        raise ValueError('coverage requires an instrumented full component, not a subset')
    keys = [(task_id, s['id']) for s in task['shards']]
    report = aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'], expected_run_id=run_id, expected_shards=keys)
    if not report['pytest_scope_passed']:
        raise ValueError('coverage cannot accept unsuccessful/incomplete test execution')
    roots, _ = locations(plan, binding)
    if environment_snapshot()['content_hash'] != plan['environments'][task['environment']]['identity']['content_hash'] or source_snapshot({n: Path(p) for n, p in roots.items()}) != plan['source']:
        raise ValueError('coverage owner runs on the wrong source/environment')
    from openpine.verification.execution_identity import ensure_external_output
    ensure_external_output(output, {n: Path(p) for n, p in roots.items()})
    output.mkdir(parents=True, exist_ok=False)
    run = read_json(root / 'run.json')
    covered_shards = {s['id'] for s in task['shards'] if s.get('coverage', task.get('coverage', False))}
    files = [str(evidence_path(root, a['artifacts']['coverage']['path'])) for a in run['attempts'] if a['task'] == task_id and a['shard'] in covered_shards]
    if not files:
        raise ValueError('no instrumented owner tests; coverage is not proven')
    config = Path(roots[task['component']]) / 'pyproject.toml'
    environment = clean_environment(roots, output / 'private')
    commands = [[sys.executable, '-m', 'coverage', 'combine', '--keep', '--rcfile=' + str(config), '--data-file=' + str(output / '.coverage'), *files], [sys.executable, '-m', 'coverage', 'json', '--rcfile=' + str(config), '--data-file=' + str(output / '.coverage'), '-o', str(output / 'coverage.json')], [sys.executable, '-m', 'coverage', 'report', '--rcfile=' + str(config), '--data-file=' + str(output / '.coverage')]]
    outcomes = []
    for index, argv in enumerate(commands):
        command_root = output / f'command-{index}'
        completed = run_logged(argv, cwd=roots[task['component']], output=command_root, env=environment, timeout=180)
        outcomes.append({'argv': argv, 'returncode': completed['returncode'], 'ok': completed['ok'], 'receipt': descriptor(output, command_root / 'command.json')})
        if not completed['ok']:
            break
    result = seal({'schema_id': 'openpine.component_coverage.v1', 'task': task_id, 'candidate_hash': plan['source']['content_hash'], 'plan_hash': plan['content_hash'], 'run_id': run_id, 'pytest_aggregate_hash': report['content_hash'], 'owner_config_sha256': plan['source']['components'][task['component']]['files']['pyproject.toml']['sha256'], 'commands': outcomes, 'threshold_policy': 'unchanged owner pyproject; no lowered shard threshold', 'source_unchanged': source_snapshot({n: Path(p) for n, p in roots.items()}) == plan['source'], 'ok': len(outcomes) == 3 and all((row['ok'] for row in outcomes)) and (source_snapshot({n: Path(p) for n, p in roots.items()}) == plan['source']), 'json': descriptor(output, output / 'coverage.json') if (output / 'coverage.json').is_file() else None, 'full_stage_accepted': False})
    write_once_json(output / 'receipt.json', result)
    return result

def verify_task_coverage(plan: dict, root: Path, task_id: str, output: Path, *, run_id: str) -> dict:
    """Re-read the existing coverage owner's command/data receipts, not a PASS flag."""
    from openpine.verification.execution_identity import hash_file, read_artifact
    tasks = [t for t in plan['tasks'] if t['id'] == task_id]
    if len(tasks) != 1 or not tasks[0].get('coverage'):
        raise ValueError('unknown or uninstrumented coverage task')
    task = tasks[0]
    keys = [(task_id, s['id']) for s in task['shards']]
    aggregation = aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'], expected_run_id=run_id, expected_shards=keys)
    receipt = read_json(output / 'receipt.json')
    verify(receipt, 'openpine.component_coverage.v1')
    config_hash = plan['source']['components'][task['component']]['files']['pyproject.toml']['sha256']
    if not aggregation['pytest_scope_passed'] or receipt.get('ok') is not True or receipt.get('source_unchanged') is not True or (receipt.get('task') != task_id) or (receipt.get('candidate_hash') != plan['source']['content_hash']) or (receipt.get('plan_hash') != plan['content_hash']) or (receipt.get('run_id') != run_id) or (receipt.get('pytest_aggregate_hash') != aggregation['content_hash']) or (receipt.get('owner_config_sha256') != config_hash):
        raise ValueError('coverage receipt does not accept this exact component execution')
    commands = receipt.get('commands', [])
    if len(commands) != 3:
        raise ValueError('incomplete coverage combine/json/report commands')
    for index, expected_command in enumerate(('combine', 'json', 'report')):
        entry = commands[index]
        actual = read_artifact(output, entry['receipt'])
        verify(actual, 'openpine.execution_command.v1')
        if not actual.get('ok') or actual.get('returncode') != 0 or actual.get('status') != 'completed' or (entry.get('ok') is not True) or (actual['argv'] != entry['argv']) or (actual['argv'][1:4] != ['-m', 'coverage', expected_command]):
            raise ValueError('coverage command did not complete successfully')
        command_root = evidence_path(output, entry['receipt']['path']).parent
        for relative, expected in actual['files'].items():
            if hash_file(evidence_path(command_root, relative)) != expected:
                raise ValueError('coverage command log/input checksum mismatch')
    data = read_artifact(output, receipt['json'])
    if not isinstance(data, dict) or not isinstance(data.get('files'), dict) or (not data['files']) or (not isinstance(data.get('totals'), dict)):
        raise ValueError('missing or malformed coverage observations')
    return receipt
