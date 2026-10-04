"""Archived owner readers preserve primaries; copied installations are not executions."""
import shutil
from pathlib import Path

import pytest

from openpine.verification.execution_binding import make_binding
from openpine.verification.identity import seal
from openpine.verification.stage_gate import run_stabilization_gate
from rc6_tests.stabilization_fixture import build_fixture


@pytest.mark.parametrize('mutation', ['empty', 'missing', 'symlink', 'traversal', 'relative'])
def test_owner_locator_fails_closed(tmp_path, mutation):
    from openpine.verification.stabilization_evidence import replay_path
    historical = tmp_path / 'old'
    historical.mkdir()
    archive = tmp_path / 'archive'
    archive.mkdir()
    mappings = {str(historical): str(archive)}
    if mutation == 'empty':
        mappings = {}
    elif mutation == 'missing':
        mappings = {str(tmp_path / 'other'): str(archive)}
    elif mutation == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(archive, target_is_directory=True)
        mappings[str(historical)] = str(link)
    elif mutation == 'traversal':
        mappings[str(historical)] = str(archive / '..' / 'archive')
    else:
        mappings[str(historical)] = 'relative'
    with pytest.raises((ValueError, OSError)):
        replay_path(historical, mappings)


@pytest.mark.parametrize('launch', ['launch-a', 'launch-b'])
def test_owner_archive_replay_without_original_staging(tmp_path, launch):
    original = tmp_path / launch
    original.mkdir()
    host, plan, evidence, packet = build_fixture(original)
    expected = run_stabilization_gate(host, plan, evidence,
                                     expected_plan_hash=plan['content_hash'], run_id=packet['run_id'])
    assert all(g['status'] == 'passed' for g in expected['stabilization']['gates'].values())
    primaries = {str(p.relative_to(evidence)): p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    archive = tmp_path / 'third-archive'
    # Copy only regular bytes, not a runnable relocated virtualenv or symlinks.
    shutil.copytree(original, archive, ignore=lambda directory, names: [n for n in names if (Path(directory) / n).is_symlink()])
    roots = {name: archive / Path(path).relative_to(original) for name, path in plan['roots'].items()}
    binding = make_binding(plan, roots, {name: env['executable'] for name, env in plan['environments'].items()})
    binding.pop('content_hash')
    binding['owner_paths'] = {str(original): str(archive)}
    binding = seal(binding)
    shutil.rmtree(original)
    assert not original.exists()
    moved_evidence = archive / evidence.relative_to(original)
    actual = run_stabilization_gate(roots['openpine'], plan, moved_evidence,
                                   expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=binding)
    assert actual == expected, actual['stabilization']['gates']
    assert {str(p.relative_to(moved_evidence)): p.read_bytes() for p in moved_evidence.rglob('*') if p.is_file()} == primaries
    assert plan == __import__('json').loads(primaries['plan.json'])
    # Missing mappings must not revive historical paths or authorize a new owner.
    incomplete = dict(binding)
    incomplete.pop('content_hash')
    incomplete['owner_paths'] = {str(evidence): str(moved_evidence)}
    missing = run_stabilization_gate(roots['openpine'], plan, moved_evidence,
                                    expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=seal(incomplete))
    assert missing['stabilization']['gates']['packages']['status'] == 'blocked'
    version = next(iter(packet['gates']['packages']))
    probe = __import__('json').loads((moved_evidence / packet['gates']['packages'][version]['normal']['probe']['path']).read_text())
    component = next(iter(probe['components'].values()))
    relative = next(iter(component['files']))
    installed = archive / Path(probe['prefix']).relative_to(original) / relative
    before = installed.read_bytes()
    installed.write_bytes(before + b'changed')
    tampered = run_stabilization_gate(roots['openpine'], plan, moved_evidence,
                                     expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=binding)
    assert tampered['stabilization']['gates']['packages']['status'] == 'blocked'
    installed.write_bytes(before)
    # Independent wheel/source authorities must reject changed transported bytes.
    staged = archive / Path(packet['gates']['frontend']['binding']['source_root']).relative_to(original)
    (staged / 'package.json').write_text('{}')
    rejected = run_stabilization_gate(roots['openpine'], plan, moved_evidence,
                                     expected_plan_hash=plan['content_hash'], run_id=packet['run_id'], binding=binding)
    assert rejected['stabilization']['gates']['frontend']['status'] == 'blocked'
