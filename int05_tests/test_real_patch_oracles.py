"""Real owner tests against real source mutations; bounded pre-review evidence.

This is deliberately a targeted oracle probe. Complete affected/full campaigns
must still be compared after the parent releases the heavy execution slot.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openpine.verification.execution_identity import clean_environment, hash_file, write_once_json
from openpine.verification.execution_plan import select_components
from openpine.verification.identity import read_json
from openpine.verification.pytest_gate import collection_hash, validate_inventory, validate_phase_reports

HOST = Path(__file__).resolve().parents[1]
SCENARIOS = read_json(Path(__file__).with_name('patch_scenarios.json'))
POLICY = read_json(HOST / 'verification/execution-policy.json')
ROOTS = {owner: HOST if owner == 'openpine' else HOST.parent / owner
         for owner in POLICY['components']}


def execute_oracle(folder, roots, owner, nodes):
    folder.mkdir()
    lock = {owner: {'count': len(nodes), 'sha256': collection_hash(nodes), 'deselected': 0}}
    write_once_json(folder / 'target-lock.json', lock)
    argv = [sys.executable, '-m', 'pytest', '-q', '-p', 'openpine.verification.pytest_gate',
            '--verification-suite=' + owner,
            '--verification-lock=' + str(folder / 'target-lock.json'),
            '--verification-output=' + str(folder / 'phases.json'),
            '--junitxml=' + str(folder / 'junit.xml'), *nodes]
    started = time.perf_counter()
    with (folder / 'stdout.log').open('x') as log:
        result = subprocess.run(argv, cwd=roots[owner],  # noqa: S603 -- fixed pytest argv; returncode and receipt checked
            env=clean_environment({n: str(p) for n, p in roots.items()}, folder / 'private'),
            stdout=log, stderr=subprocess.STDOUT, timeout=90)
    receipt = read_json(folder / 'phases.json')
    assert receipt['schema_id'] == 'openpine.test_inventory.v1'
    validate_inventory(receipt['nodeids'], lock[owner], receipt['deselected'])
    assert receipt['nodeids'] == sorted(nodes), (folder / 'stdout.log').read_text()
    return result.returncode, receipt, time.perf_counter() - started


@pytest.mark.parametrize('scenario', SCENARIOS, ids=lambda s: s['id'])
def test_real_patch_reaches_independently_named_owner_oracle(tmp_path, scenario):
    owner = scenario['owner']
    oracle_owner = scenario.get('oracle_owner', owner)
    copied = tmp_path / 'source'
    shutil.copytree(ROOTS[owner], copied,
        ignore=shutil.ignore_patterns('.git', '__pycache__', 'node_modules', '*.egg-info',
                                     '.pytest_cache', '.ruff_cache', '.mypy_cache'))
    roots = {**ROOTS, owner: copied}
    nodes = [scenario['oracle']]
    target = copied / scenario['path']
    before = target.read_text()
    assert scenario['before'] in before, 'patch scenario no longer matches the candidate'
    before_hash = hash_file(target)
    control_code, control, control_seconds = execute_oracle(tmp_path / 'control', roots, oracle_owner, nodes)
    assert control_code == 0 and control['ok'], (tmp_path / 'control/stdout.log').read_text()
    assert validate_phase_reports(nodes, control['reports']) == []

    target.write_text(before.replace(scenario['before'], scenario['after'], 1))
    # A new subprocess plus cache disposal prevents a same-size, same-second
    # source edit from accidentally executing the control's bytecode.
    for cache in copied.rglob('__pycache__'):
        shutil.rmtree(cache)
    changed = owner + '/' + scenario['path']
    selected, reasons = select_components(POLICY, 'affected', [], [changed])
    expected = sorted(POLICY['components']) if scenario['expected_owners'] == 'all' else scenario['expected_owners']
    mutation_code, mutation, mutation_seconds = execute_oracle(tmp_path / 'mutation', roots, oracle_owner, nodes)
    assert mutation_code == 1 and mutation['ok'] is False, (tmp_path / 'mutation/stdout.log').read_text()
    assert set(mutation['reports']) == set(nodes)
    assert any(row['outcome'] == 'failed' for row in mutation['reports'][nodes[0]])
    assert selected == expected
    assert oracle_owner in selected, 'real failing consumer oracle omitted by affected selection'
    write_once_json(tmp_path / 'checkpoint.json', {
        'scenario': scenario, 'changed': changed, 'before_sha256': before_hash,
        'after_sha256': hash_file(target), 'selected_owners': selected,
        'selection_reasons': reasons, 'targeted_nodeids': nodes,
        'control_phase_hash': hash_file(tmp_path / 'control/phases.json'),
        'mutation_phase_hash': hash_file(tmp_path / 'mutation/phases.json'),
        'control_seconds': control_seconds, 'mutation_seconds': mutation_seconds,
        'retries': 0, 'scope': 'one real product oracle per production patch; not full qualification',
        'full_affected_comparison_executed': False, 'full_acceptance': False,
    })


@pytest.mark.parametrize('owner', sorted(POLICY['components']))
def test_policy_smoke_executes_actual_owner(tmp_path, owner):
    nodes = POLICY['components'][owner]['smoke']
    code, receipt, seconds = execute_oracle(tmp_path / 'smoke', ROOTS, owner, nodes)
    assert code == 0 and receipt['ok'], (tmp_path / 'smoke/stdout.log').read_text()
    assert validate_phase_reports(nodes, receipt['reports']) == []
    write_once_json(tmp_path / 'checkpoint.json', {
        'owner': owner, 'nodeids': nodes, 'count': len(nodes),
        'phase_hash': hash_file(tmp_path / 'smoke/phases.json'), 'wall_seconds': seconds,
        'scope': 'actual owner smoke', 'full_acceptance': False, 'retries': 0,
    })
