"""Bounded owner composition through the real CLI and immutable primaries."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from openpine.verification import execution_ci as ci
from openpine.verification.execution_cli import add_commands
from openpine.verification.execution_identity import hash_file
from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal


def parser():
    result = argparse.ArgumentParser()
    add_commands(result.add_subparsers(dest='command', required=True))
    return result


def test_real_parser_accepts_explicit_owner_roots():
    args = parser().parse_args(['test-ci', 'stabilization', '--restored', '/restored',
        '--plan', '/plan', '--foundation', '/native', '--output', '/out', '--run-id', 'run',
        '--frontend', '/attempt', '--packages', '/package-attempt'])
    assert args.frontend == Path('/attempt') and args.packages == Path('/package-attempt')


def projection_fixture(tmp_path):
    measure = Path(__file__).parents[1] / 'scripts/rc6_stabilization/measure.py'
    root = tmp_path / 'package'
    root.mkdir()
    cwd = root / 'cwd'
    cwd.mkdir()
    version = '.'.join(map(str, sys.version_info[:2]))
    supplied, expected = [], []
    helper = root / 'helper.py'
    helper.write_text('# Independently frozen tiny harness input\n')
    harness = {'fixture-helper': {'path': str(helper), 'sha256': hash_file(helper)}}
    tools = {'owner-tool:runner': {'path': str(Path(sys.executable).resolve()),
                                  'sha256': hash_file(Path(sys.executable).resolve())}}
    for index, (label, role, tail) in enumerate([
        ('version-' + version + '-wheel', None, ['-c', f"import sys; assert '.'.join(map(str,sys.version_info[:2])) == {version!r}; print(sys.version); print(sys.executable)"]),
        ('install', 'install', ['-c', 'print("installed fixture")']),
        ('probe', 'probe', ['-c', 'print("probe fixture")']),
    ]):
        folder = root / 'logs' / f'{index:03d}-{label}'
        argv = [sys.executable, str(measure), str(folder / 'resources.json'), sys.executable, *tail]
        inputs = {**harness, **(tools if role else {})}
        spec = {'argv': argv, 'cwd': str(cwd), 'role': role, 'inputs': inputs}
        run_logged(argv, cwd=cwd, output=folder, timeout=30, env={'PATH': os.environ['PATH']}, inputs=inputs)
        supplied.append({'path': (folder / 'command.json').relative_to(root).as_posix(), 'sha256': hash_file(folder / 'command.json')})
        if role:
            expected.append(spec)
    return root, supplied, expected, version


def test_projection_keeps_exact_mandatory_sequence_and_version_bytes(tmp_path):
    from openpine.verification.execution_ci import project_package_commands
    root, supplied, expected, version = projection_fixture(tmp_path)
    frozen = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    projected = project_package_commands(root, supplied, expected, version, str(root))
    assert projected == supplied[1:]
    assert {p: p.read_bytes() for p in frozen} == frozen


@pytest.mark.parametrize('mutation', ['missing', 'reordered', 'duplicate', 'extra', 'tampered', 'provenance'])
def test_projection_fails_closed(tmp_path, mutation):
    from openpine.verification.execution_ci import project_package_commands
    root, supplied, expected, version = projection_fixture(tmp_path)
    if mutation == 'missing':
        supplied.pop()
    elif mutation == 'reordered':
        supplied[1:] = reversed(supplied[1:])
    elif mutation == 'duplicate':
        supplied.insert(0, supplied[0])
    elif mutation == 'extra':
        supplied.append(supplied[-1])
    elif mutation == 'tampered':
        (root / supplied[0]['path']).write_text('{}')
    else:
        expected[0]['inputs'] = {'required': {'path': '/frozen', 'sha256': 'sha256:' + '1'*64}}
    with pytest.raises((ValueError, OSError)):
        project_package_commands(root, supplied, expected, version, str(root))


@pytest.fixture(scope='module')
def owners(tmp_path_factory):
    from rc6_tests.stabilization_fixture import build_fixture
    base = tmp_path_factory.mktemp('composed-owners')
    host, plan, evidence, packet = build_fixture(base, portable=True, owner_namespaces=True)
    for path in evidence.rglob('*'):
        if path.is_symlink():
            path.unlink()  # Archive transports installed regular files, never aliases.
    (evidence / 'frontend/entry.json').write_text(json.dumps(packet['gates']['frontend']))
    stage = evidence / 'stage-packages-inputs.json'
    stage.write_text(json.dumps({'gates': {'packages': packet['gates']['packages']}}))
    (evidence / 'source-before.json').write_text(json.dumps(plan['source']))
    receipt = seal({'schema_id': 'openpine.installed_package_receipt.v1', 'owner': 'packages',
        'plan_hash': plan['content_hash'], 'source_hash': plan['source']['content_hash'],
        'run_id': packet['run_id'], 'development': False,
        'source': {'path': 'source-before.json', 'sha256': hash_file(evidence / 'source-before.json')},
        'stage_inputs': {'path': stage.name, 'sha256': hash_file(stage)}})
    (evidence / 'receipt.json').write_text(json.dumps(receipt))
    native = base / 'native'
    shutil.copytree(evidence / packet['campaign'], native / 'merged')
    for env, relative in packet['gates']['foundation']['environments'].items():
        shutil.copytree(evidence / relative, native / 'suites' / env)
    for task, relative in packet['gates']['coverage']['tasks'].items():
        shutil.copytree(evidence / relative, native / 'owner-coverage' / task)
    return host, plan, evidence, packet, native


def test_real_coordinator_composes_owners_and_keeps_performance_negative(owners, tmp_path):
    host, plan, evidence, packet, native = owners
    frozen = {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    output = tmp_path / 'common'
    result = ci.finalize_stabilization(plan, host, [native], output, packet['run_id'],
        frontend=evidence, packages=evidence)
    assert result == read_json(output / 'replayed-current.json')
    for gate in ('frontend', 'packages'):
        assert result['stabilization']['gates'][gate]['status'] == 'passed', result
    assert result['stabilization']['gates']['test-performance']['status'] == 'not_run'
    assert not result['ok'] and not result['full_stage2_accepted']
    assert {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob('*') if p.is_file()} == frozen
    locators = read_json(output / 'evidence/stabilization-inputs.json')
    for gate in ('frontend', 'packages'):
        supplied = copy.deepcopy(locators['gates'][gate])
        supplied.pop('evidence_subroot')
        assert supplied == packet['gates'][gate]


def test_real_cli_dispatch_threads_inputs_to_common_owner(owners, tmp_path):
    from openpine.verification.execution_cli import run_command
    from openpine.verification.execution_identity import environment_snapshot
    host, plan, evidence, packet, native = owners
    restored = tmp_path / 'restored.json'
    restored.write_text(json.dumps({'roots': plan['roots'], 'source_commits': plan['source_commits'],
                                   'environment': environment_snapshot()}))
    output = tmp_path / 'common'
    args = parser().parse_args(['test-ci', 'stabilization', '--restored', str(restored),
        '--plan', str(evidence / 'plan.json'), '--foundation', str(native), '--output', str(output),
        '--run-id', packet['run_id'], '--frontend', str(evidence), '--packages', str(evidence)])
    assert run_command(args) == 1
    current = read_json(output / 'current.json')
    assert current['stabilization']['gates']['frontend']['status'] == 'passed'
    assert current['stabilization']['gates']['packages']['status'] == 'passed'
    assert current == read_json(output / 'replayed-current.json')


@pytest.mark.parametrize('mutation', ['wrong-plan', 'wrong-run', 'wrong-source', 'missing-entry', 'tampered-packet', 'symlink'])
def test_coordinator_rejects_foreign_missing_detached_package_input(owners, tmp_path, mutation):
    host, plan, evidence, packet, native = owners
    package = tmp_path / 'package'
    shutil.copytree(evidence, package, ignore=lambda d, ns: [n for n in ns if (Path(d)/n).is_symlink()])
    path = package / 'receipt.json'
    receipt = read_json(path)
    if mutation.startswith('wrong-'):
        receipt[{'wrong-plan': 'plan_hash', 'wrong-run': 'run_id', 'wrong-source': 'source_hash'}[mutation]] = 'foreign'
        receipt.pop('content_hash')
        path.write_text(json.dumps(seal(receipt)))
    elif mutation == 'missing-entry':
        (package / 'stage-packages-inputs.json').unlink()
    elif mutation == 'tampered-packet':
        (package / 'stage-packages-inputs.json').write_text('{}')
    else:
        path.unlink()
        path.symlink_to(evidence / 'receipt.json')
    with pytest.raises((ValueError, OSError)):
        ci.finalize_stabilization(plan, host, [native], tmp_path / 'common', packet['run_id'], packages=package)


def test_workflow_downloads_same_run_owners_in_frozen_namespaces():
    import yaml
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/rc6-native.yml').read_text())
    steps = workflow['jobs']['stabilization']['steps']
    downloads = {s['with']['name']: s['with']['path'] for s in steps if s.get('uses', '').startswith('actions/download-artifact') and 'name' in s['with']}
    assert downloads['rc6-ui-${{ github.run_id }}'] == '${{ runner.temp }}/exact-owner/frontend'
    assert downloads['rc6-packages-${{ github.run_id }}'] == '${{ runner.temp }}/package-owner'
    command = next(s['run'] for s in steps if 'test-ci stabilization' in s.get('run', ''))
    assert '--frontend "$RUNNER_TEMP/exact-owner"' in command
    assert '--packages "$RUNNER_TEMP/package-owner"' in command


@pytest.mark.parametrize('mutation', ['missing', 'wrong-layout', 'tampered', 'symlink', 'overlap', 'traversal'])
def test_frontend_input_failclosed_controls(owners, tmp_path, mutation):
    host, plan, evidence, packet, native = owners
    frontend = tmp_path / 'frontend-input'
    shutil.copytree(evidence, frontend)
    output = tmp_path / 'common'
    if mutation == 'missing':
        (frontend / 'frontend/entry.json').unlink()
    elif mutation == 'wrong-layout':
        frontend = frontend / 'frontend'
    elif mutation == 'tampered':
        entry = read_json(frontend / 'frontend/entry.json')
        (frontend / entry['tests']['path']).write_text('{}')
    elif mutation == 'symlink':
        (frontend / 'unsafe').symlink_to(evidence, target_is_directory=True)
    elif mutation == 'overlap':
        output = frontend / 'common'
    else:
        frontend = frontend / '..' / frontend.name
    if mutation == 'tampered':
        result = ci.finalize_stabilization(plan, host, [native], output, packet['run_id'], frontend=frontend)
        assert result['stabilization']['gates']['frontend']['status'] == 'blocked'
        assert not result['ok'] and not result['full_stage2_accepted']
    else:
        with pytest.raises((ValueError, OSError)):
            ci.finalize_stabilization(plan, host, [native], output, packet['run_id'], frontend=frontend)


def test_composed_archive_replays_after_original_staging_removed(owners, tmp_path):
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.stage_gate import run_stabilization_gate
    host, plan, evidence, packet, native = owners
    original = evidence.parent
    output = original / 'common-archive'
    expected = ci.finalize_stabilization(plan, host, [native], output, packet['run_id'],
                                        frontend=evidence, packages=evidence)
    primaries = {p.relative_to(output / 'evidence'): p.read_bytes()
                 for p in (output / 'evidence').rglob('*') if p.is_file()}
    archive = tmp_path / 'archive'
    shutil.copytree(original, archive, ignore=lambda d, ns: [n for n in ns if (Path(d)/n).is_symlink()])
    roots = {n: archive / Path(p).relative_to(original) for n, p in plan['roots'].items()}
    binding = make_binding(plan, roots, {n: e['executable'] for n, e in plan['environments'].items()})
    binding.pop('content_hash')
    binding['owner_paths'] = {str(evidence): str(archive / 'common-archive/evidence/owners/frontend')}
    binding = seal(binding)
    shutil.rmtree(original)
    restored_evidence = archive / 'common-archive/evidence'
    result = run_stabilization_gate(roots['openpine'], plan, restored_evidence,
        expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=binding)
    assert result == expected
    assert {p.relative_to(restored_evidence): p.read_bytes() for p in restored_evidence.rglob('*') if p.is_file()} == primaries
    incomplete = copy.deepcopy(binding)
    incomplete.pop('content_hash')
    incomplete['owner_paths'] = {}
    blocked = run_stabilization_gate(roots['openpine'], plan, restored_evidence,
        expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=seal(incomplete))
    for gate in ('frontend', 'packages'):
        assert blocked['stabilization']['gates'][gate]['status'] == 'blocked'
    assert not blocked['ok'] and not blocked['full_stage2_accepted']
