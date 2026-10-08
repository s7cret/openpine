"""Small committed Git fixtures exercise admission; no product campaign claim."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from int05_tests.affected_from_ci import affected_from_ci
from int05_tests.freeze_real_patches import freeze
from openpine.verification.execution_ci import COMPONENTS, bundle_manifest, create_source_archive, make_ci_plan
from openpine.verification.execution_identity import clean_environment, environment_snapshot, source_snapshot, write_once_json
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import digest, read_json, seal
from openpine.verification.pytest_gate import validate_inventory
from rc6_tests.test_rc6_execution_platform import lock, reseal


def test_reviewed_host_selectors_collect_original_narrow_local_oracle(tmp_path):
    host = Path(__file__).resolve().parents[1]
    policy = read_json(host / 'verification/execution-policy.json')
    scenario = next(s for s in read_json(host / 'int05_tests/patch_scenarios.json')
                    if s['id'] == 'narrow-local')
    oracle = scenario['oracle']
    oracle_file = Path(oracle.split('::', 1)[0])
    selectors = (policy['components']['openpine']['selectors']
                 + read_json(host / 'rc6_tests/selected_regressions.json'))
    assert any(oracle_file == Path(selector) or Path(selector) in oracle_file.parents
               for selector in selectors), 'effective canonical host inventory excludes the reviewed narrow-local oracle'
    reviewed = lock([oracle])
    write_once_json(tmp_path / 'target-lock.json', {'int05-narrow-oracle': reviewed})
    argv = [sys.executable, '-B', '-m', 'pytest', '-q', '--collect-only',
            '-p', 'openpine.verification.pytest_gate', '--verification-suite=int05-narrow-oracle',
            '--verification-lock=' + str(tmp_path / 'target-lock.json'),
            '--verification-output=' + str(tmp_path / 'collection.json'), oracle]
    result = subprocess.run(argv, cwd=host, capture_output=True, text=True, timeout=90,  # noqa: S603 -- actual current interpreter, explicit named product node and owner plugin
        env=clean_environment({'openpine': str(host)}, tmp_path / 'private'))
    (tmp_path / 'collection.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = read_json(tmp_path / 'collection.json')
    validate_inventory(receipt['nodeids'], reviewed, receipt['deselected'])
    assert receipt['collect_only'] is True and receipt['errors'] == []
    assert receipt['nodeids'] == [oracle] and receipt['reports'] == {}


def committed_candidate(tmp_path, *, patch='executable-edit', two_environments=False,
                        relative='test_value.py'):
    git_exe = shutil.which('git')
    assert git_exe is not None

    def git(root, *args):
        return subprocess.run([git_exe, '-C', str(root), *args], capture_output=True,  # noqa: S603 -- resolved Git executable and local fixture arguments
                              check=True, text=True).stdout.strip()

    def commit(root, message):
        git(root, 'add', '.')
        git(root, '-c', 'user.name=INT05 fixture', '-c', 'user.email=int05@example.invalid',
            'commit', '-qm', message)
        return git(root, 'rev-parse', 'HEAD')

    roots, commits = {}, {}
    before = 'def test_value():\n    assert True\n'
    for owner in COMPONENTS:
        root = tmp_path / 'stack' / owner
        root.mkdir(parents=True)
        roots[owner] = root
        git(root, 'init', '-q')
        if owner != 'openpine':
            (root / 'test_value.py').write_text(before)
            commits[owner] = commit(root, owner)
    host = roots['openpine']
    policy = {'components': {n: {} for n in COMPONENTS},
              'required_gates': {'stage-full': ['foundation']}}
    policy['components']['openpine']['dependencies'] = [n for n in COMPONENTS if n != 'openpine']
    write_once_json(host / 'verification/execution-policy.json', policy)
    write_once_json(host / 'docs/RC6_LIFECYCLE_SOURCES.json', commits)
    target = host / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if patch != 'addition':
        target.write_text(before)
        target.chmod(0o755 if patch == 'executable-edit' else 0o644)
    commit(host, 'real parent preimage')
    after = before if patch == 'mode-only' else before + '# actual Git edit\n'
    target.write_text(after)
    target.chmod(0o755)
    commits['openpine'] = commit(host, 'actual single-file edit')
    source, environment = source_snapshot(roots), environment_snapshot()
    environments = {'py313': {'identity': environment, 'executable': sys.executable}}
    if two_environments:
        environments['py313-secondary'] = copy.deepcopy(environments['py313'])
    nodes = [relative + '::test_value']
    owner_nodes = {n: nodes if n == 'openpine' else ['test_value.py::test_value'] for n in COMPONENTS}
    collection = seal({'schema_id': 'openpine.test_collection_set.v1', 'source': source,
        'roots': {n: str(p) for n, p in roots.items()}, 'policy_hash': digest(policy),
        'environments': environments, 'inventories': {n + '@' + env: {
            'nodeids': owner_nodes[n], 'node_markers': {node: [] for node in owner_nodes[n]},
            'reviewed_lock': lock(owner_nodes[n]),
            'source_hash': source['content_hash'], 'environment_hash': environment['content_hash'],
            'deselected': 0} for n in COMPONENTS for env in environments},
        'ok': True, 'errors': [], 'collect_only': True, 'execution_pass': False})
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    collection_path = bundle / 'collection.json'
    write_once_json(collection_path, collection)
    create_source_archive(roots, bundle / 'sources.tar.gz')
    prepared = bundle_manifest(bundle, source=source, environment=environment,
        source_pins={n: commits[n] for n in COMPONENTS if n != 'openpine'},
        host_commit=commits['openpine'])
    full = make_ci_plan([collection_path], roots, tmp_path / 'full.json', commits)
    scenario = {'id': 'actual-edit', 'owner': 'openpine', 'path': relative,
                'oracle': nodes[0], 'expected_owners': ['openpine']}
    return roots, collection, bundle, prepared, full, scenario


@pytest.mark.parametrize('patch', ['executable-edit', 'mode-only', 'addition'])
@pytest.mark.parametrize('relative', ['test_value.py', 'src/test_value.py'])
def test_real_git_patch_replay_preserves_bytes_and_executable_bit(tmp_path, patch, relative):
    roots, collection, _, _, _, scenario = committed_candidate(tmp_path, patch=patch, relative=relative)
    output = tmp_path / 'real-patches'
    result = freeze(collection, [scenario], output)
    row = result['rows'][0]
    replay = output / scenario['id'] / 'replayed-file' / scenario['path']
    assert replay.read_bytes() == (roots['openpine'] / scenario['path']).read_bytes()
    assert replay.stat().st_mode & 0o111
    assert row['after_mode'] == '100755'
    assert row['before_mode'] == ({'executable-edit': '100755', 'mode-only': '100644',
                                   'addition': None}[patch])
    assert row['patch_replay_matches_after']
    assert result['source_hash'] == collection['source']['content_hash']
    assert not result['execution_performed'] and not result['selection_qualified']


@pytest.mark.parametrize('mutation', ['scenario-path', 'scenario-id', 'scope', 'oracle',
                                    'omitted-owner', 'policy'])
def test_patch_freeze_rejects_unreviewed_inputs_before_creating_packet(tmp_path, mutation):
    _, collection, _, _, _, scenario = committed_candidate(tmp_path)
    if mutation == 'scenario-path':
        scenario['path'] = '../outside.py'
    elif mutation == 'scenario-id':
        scenario['id'] = '../outside'
    elif mutation == 'scope':
        scenario['expected_owners'] = ['pine2ast']
    elif mutation == 'oracle':
        scenario['oracle'] = 'test_value.py::test_unreviewed'
    elif mutation == 'omitted-owner':
        del collection['inventories']['optimizer@py313']
        collection = reseal(collection)
    else:
        collection['policy_hash'] = 'sha256:' + 'f' * 64
        collection = reseal(collection)
    output = tmp_path / 'refused-patches'
    with pytest.raises(ValueError):
        freeze(collection, [scenario], output)
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['omitted-owner', 'reviewed-lock', 'deselected', 'owner-gate'])
def test_affected_admission_rejects_structurally_valid_full_drift(tmp_path, mutation):
    _, _, bundle, prepared, full, _ = committed_candidate(tmp_path)
    if mutation == 'omitted-owner':
        full['tasks'] = [t for t in full['tasks'] if t['component'] != 'optimizer']
    elif mutation == 'owner-gate':
        full['required_gates'] = ['another-owner']
    else:
        task = next(t for t in full['tasks'] if t['component'] == 'optimizer')
        if mutation == 'reviewed-lock':
            task['reviewed_lock_hash'] = 'sha256:' + 'f' * 64
        else:
            task['deselected'] = 1
    full = reseal(full)
    assert validate_plan(full) == full
    output = tmp_path / 'refused-affected.json'
    with pytest.raises(ValueError, match='CI full'):
        affected_from_ci(bundle, full, expected_bundle_hash=prepared['content_hash'],
            expected_full_hash=full['content_hash'], changes=['openpine/test_value.py'],
            expected_owners=['openpine'], output=output)
    assert not output.exists()


def test_affected_owner_scope_is_unique_across_interpreter_selectors(tmp_path):
    _, _, bundle, prepared, full, _ = committed_candidate(tmp_path, two_environments=True)
    output = tmp_path / 'affected.json'
    plan = affected_from_ci(bundle, full, expected_bundle_hash=prepared['content_hash'],
        expected_full_hash=full['content_hash'], changes=['openpine/test_value.py'],
        expected_owners=['openpine'], output=output)
    assert {t['component'] for t in plan['tasks']} == {'openpine'}
    assert {t['environment'] for t in plan['tasks']} == {'py313', 'py313-secondary'}
    assert plan['source_commits'] == full['source_commits']
    assert plan['source'] == full['source']
