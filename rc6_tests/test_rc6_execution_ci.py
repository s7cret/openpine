"""CI transfer, process and marker contracts; no synthetic hosted-run PASS."""
from __future__ import annotations
import copy
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
import pytest
import yaml
from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_ci import bundle_manifest, create_source_archive, unpack_source_archive, verify_bundle
from openpine.verification.execution_coverage import combine_task_coverage, verify_task_coverage
from openpine.verification.execution_identity import environment_snapshot, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import make_plan
from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json
from rc6_tests.test_rc6_execution_platform import HOST, lock, tiny_plan

def test_foundation_passes_verified_restored_sources_to_coverage(tmp_path, monkeypatch):
    from openpine.verification import execution_ci as ci, execution_coverage as coverage
    from openpine.verification.identity import seal
    roots = {'openpine': tmp_path / 'restored'}
    frozen_source = {'content_hash': 'frozen-source'}
    plan = {'source': frozen_source, 'content_hash': 'frozen-plan'}
    fragment = tmp_path / 'fragment'
    (fragment / 'coverage-owner').mkdir(parents=True)
    (fragment / 'run.json').write_text('{}')
    (fragment / 'coverage-owner/receipt.json').write_text('{}')
    write_once_json(fragment / 'ci-task.json', seal({
        'schema_id': 'openpine.ci_task.v1', 'task': 'openpine@py311',
        'plan_hash': plan['content_hash'], 'run_id': 'test-run',
        'run_sha256': hash_file(fragment / 'run.json'),
        'coverage_sha256': hash_file(fragment / 'coverage-owner/receipt.json'),
    }))
    monkeypatch.setattr(ci, 'source_snapshot', lambda observed: frozen_source)

    class OwnerBoundaryReached(Exception):
        pass

    def checked_owner(*args, **kwargs):
        assert kwargs.get('source_roots') == roots
        raise OwnerBoundaryReached

    monkeypatch.setattr(coverage, 'verify_task_coverage', checked_owner)
    with pytest.raises(OwnerBoundaryReached):
        ci.finalize_foundation(plan, roots, [fragment], tmp_path / 'out', 'test-run')


def source_roots(tmp_path):
    roots = {}
    for name in ('openpine', 'pine2ast'):
        root = tmp_path / name
        root.mkdir()
        (root / 'source.py').write_text('answer = 42\n')
        (root / 'tests').mkdir()
        (root / 'tests/test_contract.py').write_text('def test_identity():\n    assert 6*7 == 42\n')
        (root / 'source.py').chmod(493)
        roots[name] = root
    return roots

def test_source_archive_roundtrip_preserves_every_input_and_executable_bit(tmp_path):
    roots = source_roots(tmp_path)
    manifest = create_source_archive(roots, tmp_path / 'sources.tar.gz')
    restored = unpack_source_archive(tmp_path / 'sources.tar.gz', tmp_path / 'restored', manifest)
    assert source_snapshot(restored) == manifest
    assert restored['openpine'].joinpath('source.py').stat().st_mode & 73
    assert (restored['pine2ast'] / 'tests/test_contract.py').read_bytes() == (roots['pine2ast'] / 'tests/test_contract.py').read_bytes()


