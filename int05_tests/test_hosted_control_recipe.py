"""Negative contracts for the hosted adapter; these are not qualification runs."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import json
import tarfile
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def recipe():
    spec = importlib.util.spec_from_file_location(
        'int05_hosted_control', ROOT / 'scripts/int05_hosted_control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('mutation', ['host', 'pinelib', 'missing', 'extra'])
def test_control_rejects_changed_producer_identity(mutation):
    owner = recipe()
    commits = deepcopy(owner.COMMITS)
    if mutation in ('host', 'pinelib'):
        commits['openpine' if mutation == 'host' else mutation] = 'a' * 40
    elif mutation == 'missing':
        commits.pop('optimizer')
    else:
        commits['unknown'] = 'a' * 40
    with pytest.raises(ValueError, match='exact reviewed eight-package candidate'):
        owner.require_candidate(commits)


def test_reviewed_control_identity_is_accepted():
    recipe().require_candidate(recipe().COMMITS)


def test_workflow_bounds_one_control_and_keeps_product_checkout_frozen():
    workflow = yaml.safe_load((ROOT / '.github/workflows/rc6-preparation.yml').read_text())
    job = workflow['jobs']['int05-control']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert job['timeout-minutes'] == 120
    assert 'strategy' not in job and 'container' not in job
    assert workflow['concurrency']['cancel-in-progress'] is False
    steps = job['steps']
    candidates = [step for step in steps if step.get('with', {}).get('path') == 'candidate']
    assert len(candidates) == 1
    assert candidates[0]['with']['ref'] == '4eee24fc458a2099f000211850f720ccfc6bfb2b'
    assert candidates[0]['with']['persist-credentials'] is False
    commands = '\n'.join(step.get('run', '') for step in steps)
    assert commands.count('int05_hosted_control.py" control') == 1
    assert commands.count('test-ci prepare') == 1
    launches = [step['run'] for step in steps if step.get('name') in (
        'Stock prepare exact eight-package candidate', 'Restore once through the stock owner')]
    assert len(launches) == 2
    assert all('-- "$INT05_PYTHON" -m openpine.verification' in command for command in launches)
    assert 'workflow_dispatch' in job['if']
    assert 'reviewed_workflow_sha' in job['if'] and 'github.sha' in job['if']
    assert 'steps.evidence.outputs.public_ready' in steps[-1]['if']


def test_public_owner_refuses_required_archive_without_touching_bytes(tmp_path):
    from scripts.rc6_public_evidence import audit
    source = tmp_path / 'sources.tar.gz'
    source.write_bytes(b'bounded archive placeholder; not qualification')
    before = source.read_bytes()
    report = audit(tmp_path, candidate='4eee24fc458a2099f000211850f720ccfc6bfb2b',
                   run_id='int05-public-boundary', max_bytes=1024)
    assert report['ok'] is False
    assert report['errors'] == [{'path': 'sources.tar.gz', 'reason': 'outside-public-allowlist'},
                                {'reason': 'empty-public-evidence'}]
    assert source.read_bytes() == before


def test_retention_seals_failed_primary_and_inputs_but_withholds_unapproved_upload(tmp_path, monkeypatch):
    root = tmp_path / 'attempt'
    root.mkdir()
    required = {
        'prepare/host.tar': b'unchanged committed source archive',
        'prepare/bundle/sources.tar.gz': b'unchanged sources archive',
        'prepare/bundle/git/openpine.bundle': b'unchanged Git provenance',
        'prepare/commands/0039/stderr.log': b'failed prerequisite original log',
        'raw/control-full/owner/phases.json': b'{"reports":{}}',
    }
    for relative, data in required.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    output = tmp_path / 'github-output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setenv('ARTIFACT_BUDGET', str(2 * 1024**2))
    monkeypatch.setenv('INT05_WORKFLOW_SHA', 'b' * 40)
    with pytest.raises(ValueError, match='reviewed durable transport required before dispatch'):
        recipe().retain(root, 'retention-contract')
    assert output.read_text() == 'public_ready=false\n'
    assert all((root / relative).read_bytes() == data for relative, data in required.items())
    with tarfile.open(root / 'archive/evidence.tar.gz') as archive:
        assert archive.extractfile('committed-host.tar').read() == required['prepare/host.tar']
        assert archive.extractfile('prepared-bundle/sources.tar.gz').read() == required['prepare/bundle/sources.tar.gz']
        assert archive.extractfile('prepared-bundle/git/openpine.bundle').read() == required['prepare/bundle/git/openpine.bundle']
        assert archive.extractfile('prepare-commands/0039/stderr.log').read() == required['prepare/commands/0039/stderr.log']
        assert archive.extractfile('control-full/owner/phases.json').read() == required['raw/control-full/owner/phases.json']
    audit = json.loads((root / 'public-evidence-audit.json').read_text())
    assert audit['ok'] is False and audit['original_primaries_changed'] is False


@pytest.mark.parametrize('absolute', [False, True])
def test_stock_monitor_uses_real_declared_process_absolute_boundary(tmp_path, monkeypatch, absolute):
    """Execute the real operational wrapper/launcher, never a mocked owner."""
    spec = importlib.util.spec_from_file_location(
        'int05_monitor_contract', ROOT / 'scripts/int05_monitor_stock_command.py')
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    output = tmp_path / 'monitor'
    monkeypatch.setattr(sys, 'argv', ['monitor', '--output', str(output), '--cwd', str(tmp_path),
                                    '--minimum-free-bytes', '0', '--',
                                    sys.executable if absolute else 'python', '-c',
                                    'print("int05-declared-child")'])
    if absolute:
        assert monitor.main() == 0
        assert (output / 'stdout.log').read_text().strip() == 'int05-declared-child'
        report = json.loads((output / 'measurement.json').read_text())
        assert report['returncode'] == 0 and report['argv'][0] == sys.executable
    else:
        with pytest.raises(ValueError, match='absolute'):
            monitor.main()
        assert (output / 'stdout.log').read_bytes() == b''


def test_elapsed_deadline_refuses_launch_and_retains_resource_receipt(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'int05_monitor_deadline', ROOT / 'scripts/int05_monitor_stock_command.py')
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    output = tmp_path / 'monitor'
    monkeypatch.setattr(sys, 'argv', ['monitor', '--output', str(output), '--cwd', str(tmp_path),
                                    '--minimum-free-bytes', '0', '--deadline-epoch', '1',
                                    '--', sys.executable, '-c', 'raise SystemExit(99)'])
    assert monitor.main() == 1
    report = json.loads((output / 'measurement.json').read_text())
    assert report['deadline_cancelled'] is True and report['returncode'] is None
    assert (output / 'stdout.log').read_bytes() == b''


def test_raw_retention_cap_failure_keeps_inventory_and_audit_before_any_archive(tmp_path, monkeypatch):
    root = tmp_path / 'attempt'
    source = root / 'prepare/bundle/sources.tar.gz'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'x' * 256)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'output'))
    monkeypatch.setenv('ARTIFACT_BUDGET', '64')
    monkeypatch.setenv('INT05_WORKFLOW_SHA', 'b' * 40)
    with pytest.raises(ValueError, match='uncompressed byte cap'):
        recipe().retain(root, 'raw-cap-boundary')
    inventory = json.loads((root / 'retention-inventory.json').read_text())
    assert inventory['total_bytes'] == 256
    assert (root / 'retention-preflight-audit.json').is_file()
    failure = json.loads((root / 'retention-failure.json').read_text())
    assert failure['full_scope_public_audit_completed'] is False
    assert failure['originals_retained'] is True
    assert not (root / 'archive').exists() and source.read_bytes() == b'x' * 256
