"""CI transfer, process and marker contracts; no synthetic hosted-run PASS."""
from __future__ import annotations
import copy
import inspect
import io
import os
import re
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
    with pytest.raises(FileExistsError):
        run_logged([sys.executable, '-c', 'pass'], cwd=tmp_path, output=tmp_path / 'command', env=dict(os.environ))

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

def test_ci_graph_retains_all_interpreters_and_decouples_frontend():
    workflow = yaml.safe_load((HOST / '.github/workflows/rc6-native.yml').read_text())
    jobs = workflow['jobs']
    assert jobs['prepare']['strategy']['matrix']['python'] == ['3.11', '3.12', '3.13']
    assert jobs['verify']['strategy']['matrix']['python'] == ['3.11', '3.13']
    assert jobs['frontend']['needs'] == ['prepare']
    assert jobs['component']['needs'] == ['plan']
    assert jobs['aggregate']['if'] == 'always()'
    assert set(jobs['aggregate']['needs']) == set(jobs) - {'aggregate'}
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
    assert any('bash scripts/rc6_builtin_remainder.sh' in step.get('run', '') for step in verify_steps)
    remainder = (HOST / 'scripts/rc6_builtin_remainder.sh').read_text()
    assert 'builtin-index' in remainder and 'stage2-remaining' in remainder
    assert 'test "$INDEX_STATUS" -eq 0' in remainder
    assert 'test "$REMAINDER_STATUS" -eq 0' in remainder
    policy = read_json(HOST / 'verification/execution-policy.json')
    assert sum((len(component['pythons']) for component in policy['components'].values())) == 23
    assert policy['untraced_markers'] == ['performance']
    assert len(policy['components']) == 8
    assert 'test-performance' in policy['required_gates']['stage-full']


def test_lifecycle_pins_use_current_release_heads_without_rewriting_source_bound_evidence():
    pins = read_json(HOST / 'docs/RC6_LIFECYCLE_SOURCES.json')
    assert pins == {
        'ast2python': 'ce2e6eed49be541543654ebc0439d545c3084f30',
        'backtest_engine': '8eba0eb7350d5eab1529dbaff96f7bf9ada82572',
        'marketdata-provider': '4610c93b904d7e70fe6cbfe2914a07f29935933e',
        'openpine-contracts': '7ad7de5ba9b3a7f0cfec0f0bc8d7a7f91e98df34',
        'optimizer': '762f97e306467f505fc4ade22acaf40577214e24',
        'pine2ast': 'ddb164a8819d889150378a303b0d3255cf0b5102',
        'pinelib': 'fcfdab59767103cbc6903c0fa251d895d58b1568',
    }
    review = read_json(HOST / 'verification/source-pin-reconciliation-review.json')
    assert review['release_heads'] == pins
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