def test_git_provenance_restores_exact_head_and_historical_objects_without_changing_sources(tmp_path):
    from openpine.verification.execution_ci import export_git_provenance, restore_git_provenance
    from openpine.verification.execution_identity import source_snapshot

    repo = tmp_path / 'pine2ast'
    repo.mkdir()
    git_exe = shutil.which('git')
    assert git_exe is not None
    def git(*args):
        return subprocess.run([git_exe, '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603 -- fixed test Git executable and argv
    git('init', '-q')
    git('config', 'user.email', 'ci@example.invalid')
    git('config', 'user.name', 'CI fixture')
    (repo / 'source.py').write_text('answer = 1\n')
    git('add', 'source.py')
    git('commit', '-qm', 'historical')
    historical = git('rev-parse', 'HEAD')
    (repo / 'source.py').write_text('answer = 42\n')
    git('add', 'source.py')
    git('commit', '-qm', 'candidate')
    candidate = git('rev-parse', 'HEAD')
    git('checkout', '--detach', candidate)
    bundle = tmp_path / 'provenance.bundle'
    export_git_provenance(repo, bundle, candidate)
    archive = tmp_path / 'sources.tar.gz'
    source = create_source_archive({'pine2ast': repo}, archive)
    restored = unpack_source_archive(archive, tmp_path / 'restored', source)['pine2ast']
    restore_git_provenance(restored, bundle, 'pine2ast', candidate)
    assert subprocess.run([git_exe, '-C', str(restored), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip() == candidate  # noqa: S603 -- fixed test Git executable and argv
    assert subprocess.run([git_exe, '-C', str(restored), 'symbolic-ref', '-q', 'HEAD'], capture_output=True).returncode == 1  # noqa: S603 -- fixed test Git executable and argv
    subprocess.run([git_exe, '-C', str(restored), 'cat-file', '-e', historical + '^{commit}'], check=True)  # noqa: S603 -- fixed test Git executable and argv
    assert source_snapshot({'pine2ast': restored}) == source
    tampered = unpack_source_archive(archive, tmp_path / 'tampered', source)['pine2ast']
    (tampered / 'source.py').write_text('tampered\n')
    with pytest.raises(ValueError, match='differs from archived source'):
        restore_git_provenance(tampered, bundle, 'pine2ast', candidate)
    (repo / 'untracked.py').write_text('unexpected_input = True\n')
    extra_archive = tmp_path / 'source-with-untracked.tar.gz'
    extra_source = create_source_archive({'pine2ast': repo}, extra_archive)
    extra = unpack_source_archive(extra_archive, tmp_path / 'with-untracked', extra_source)['pine2ast']
    with pytest.raises(ValueError, match='not present in Git commit'):
        restore_git_provenance(extra, bundle, 'pine2ast', candidate)


def test_compiler_commit_environment_uses_exact_plan_producer_identity(monkeypatch):
    from openpine.build_identity import compiler_producer_commits
    from openpine.verification.execution_campaign import compiler_commit_environment
    expected = {name: str(index) * 40 for index, name in enumerate(('pine2ast', 'ast2python', 'pinelib', 'openpine-contracts'), start=1)}
    value = compiler_commit_environment({'openpine': 'f' * 40, **expected})
    monkeypatch.setenv('OPENPINE_PRODUCER_COMMITS_JSON', value)
    assert compiler_producer_commits() == expected
    with pytest.raises(ValueError, match='producer commits'):
        compiler_commit_environment({'openpine': 'f' * 40})


@pytest.mark.parametrize('mutation', ['traversal', 'symlink', 'duplicate', 'missing', 'tamper', 'unknown'])
def test_source_archive_rejects_incomplete_or_unsafe_transfer(tmp_path, mutation):
    roots = source_roots(tmp_path)
    manifest = create_source_archive(roots, tmp_path / 'original.tar.gz')
    with tarfile.open(tmp_path / 'original.tar.gz') as original:
        entries = [(member, original.extractfile(member).read()) for member in original]
    if mutation == 'missing':
        entries.pop()
    elif mutation == 'duplicate':
        entries.append(entries[0])
    else:
        member, content = entries[0]
        member = copy.copy(member)
        if mutation == 'traversal':
            member.name = '../outside'
        elif mutation == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '../outside'
        elif mutation == 'unknown':
            member.name = 'unknown/source.py'
        else:
            content = b'x' * len(content)
        entries[0] = (member, content)
    with tarfile.open(tmp_path / 'changed.tar.gz', 'w:gz') as archive:
        for member, content in entries:
            archive.addfile(member, io.BytesIO(content))
    with pytest.raises(ValueError):
        unpack_source_archive(tmp_path / 'changed.tar.gz', tmp_path / 'restored', manifest)
    assert not (tmp_path / 'outside').exists()

def prepared_bundle(tmp_path):
    roots = source_roots(tmp_path)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    source = create_source_archive(roots, bundle / 'sources.tar.gz')
    (bundle / 'nested').mkdir()
    (bundle / 'nested/bundle.json').write_text('{"this":"is an ordinary required file"}')
    result = bundle_manifest(bundle, source=source, environment=environment_snapshot(), source_pins={'pine2ast': '1' * 40}, host_commit='2' * 40)
    return (bundle, result)

def test_prepared_bundle_checks_nested_manifest_named_files(tmp_path):
    bundle, result = prepared_bundle(tmp_path)
    assert 'nested/bundle.json' in result['files']
    assert verify_bundle(bundle, expected_source_hash=result['source']['content_hash']) == result

@pytest.mark.parametrize('mutation', ['modified', 'missing', 'extra', 'link', 'other-candidate'])
def test_prepared_bundle_rejects_transfer_drift(tmp_path, mutation):
    bundle, result = prepared_bundle(tmp_path)
    if mutation == 'modified':
        (bundle / 'nested/bundle.json').write_text('{}')
    elif mutation == 'missing':
        (bundle / 'nested/bundle.json').unlink()
    elif mutation == 'extra':
        (bundle / 'other.txt').write_text('unexpected')
    elif mutation == 'link':
        (bundle / 'unexpected-link').symlink_to(tmp_path / 'openpine', target_is_directory=True)
    expected = 'sha256:' + '0' * 64 if mutation == 'other-candidate' else result['source']['content_hash']
    with pytest.raises(ValueError):
        verify_bundle(bundle, expected_source_hash=expected)

@pytest.mark.parametrize('mode', ['success', 'failure', 'timeout', 'spawn-error'])
def test_logged_commands_retain_actual_exit_and_logs(tmp_path, mode):
    code = {'success': 'print("observed")', 'failure': 'print("failure");raise SystemExit(7)', 'timeout': 'import time;print("started",flush=True);time.sleep(30)', 'spawn-error': ''}[mode]
    executable = sys.executable if mode != 'spawn-error' else str(tmp_path / 'missing-python')
    report = run_logged([executable, '-c', code], cwd=tmp_path, output=tmp_path / 'command', env=dict(os.environ), timeout=1 if mode == 'timeout' else 20)
    assert report['ok'] is (mode == 'success')
    assert report['status'] == {'success': 'completed', 'failure': 'failed', 'timeout': 'timeout', 'spawn-error': 'infrastructure_error'}[mode]
    assert report['full_stage_accepted'] is False
    assert (tmp_path / 'command/command.json').is_file()
    for name, expected in report['files'].items():
        assert hash_file(tmp_path / 'command' / name) == expected
    if mode == 'failure':
        assert report['returncode'] == 7
        from openpine.verification.execution_ci import Commands
        with pytest.raises(RuntimeError, match='raw receipt') as error:
            Commands(tmp_path / 'ci').run([sys.executable, '-c', code], cwd=tmp_path)
        assert 'failure' in str(error.value)
        assert 'returncode=7' in str(error.value)
        assert read_json(tmp_path / 'ci/commands/0000/command.json')['returncode'] == 7
    with pytest.raises(FileExistsError):
        run_logged([sys.executable, '-c', 'pass'], cwd=tmp_path, output=tmp_path / 'command', env=dict(os.environ))

@pytest.mark.parametrize('tool', ['argv', 'node', 'chromium'])
@pytest.mark.parametrize('mutation', ['before', 'during', 'same-bytes-target', 'target-bytes'])
def test_logged_declared_tool_alias_drift_fails_closed(tmp_path, tool, mutation):
    target = tmp_path / 'frozen-tool'
    foreign = tmp_path / 'foreign-tool'
    alias = tmp_path / ('node' if tool == 'node' else 'tool-alias')
    foreign.write_text('#!' + sys.executable + '\nprint("FOREIGN EXECUTED", flush=True)\n')
    foreign.chmod(0o755)
    retarget = ('from pathlib import Path\n'
                f'p=Path({str(alias)!r}); p.unlink(); p.symlink_to({str(foreign)!r})\n')
    target.write_text('#!' + sys.executable + '\nprint("FROZEN EXECUTED", flush=True)\n'
                      + (retarget if mutation == 'during' and tool == 'argv' else ''))
    target.chmod(0o755)
    alias.symlink_to(target)
    spec = {'path': str(target), 'sha256': hash_file(target), 'declared_path': str(alias)}
    frozen_bytes = target.read_bytes()
    if mutation == 'same-bytes-target':
        foreign.write_bytes(frozen_bytes)
    if mutation in {'before', 'same-bytes-target'}:
        alias.unlink(); alias.symlink_to(foreign)
    elif mutation == 'target-bytes':
        target.write_bytes(foreign.read_bytes())
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ.get('PATH', ''),
           'PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH': str(alias)}
    if tool == 'argv':
        argv = [str(alias)]
    else:
        selected = 'shutil.which("node")' if tool == 'node' else 'os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"]'
        code = 'import os,shutil,subprocess\n' + (retarget if mutation == 'during' else '')
        argv = [sys.executable, '-c', code + f'subprocess.run([{selected}], check=True)']
    output = tmp_path / 'command'
    try:
        report = run_logged(argv, cwd=tmp_path, output=output, env=env, timeout=20,
                            inputs={'owner-tool:' + tool: spec})
    except ValueError as error:
        assert 'provenance' in str(error)
        assert mutation != 'during'
        return
    stdout = (output / 'stdout.log').read_text()
    assert not report['ok'], f'foreign command admitted: stdout={stdout!r}; report={report!r}'
    assert report['argv'] == argv
    assert report['cwd'] == str(tmp_path)
    assert read_json(output / 'command.json') == report
    if mutation != 'target-bytes':
        capture = report['input_provenance']['owner-tool:' + tool]['captured']['path']
        assert (output / capture).read_bytes() == frozen_bytes
    if mutation == 'during':
        assert 'FROZEN EXECUTED' in stdout if tool == 'argv' else 'FOREIGN EXECUTED' in stdout


@pytest.mark.parametrize('tool', ['node', 'chromium'])
def test_logged_auxiliary_selection_must_match_declared_alias(tmp_path, tool):
    target = tmp_path / 'frozen-tool'
    foreign = tmp_path / 'foreign-tool'
    target.write_text('#!' + sys.executable + '\nprint("FROZEN")\n')
    foreign.write_text('#!' + sys.executable + '\nprint("FOREIGN EXECUTED")\n')
    target.chmod(0o755); foreign.chmod(0o755)
    alias = tmp_path / 'declared-alias'
    alias.symlink_to(target)
    (tmp_path / 'node').symlink_to(foreign)
    env = {**os.environ, 'PATH': str(tmp_path), 'PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH': str(foreign)}
    selection = 'shutil.which("node")' if tool == 'node' else 'os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"]'
    argv = [sys.executable, '-c', 'import os,shutil,subprocess; subprocess.run([' + selection + '],check=True)']
    report = run_logged(argv, cwd=tmp_path, output=tmp_path / 'command', env=env, timeout=20,
                        inputs={'owner-tool:' + tool: {'path': str(target), 'sha256': hash_file(target), 'declared_path': str(alias)}})
    assert not report['ok'], f'foreign auxiliary admitted: {(tmp_path / "command/stdout.log").read_text()!r}'
    assert report['argv'] == argv
    assert read_json(tmp_path / 'command/command.json') == report


@pytest.mark.parametrize('mutation', ['missing', 'loop'])
def test_logged_invalid_declared_alias_retains_failed_receipt(tmp_path, mutation):
    target = tmp_path / 'frozen-tool'
    target.write_text('#!' + sys.executable + '\nprint("SHOULD NOT RUN")\n')
    target.chmod(0o755)
    alias = tmp_path / 'tool-alias'
    if mutation == 'loop':
        alias.symlink_to(alias)
    argv = [str(alias)]
    spec = {'path': str(target), 'sha256': hash_file(target), 'declared_path': str(alias)}
    report = run_logged(argv, cwd=tmp_path, output=tmp_path / 'command', env=dict(os.environ),
                        inputs={'owner-tool:argv': spec})
    assert not report['ok']
    assert report['returncode'] is None
    assert read_json(tmp_path / 'command/command.json') == report
    assert (tmp_path / 'command/input-0.bin').read_bytes() == target.read_bytes()
    assert (tmp_path / 'command/stdout.log').read_bytes() == b''


@pytest.mark.parametrize('tool', ['argv', 'node', 'chromium'])
def test_logged_matching_declared_tool_alias_preserves_capture(tmp_path, tool):
    target = tmp_path / 'frozen-tool'
    target.write_text('#!' + sys.executable + '\nprint("FROZEN EXECUTED")\n')
    target.chmod(0o755)
    alias = tmp_path / ('node' if tool == 'node' else 'tool-alias')
    alias.symlink_to(target)
    env = {**os.environ, 'PATH': str(tmp_path), 'PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH': str(alias)}
    if tool == 'argv':
        argv = [str(alias)]
    else:
        selection = 'shutil.which("node")' if tool == 'node' else 'os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"]'
        argv = [sys.executable, '-c', 'import os,shutil,subprocess; subprocess.run([' + selection + '],check=True)']
    spec = {'path': str(target), 'sha256': hash_file(target), 'declared_path': str(alias)}
    report = run_logged(argv, cwd=tmp_path, output=tmp_path / 'command', env=env,
                        inputs={'owner-tool:' + tool: spec})
    assert report['ok']
    assert report['argv'] == argv
    assert report['input_provenance']['owner-tool:' + tool]['source'] == spec
    assert (tmp_path / 'command/input-0.bin').read_bytes() == target.read_bytes()
    assert (tmp_path / 'command/stdout.log').read_text() == 'FROZEN EXECUTED\n'


def test_performance_mark_is_untraced_without_shrinking_inventory(tmp_path):
    bodies = {'test_a.py': 'import coverage\nfrom tiny import left\ndef test_value():\n    assert coverage.Coverage.current() is not None\n    assert left(1)==2\n', 'test_b.py': 'import pytest,coverage\nfrom tiny import left\n@pytest.mark.performance\ndef test_value():\n    assert coverage.Coverage.current() is None\n    assert left(2)==3\n'}
    previous, _ = tiny_plan(tmp_path, bodies=bodies)
    roots = {n: Path(p) for n, p in previous['roots'].items()}
    (roots['tiny'] / 'tiny').mkdir()
    (roots['tiny'] / 'tiny/__init__.py').write_text('def left(x):\n    return x+1\n')
    (roots['tiny'] / 'pyproject.toml').write_text('[tool.coverage.run]\nsource=["tiny"]\n[tool.coverage.report]\nfail_under=100\n[tool.pytest.ini_options]\nmarkers=["performance: untraced measurement"]\n')
    source = source_snapshot(roots)
    nodes = previous['tasks'][0]['nodeids']
    inventories = {'tiny@py': {'nodeids': nodes, 'node_markers': {nodes[0]: [], nodes[1]: ['performance']}, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': source['content_hash'], 'environment_hash': previous['environments']['py']['identity']['content_hash']}}
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']}}, 'untraced_markers': ['performance']}
    plan = make_plan(profile='component', policy=policy, roots=roots, source=source, inventories=inventories, environments=previous['environments'], requested=['tiny'], shard_count=2, coverage=True)
    assert {s['coverage'] for s in plan['tasks'][0]['shards']} == {False, True}
    assert sorted((n for s in plan['tasks'][0]['shards'] for n in s['nodeids'])) == nodes
    path = tmp_path / 'instrumented-plan.json'
    write_once_json(path, plan)
    run_campaign(plan, path, tmp_path / 'run', jobs=2, run_id='instrumentation')
    result = aggregate_campaign(plan, tmp_path / 'run', expected_plan_hash=plan['content_hash'], expected_run_id='instrumentation')
    assert result['ok'], result
    coverage = combine_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'coverage', run_id='instrumentation')
    assert coverage['ok'], coverage
    assert verify_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'coverage', run_id='instrumentation')['ok']
    (tmp_path / 'coverage/command-2/stdout.log').write_text('untrusted PASS')
    with pytest.raises(ValueError, match='checksum'):
        verify_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'coverage', run_id='instrumentation')

@pytest.mark.parametrize('relocated', [False, True])
def test_coverage_replay_unions_multiple_shards_without_mutating_primaries(tmp_path, relocated):
    bodies = {f'test_{name}.py': f'from tiny import left\ndef test_value():\n    assert left({value}) == {value + 1}\n' for name, value in [('a', 1), ('b', 2)]}
    previous, _ = tiny_plan(tmp_path, bodies=bodies)
    roots = {n: Path(p) for n, p in previous['roots'].items()}
    (roots['tiny'] / 'tiny').mkdir()
    (roots['tiny'] / 'tiny/__init__.py').write_text('def left(x):\n    a=x\n    b=a\n    c=b\n    d=c\n    e=d\n    return e+1\ndef uncovered():\n    return 0\n')
    (roots['tiny'] / 'pyproject.toml').write_text('[tool.coverage.run]\nsource=["tiny"]\n[tool.coverage.report]\nfail_under=89\nprecision=0\n')
    source = source_snapshot(roots)
    nodes = previous['tasks'][0]['nodeids']
    inventories = {'tiny@py': {'nodeids': nodes, 'node_markers': {n: [] for n in nodes}, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': source['content_hash'], 'environment_hash': previous['environments']['py']['identity']['content_hash']}}
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']}}}
    plan = make_plan(profile='component', policy=policy, roots=roots, source=source, inventories=inventories, environments=previous['environments'], requested=['tiny'], shard_count=2, coverage=True)
    assert len(plan['tasks'][0]['shards']) == 2
    path = tmp_path / 'instrumented-plan.json'
    write_once_json(path, plan)
    from openpine.verification.execution_binding import make_binding
    import shutil
    execution_roots = dict(roots)
    binding = None
    if relocated:
        execution_roots['tiny'] = tmp_path / 'executor-tiny'
        shutil.copytree(roots['tiny'], execution_roots['tiny'])
        binding = make_binding(plan, execution_roots, {'py': sys.executable})
    run_campaign(plan, path, tmp_path / 'run', jobs=2, run_id='multi-shard', binding=binding)
    result = combine_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'coverage', run_id='multi-shard', binding=binding)
    assert result['ok'], result
    if relocated:
        replay_root = tmp_path / 'third-tiny'
        shutil.copytree(execution_roots['tiny'], replay_root)
        shutil.rmtree(execution_roots['tiny'])
        shutil.rmtree(roots['tiny'])
        roots['tiny'] = replay_root
    primaries = {str(p.relative_to(tmp_path)): hash_file(p) for folder in ['run', 'coverage'] for p in (tmp_path / folder).rglob('*') if p.is_file()}
    assert verify_task_coverage(plan, tmp_path / 'run', 'tiny@py', tmp_path / 'coverage', run_id='multi-shard', source_roots=roots)['ok']
    assert {str(p.relative_to(tmp_path)): hash_file(p) for folder in ['run', 'coverage'] for p in (tmp_path / folder).rglob('*') if p.is_file()} == primaries


def test_ci_graph_retains_all_interpreters_and_decouples_frontend():
    workflow = yaml.safe_load((HOST / '.github/workflows/rc6-native.yml').read_text())
    jobs = workflow['jobs']
    assert jobs['prepare']['strategy']['matrix']['python'] == ['3.11', '3.12', '3.13']
    prepare_checkout = next(step for step in jobs['prepare']['steps'] if step.get('uses', '').startswith('actions/checkout@'))
    assert prepare_checkout['with']['fetch-depth'] == 0
    from openpine.verification import execution_ci
    assert 'export_git_provenance' in inspect.getsource(execution_ci.prepare)
    assert 'restore_git_provenance' in inspect.getsource(execution_ci.restore)
    assert jobs['verify']['strategy']['matrix']['python'] == ['3.11', '3.13']
    assert jobs['frontend']['needs'] == ['prepare', 'plan']
    frontend_runs='\n'.join(step.get('run','') for step in jobs['frontend']['steps'])
    assert 'frontend_exact.py' in frontend_runs
    assert any(step.get('env',{}).get('OPENPINE_BINDING')=='${{ runner.temp }}/frontend-binding.json' for step in jobs['frontend']['steps'])
    planner_runs='\n'.join(step.get('run','') for step in jobs['plan']['steps'])
    assert '--owner-locations' in planner_runs and 'make_owner_locations' in planner_runs
    assert jobs['packages']['needs'] == ['prepare','plan']
    assert 'harness.py' in '\n'.join(step.get('run','') for step in jobs['packages']['steps'])
    assert jobs['component']['needs'] == ['plan']
    task_upload = next(
        step for step in jobs['component']['steps']
        if step.get('with', {}).get('name', '').startswith('rc6-task-')
    )
    task_paths = task_upload['with']['path'].splitlines()
    assert '${{ runner.temp }}/fragment/' in task_paths
    assert '!${{ runner.temp }}/fragment/**/private/**' in task_paths
    assert task_upload['with']['include-hidden-files'] is True
    assert task_upload['if'] == 'always()'
    assert jobs['aggregate']['if'] == 'always()'
    assert set(jobs['aggregate']['needs']) == {'prepare', 'plan', 'component', 'quality', 'verify', 'frontend', 'packages'}
    assert set(jobs) == set(jobs['aggregate']['needs']) | {'aggregate', 'language-diagnostic', 'strict-language', 'stabilization'}
    assert jobs['stabilization']['if'] == 'always()'
    assert set(jobs['stabilization']['needs']) == {'prepare', 'plan', 'verify', 'frontend', 'packages'}
    common_runs = '\n'.join(step.get('run', '') for step in jobs['stabilization']['steps'])
    assert 'test-ci stabilization' in common_runs
    assert 'strict-language' not in jobs['stabilization']['needs']
    assert 'language-diagnostic' not in jobs['stabilization']['needs']
    assert any(step.get('if') == 'always()' and 'upload-artifact@' in step.get('uses', '')
               for step in jobs['stabilization']['steps'])
    assert workflow['permissions'] == {'contents': 'read'}
    assert 'pull_request' in workflow['concurrency']['cancel-in-progress']
    for job in jobs.values():
        for step in job.get('steps', []):
            assert step.get('continue-on-error', False) is False
            action = step.get('uses')
            if action:
                assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", action), action
            if action and action.startswith('actions/checkout@'):
                assert step['with']['persist-credentials'] is False
            if 'pip install' in step.get('run', ''):
                assert '--require-hashes' in step['run']
                assert 'verification/ci-bootstrap-requirements.txt' in step['run']
    assert (HOST / 'verification/ci-bootstrap-requirements.txt').is_file()
    assert (HOST / 'verification/ci-runtime-requirements.txt').is_file()
    runtime_lock = (HOST / 'verification/ci-runtime-requirements.txt').read_text()
    assert all(f'{package}==' in runtime_lock for package in ('pytest_asyncio', 'ruff', 'mypy', 'mypy_extensions', 'pathspec', 'librt'))
    assert '--hash=sha256:' in runtime_lock
    platform_workflow = yaml.safe_load((HOST / '.github/workflows/rc6-test-platform.yml').read_text())
    platform_paths = set(platform_workflow.get('on', platform_workflow.get(True))['pull_request']['paths'])
    assert {
        'verification/ci-bootstrap-requirements.txt',
        'verification/ci-runtime-requirements.txt',
        'pyproject.toml',
        'docs/RC6_LIFECYCLE_SOURCES.json',
    } <= platform_paths
    platform_steps = platform_workflow['jobs']['platform']['steps']
    assert all(('--require-hashes' in step.get('run', '') or "'--no-deps'" in step.get('run', '')) for step in platform_steps if 'pip install' in step.get('run', ''))
    owner_install = next(step['run'] for step in platform_steps if step.get('name') == 'Install exact owner contracts used by prerequisite tests')
    assert "'--no-build-isolation'" in owner_install
    assert "'--no-deps'" in owner_install
    from openpine.verification import execution_ci
    prepare_source = inspect.getsource(execution_ci.prepare)
    assert "'pip', 'download', '--no-deps', '--only-binary=:all:'" not in prepare_source
    assert "'pip', 'download', '--require-hashes', '--no-deps', '--only-binary=:all:'" in prepare_source
    assert "str(tools_lock)" in prepare_source and "str(runtime_lock)" in prepare_source
    assert "'--require-hashes'" in prepare_source
    assert "'--no-index'" in prepare_source
    assert "'--no-deps'" in prepare_source
    verify_steps = jobs['verify']['steps']
    foundation_upload = next(step for step in verify_steps
                             if step.get('with', {}).get('name', '').startswith('rc6-checks-'))
    assert foundation_upload['with']['include-hidden-files'] is True
    assert '!${{ runner.temp }}/foundation/**/private/**' in foundation_upload['with']['path'].splitlines()
    command_upload = next(step for step in verify_steps
                          if step.get('with', {}).get('name', '').startswith('rc6-command-foundation-'))
    assert command_upload['if'] == 'always()'
    assert command_upload['with']['path'] == '${{ runner.temp }}/restored/execute-log/commands/'
    assert command_upload['with']['if-no-files-found'] == 'error'
    for job in ('quality', 'verify', 'language-diagnostic', 'strict-language', 'stabilization'):
        runs = '\n'.join(step.get('run', '') for step in jobs[job]['steps'])
        assert "f.write('PYTHONPATH='+r['roots']['openpine']+'\\n')" in runs
        assert 'os.pathsep.join(r[\'roots\'].values())' not in runs
    assert not any('bash scripts/rc6_builtin_remainder.sh' in step.get('run', '') for step in verify_steps)
    assert any('bash scripts/rc6_builtin_remainder.sh --diagnostic-provisional' in step.get('run', '') for step in jobs['language-diagnostic']['steps'])
    assert any('bash scripts/rc6_builtin_remainder.sh' in step.get('run', '') for step in jobs['strict-language']['steps'])
    remainder = (HOST / 'scripts/rc6_builtin_remainder.sh').read_text()
    assert 'builtin-index' in remainder and 'stage2-remaining' in remainder
    assert 'test "$INDEX_STATUS" -eq 0' in remainder
    assert 'test "$REMAINDER_STATUS" -eq 0' in remainder
    policy = read_json(HOST / 'verification/execution-policy.json')
    assert sum((len(component['pythons']) for component in policy['components'].values())) == 23
    assert policy['untraced_markers'] == ['performance']
    assert len(policy['components']) == 8
    assert 'test-performance' in policy['required_gates']['stage-full']


@pytest.mark.parametrize('diagnostic,owner_status', [(False, 1), (True, 0), (True, 2)])
def test_remainder_script_keeps_modes_and_raw_exits(tmp_path, diagnostic, owner_status):
    """Subprocess shim tests orchestration, not language/product acceptance."""
    tools = tmp_path / 'bin'
    tools.mkdir()
    calls = tmp_path / 'calls.jsonl'
    shim = tools / 'python'
    shim.write_text('#!' + sys.executable + '\n' +
                    'import json,os,sys\n'
                    'with open(os.environ["CALLS"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                    'if sys.argv[1:2]==["-c"]: print("hash"); raise SystemExit(0)\n'
                    'if sys.argv[1:2]==["scripts/derive_numeric_closure.py"]: raise SystemExit(0)\n'
                    'if sys.argv[1:2]==["-"]: os.execv(sys.executable, [sys.executable,*sys.argv[1:]])\n'
                    'raise SystemExit(int(os.environ["OWNER_STATUS"]))\n')
    shim.chmod(0o755)
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'],
           'CALLS': str(calls), 'OWNER_STATUS': str(owner_status),
           'GITHUB_WORKSPACE': str(HOST), 'RUNNER_TEMP': str(tmp_path)}
    (tmp_path / 'evidence').mkdir()
    bash = shutil.which('bash')
    assert bash is not None
    result = subprocess.run([bash, str(HOST / 'scripts/rc6_builtin_remainder.sh'),  # noqa: S603 -- fixed source-controlled shell test and explicit mode
                             *(['--diagnostic-provisional'] if diagnostic else [])],
                            cwd=HOST, env=env, capture_output=True, text=True, timeout=30)
    invoked = [json.loads(line) for line in calls.read_text().splitlines()]
    owners = [row for row in invoked if 'builtin-index' in row or 'stage2-remaining' in row]
    assert len(owners) == 2
    assert all(('--diagnostic-provisional' in row) is diagnostic for row in owners)
    assert (result.returncode == 0) is (owner_status == 0)
    exits = read_json(tmp_path / 'evidence/language-command-exits.json')
    assert exits['builtin_index_exit'] == exits['remainder_exit'] == owner_status
    assert exits['mode'] == ('diagnostic-provisional' if diagnostic else 'strict')
    assert exits['language_accepted'] is False


def test_language_jobs_are_named_separate_and_do_not_block_native_foundation():
    jobs = yaml.safe_load((HOST / '.github/workflows/rc6-native.yml').read_text())['jobs']
    assert 'language-diagnostic' in jobs and 'strict-language' in jobs
    assert 'strict-language' not in jobs['verify']['needs']
    assert 'strict-language' not in jobs['aggregate']['needs']
    assert 'verify' in jobs['language-diagnostic']['needs']
    assert 'verify' in jobs['strict-language']['needs']
    diagnostic = '\n'.join(step.get('run', '') for step in jobs['language-diagnostic']['steps'])
    strict = '\n'.join(step.get('run', '') for step in jobs['strict-language']['steps'])
    assert 'bash scripts/rc6_builtin_remainder.sh --diagnostic-provisional' in diagnostic
    assert 'bash scripts/rc6_builtin_remainder.sh' in strict
    assert '--diagnostic-provisional' not in strict
    for job in ('language-diagnostic', 'strict-language'):
        assert any(step.get('if') == 'always()' and 'upload-artifact@' in step.get('uses', '') for step in jobs[job]['steps'])


def test_lifecycle_pins_use_current_release_heads_without_rewriting_source_bound_evidence():
    pins = read_json(HOST / 'docs/RC6_LIFECYCLE_SOURCES.json')
    assert pins == {
        'ast2python': '17ad5f4b6f9c59cb549f4eb08a2c926561985f16',
        'backtest_engine': '8eba0eb7350d5eab1529dbaff96f7bf9ada82572',
        'marketdata-provider': 'b453a2b05bc825884270c81500aa879fc543a208',
        'openpine-contracts': '7ad7de5ba9b3a7f0cfec0f0bc8d7a7f91e98df34',
        'optimizer': '762f97e306467f505fc4ade22acaf40577214e24',
        'pine2ast': '6dfd47badff5ef5b038dfd48a9fb3d8e762b1836',
        'pinelib': 'fcfdab59767103cbc6903c0fa251d895d58b1568',
    }
    review = read_json(HOST / 'verification/source-pin-reconciliation-review.json')
    # This review is the retained historical snapshot, not a receipt for the
    # newly published marketdata-provider API commit.
    assert review['release_heads'] == {
        **pins,
        'ast2python': 'ce2e6eed49be541543654ebc0439d545c3084f30',
        'pine2ast': 'ddb164a8819d889150378a303b0d3255cf0b5102',
        'marketdata-provider': '4610c93b904d7e70fe6cbfe2914a07f29935933e',
    }
    assert review['requires_fresh_sibling_collection_and_evidence'] is True
    assert review['derived_inventory_review']['stage2_gate']['action'].startswith('retained strict')
    assert review['derived_inventory_review']['historical_source_bound_evidence']['action'].startswith('not rewritten')


def test_ci_restore_rejects_unknown_action_and_cli_is_exposed(tmp_path):
    result = run_logged([sys.executable, '-m', 'openpine.verification', 'test-ci', '--help'], cwd=HOST, output=tmp_path / 'help', env=dict(os.environ), timeout=30)
    assert result['ok']
    text = (tmp_path / 'help/stdout.log').read_text()
    for command in ('prepare', 'restore', 'plan', 'task', 'foundation'):
        assert command in text
    rejected = run_logged([sys.executable, '-m', 'openpine.verification', 'test-ci', 'unsupported'], cwd=HOST, output=tmp_path / 'bad-action', env=dict(os.environ), timeout=30)
    assert rejected['returncode'] == 2 and (not rejected['ok'])

def test_resource_budget_uses_inherited_quota_not_only_affinity(tmp_path):
    from openpine.verification.execution_resources import resource_profile
    root = tmp_path / 'cgroup'
    (root / 'child').mkdir(parents=True)
    (root / 'cpu.max').write_text('150000 100000')
    (root / 'child/cpu.max').write_text('max 100000')
    (root / 'memory.max').write_text(str(512 * 1024 * 1024))
    membership = tmp_path / 'membership'
    membership.write_text('0::/child\n')
    result = resource_profile(cgroup_root=root, membership_file=membership, affinity=[0, 1, 2, 3], physical_bytes=1024 ** 3)
    assert result['cpu_slots'] == 1 and result['cpu_quota'] == 1.5
    assert result['affinity_cpus'] == 4 and result['memory_limit_bytes'] == 512 * 1024 * 1024
    assert result['cgroup_limits_observed']

@pytest.mark.parametrize('contents', ['0 100000', '-1 100000', 'max 0', 'invalid'])
def test_resource_budget_rejects_malformed_quota(tmp_path, contents):
    from openpine.verification.execution_resources import resource_profile
    root = tmp_path / 'cgroup'
    root.mkdir()
    (root / 'cpu.max').write_text(contents)
    membership = tmp_path / 'membership'
    membership.write_text('0::/\n')
    with pytest.raises(ValueError):
        resource_profile(cgroup_root=root, membership_file=membership, affinity=[0, 1], physical_bytes=1024 ** 3)

def test_resource_budget_reads_v1_cpu_and_memory_controllers(tmp_path):
    from openpine.verification.execution_resources import resource_profile
    root = tmp_path / 'cgroup'
    (root / 'cpu').mkdir(parents=True)
    (root / 'memory').mkdir()
    (root / 'cpu/cpu.cfs_quota_us').write_text('100000')
    (root / 'cpu/cpu.cfs_period_us').write_text('200000')
    (root / 'memory/memory.limit_in_bytes').write_text(str(1024 ** 3))
    membership = tmp_path / 'membership'
    membership.write_text('2:cpu,cpuacct:/\n3:memory:/\n')
    result = resource_profile(cgroup_root=root, membership_file=membership, affinity=[0, 1, 2], physical_bytes=2 * 1024 ** 3)
    assert result['cpu_slots'] == 1 and result['cpu_quota'] == 0.5
    assert result['memory_limit_bytes'] == 1024 ** 3

def test_campaign_rejects_slots_above_observed_cgroup_quota(tmp_path, monkeypatch):
    from openpine.verification import execution_resources
    plan, path = tiny_plan(tmp_path)
    monkeypatch.setattr(execution_resources, 'resource_profile', lambda: {'cpu_slots': 1, 'affinity_cpus': 5, 'memory_limit_bytes': 1024 ** 3})
    with pytest.raises(ValueError, match='CPU budget'):
        run_campaign(plan, path, tmp_path / 'run', jobs=2)
    assert not (tmp_path / 'run').exists()

def test_campaign_rejects_memory_budget_above_host_limit(tmp_path, monkeypatch):
    from openpine.verification import execution_resources
    plan, path = tiny_plan(tmp_path)
    monkeypatch.setattr(execution_resources, 'resource_profile', lambda: {'cpu_slots': 2, 'affinity_cpus': 2, 'memory_limit_bytes': 512 * 1024 ** 2})
    with pytest.raises(ValueError, match='host/cgroup'):
        run_campaign(plan, path, tmp_path / 'run', jobs=1, memory_mib=1024)
    assert not (tmp_path / 'run').exists()


def test_native_verify_preserves_required_branch_protection_contexts():
    jobs = yaml.safe_load((HOST / '.github/workflows/rc6-native.yml').read_text())['jobs']
    verify = jobs['verify']
    contexts = {
        verify['name'].replace('${{ matrix.python }}', version)
        for version in verify['strategy']['matrix']['python']
    }
    assert contexts == {'verify (3.11)', 'verify (3.13)'}
    assert jobs['frontend'].get('name', 'frontend') == 'frontend'


def test_native_inline_scripts_import_existing_verifier_exports():
    import ast
    import importlib

    workflow = yaml.safe_load((HOST / '.github/workflows/rc6-native.yml').read_text())
    checked = 0
    for job in workflow['jobs'].values():
        for step in job.get('steps', []):
            script = step.get('run', '')
            for _, body in re.findall(r"python - <<'([A-Z_]+)'\n(.*?)\n\1", script, re.S):
                for node in ast.walk(ast.parse(body)):
                    if not isinstance(node, ast.ImportFrom) or not node.module or not node.module.startswith('openpine.verification.'):
                        continue
                    module = importlib.import_module(node.module)
                    for symbol in node.names:
                        assert hasattr(module, symbol.name), f'{node.module} does not export {symbol.name}'
                        checked += 1
    assert checked >= 3


def test_platform_installs_all_pinned_transitive_owner_prerequisites():
    import ast

    steps = yaml.safe_load((HOST / '.github/workflows/rc6-test-platform.yml').read_text())['jobs']['platform']['steps']
    owner = next(step for step in steps
                 if step.get('name') == 'Install exact owner contracts used by prerequisite tests')
    script = owner['run'].split("python - <<'PYOWNER'\n", 1)[1].rsplit('\nPYOWNER', 1)[0]
    tree = ast.parse(script)
    loop = next(node for node in ast.walk(tree)
                if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'name')
    names = ast.literal_eval(loop.iter)
    pins = json.loads((HOST / 'docs/RC6_LIFECYCLE_SOURCES.json').read_text())
    assert len(names) == len(set(names))
    assert set(names) == set(pins), 'source-installed prerequisites do not close portable owner imports'
    assert 'ast2python' in names and 'pine2ast' in names and 'pinelib' in names


def test_platform_installs_hashed_runtime_closure_before_exact_owners():
    steps = yaml.safe_load((HOST / '.github/workflows/rc6-test-platform.yml').read_text())['jobs']['platform']['steps']
    owner_index = next(i for i, step in enumerate(steps)
                       if step.get('name') == 'Install exact owner contracts used by prerequisite tests')
    runtime_runs = [step.get('run', '') for step in steps[:owner_index]
                    if 'verification/ci-runtime-requirements.txt' in step.get('run', '')]
    assert runtime_runs, 'owner --no-deps requires the hashed runtime closure first'
    for run in runtime_runs:
        assert 'python -m pip install --require-hashes --only-binary=:all:' in run
        assert '--no-deps' not in run
    # Contracts requires jsonschema; these are its non-optional transitive owners.
    runtime_lock = (HOST / 'verification/ci-runtime-requirements.txt').read_text()
    for package in ('jsonschema', 'attrs', 'jsonschema-specifications', 'referencing', 'rpds-py', 'typing_extensions'):
        assert re.search(r'(?m)^' + re.escape(package) + r'==[^\n]+ \\\n\s+--hash=sha256:[0-9a-f]{64}', runtime_lock)
    owner_run = steps[owner_index]['run']
    assert "for name in ('openpine-contracts','pine2ast','pinelib','marketdata-provider','backtest_engine','ast2python','optimizer'):" in owner_run
    assert "'checkout','--detach',pins[name]" in owner_run
    assert "'rev-parse','HEAD'" in owner_run
    assert "'--no-deps',*paths],check=True)" in owner_run


def test_platform_checks_installed_dependencies_before_locked_tests():
    steps = yaml.safe_load((HOST / '.github/workflows/rc6-test-platform.yml').read_text())['jobs']['platform']['steps']
    owner_index = next(i for i, step in enumerate(steps)
                       if step.get('name') == 'Install exact owner contracts used by prerequisite tests')
    test_index = next(i for i, step in enumerate(steps) if 'python -m pytest' in step.get('run', ''))
    checks = [step for step in steps[owner_index + 1:test_index]
              if 'python -m pip check' in step.get('run', '')]
    assert len(checks) == 1, 'real pip check must fail closed after owner install and before tests'
    check = checks[0]
    assert check.get('continue-on-error', False) is False
    assert check.get('if', 'success()') == 'success()'
    assert check['run'].splitlines()[-1] == 'python -m pip check > "$RUNNER_TEMP/platform-evidence/pip-check.txt" 2>&1'
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert upload['if'] == 'always()'
    assert upload['with']['path'] == '${{ runner.temp }}/platform-evidence/'
