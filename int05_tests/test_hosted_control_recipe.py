"""Negative contracts for the hosted adapter; these are not qualification runs."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import json
import tarfile

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
