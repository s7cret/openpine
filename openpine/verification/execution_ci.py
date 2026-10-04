"""CI graph adapters for the existing planner, pytest and foundation owners.

Prepare each interpreter's exact wheelhouse once; workers install only those
verified wheels. This is orchestration, not another acceptance engine. No command
can change Git refs, install a fake dependency or declare Stage 2 accepted.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
import tarfile
import venv
from pathlib import Path, PurePosixPath
from openpine.verification.execution_identity import clean_environment, environment_snapshot, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal, verify
COMPONENTS = ('openpine', 'openpine-contracts', 'pine2ast', 'ast2python', 'pinelib', 'backtest_engine', 'marketdata-provider', 'optimizer')
BUNDLE_SCHEMA = 'openpine.ci_prepared_environment.v1'

def attest_ci_source_commits(reports: list[dict]) -> dict[str, str]:
    """Freeze the Git revisions that produced all eight archived source trees."""
    if not reports:
        raise ValueError('prepared producer commits are missing')
    first = reports[0]
    pins = first.get('source_pins')
    commits = {'openpine': first.get('host_commit'), **pins} if isinstance(pins, dict) else {}
    if set(commits) != set(COMPONENTS) or any(
        not isinstance(value, str) or re.fullmatch('[0-9a-f]{40}', value) is None
        for value in commits.values()
    ) or any(
        report.get('host_commit') != commits['openpine'] or report.get('source_pins') != pins
        for report in reports
    ):
        raise ValueError('prepared producer commits are missing or inconsistent')
    return commits

def retain_builtin_owner_evidence(merged: Path, suites: Path, evidence: Path, version: str, declared_pins: Path) -> None:
    """Carry the foundation owner's exact source receipt into the builtin join."""
    if version not in {'py311', 'py313'}:
        raise ValueError('unsupported builtin owner interpreter')
    pins = read_json(suites / version / 'source-pins.json')
    if pins != read_json(declared_pins):
        raise ValueError('foundation source pins differ from restored release pins')
    evidence.mkdir(parents=True, exist_ok=True)
    target = evidence / 'source-pins.json'
    if target.exists():
        if read_json(target) != pins:
            raise ValueError('conflicting builtin owner source pins')
    else:
        write_once_json(target, pins)
    run = read_json(merged / 'run.json')
    for attempt in run['attempts']:
        if not attempt['task'].endswith('@' + version):
            continue
        for key, desc in attempt['artifacts'].items():
            if not key.startswith('owner:'):
                continue
            relative = PurePosixPath(key[6:])
            if relative.is_absolute() or not relative.parts or any(part in {'', '.', '..'} for part in key[6:].split('/')):
                raise ValueError('invalid builtin owner artifact path')
            destination = evidence.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = (merged / desc['path']).read_bytes()
            if hash_file(merged / desc['path']) != desc['sha256']:
                raise ValueError('builtin owner artifact checksum mismatch')
            if destination.exists() and destination.read_bytes() != data:
                raise ValueError('conflicting builtin owner evidence')
            if not destination.exists():
                destination.write_bytes(data)

def create_source_archive(roots: dict[str, Path], output: Path) -> dict:
    from openpine.verification.execution_identity import ensure_external_output
    ensure_external_output(output, roots)
    source = source_snapshot(roots)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, component in source['components'].items():
            for relative, record in component['files'].items():
                path = roots[name] / relative
                if hash_file(path) != record['sha256']:
                    raise ValueError('source changed while creating archive')
                archive.add(path, arcname=name + '/' + relative, recursive=False)
    if source_snapshot(roots) != source:
        raise ValueError('source changed while creating archive')
    return source

def unpack_source_archive(path: Path, destination: Path, expected: dict) -> dict[str, Path]:
    verify(expected, 'openpine.execution_sources.v1')
    destination.mkdir(parents=True, exist_ok=False)
    seen, total = (set(), 0)
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            pure = PurePosixPath(member.name)
            parts = member.name.split('/')
            if not member.isfile() or pure.is_absolute() or '\\' in member.name or (':' in member.name) or any((p in {'', '.', '..'} for p in parts)) or (len(parts) < 2) or (parts[0] not in expected['components']) or (member.name in seen):
                raise ValueError('unsafe or unexpected source archive member')
            seen.add(member.name)
            total += member.size
            if total > 2 * 1024 ** 3 or len(seen) > 100000:
                raise ValueError('oversized source archive')
            record = expected['components'][parts[0]]['files'].get('/'.join(parts[1:]))
            if record is None or member.size != record['size']:
                raise ValueError('source archive differs from exact input manifest')
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = archive.extractfile(member)
            if content is None:
                raise ValueError('missing archive member data')
            with content, target.open('xb') as result:
                shutil.copyfileobj(content, result)
            target.chmod(493 if record['executable'] else 420)
            if hash_file(target) != record['sha256']:
                raise ValueError('source member checksum mismatch')
    roots = {name: destination / name for name in expected['components']}
    if source_snapshot(roots) != expected:
        raise ValueError('source archive omitted required files')
    return roots

def _git_provenance(repo: Path, *args: str) -> str:
    git = shutil.which('git')
    if git is None:
        raise ValueError('git is required for exact source provenance')
    result = subprocess.run([git, '-C', str(repo), *args], capture_output=True, text=True, check=False, timeout=300)  # noqa: S603 -- fixed Git executable and declared argv
    if result.returncode != 0:
        raise ValueError('exact Git provenance command failed: ' + ' '.join(args[:2]))
    return result.stdout.strip()

def export_git_provenance(repo: Path, output: Path, expected_head: str) -> None:
    if re.fullmatch('[0-9a-f]{40}', expected_head) is None or _git_provenance(repo, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('source checkout differs from exact producer commit')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ValueError('Git provenance bundle must be new')
    _git_provenance(repo, 'bundle', 'create', str(output), 'HEAD', '--all')
    if _git_provenance(repo, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('source checkout changed during provenance export')

def restore_git_provenance(repo: Path, bundle: Path, component: str, expected_head: str) -> None:
    if component not in COMPONENTS or re.fullmatch('[0-9a-f]{40}', expected_head) is None or (repo / '.git').exists():
        raise ValueError('invalid or already restored Git provenance target')
    _git_provenance(repo, 'init', '-q')
    _git_provenance(repo, 'bundle', 'verify', str(bundle))
    _git_provenance(repo, 'bundle', 'unbundle', str(bundle))
    _git_provenance(repo, 'cat-file', '-e', expected_head + '^{commit}')
    _git_provenance(repo, 'update-ref', '--no-deref', 'HEAD', expected_head)
    _git_provenance(repo, 'reset', '--mixed', '-q', 'HEAD')
    _git_provenance(repo, 'remote', 'add', 'origin', 'https://github.com/s7cret/' + component + '.git')
    if _git_provenance(repo, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('restored Git head differs from exact producer commit')
    try:
        _git_provenance(repo, 'diff', '--exit-code', 'HEAD')
    except ValueError as error:
        raise ValueError('Git checkout differs from archived source') from error
    tracked = set(_git_provenance(repo, 'ls-files', '--cached', '-z').split('\0')) - {''}
    archived = set(source_snapshot({component: repo})['components'][component]['files'])
    if archived - tracked:
        raise ValueError('archived source contains files not present in Git commit')

def bundle_manifest(bundle: Path, *, source: dict, environment: dict, source_pins: dict, host_commit: str) -> dict:
    files = {}
    for path in sorted(bundle.rglob('*')):
        if path.is_symlink():
            raise ValueError('bundle cannot contain symlinks')
        if path.is_file() and path != bundle / 'bundle.json':
            files[path.relative_to(bundle).as_posix()] = hash_file(path)
    report = seal({'schema_id': BUNDLE_SCHEMA, 'source': source, 'environment': environment, 'source_pins': source_pins, 'host_commit': host_commit, 'files': files, 'scope': 'prepared exact dependencies and source inputs, not tested stage acceptance'})
    write_once_json(bundle / 'bundle.json', report)
    return report

def verify_bundle(bundle: Path, *, expected_source_hash: str | None=None) -> dict:
    from openpine.verification.execution_identity import evidence_path
    report = read_json(bundle / 'bundle.json')
    verify(report, BUNDLE_SCHEMA)
    verify(report['source'], 'openpine.execution_sources.v1')
    verify(report['environment'], 'openpine.execution_environment.v1')
    if expected_source_hash and report['source']['content_hash'] != expected_source_hash:
        raise ValueError('prepared bundle belongs to a different candidate')
    if any((p.is_symlink() for p in bundle.rglob('*'))):
        raise ValueError('bundle contains a symlink')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file() and p != bundle / 'bundle.json'}
    if actual != set(report['files']):
        raise ValueError('bundle inventory changed')
    for relative, expected_hash in report['files'].items():
        if hash_file(evidence_path(bundle, relative)) != expected_hash:
            raise ValueError('bundle artifact checksum mismatch: ' + relative)
    return report

class Commands:

    def __init__(self, work: Path):
        self.work = work
        self.index = 0

    def run(self, argv: list[str], *, cwd: Path, roots: dict[str, Path] | None=None, timeout: int=1200):
        log = self.work / 'commands' / f'{self.index:04d}'
        self.index += 1
        environment = clean_environment({n: str(p) for n, p in (roots or {}).items()}, self.work / 'private')
        result = run_logged(argv, cwd=cwd, output=log, env=environment, timeout=timeout)
        if not result['ok']:
            # Keep the immutable full logs and expose bounded diagnostics in the
            # hosted job log even when failure precedes owner output creation.
            tails = []
            for name in ('stdout.log', 'stderr.log'):
                with (log / name).open('rb') as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 8192))
                    tails.append(name + ':\n' + stream.read().decode('utf-8', errors='replace'))
            raise RuntimeError(
                'command failed; raw receipt: ' + str(log / 'command.json')
                + f"; status={result['status']}; returncode={result['returncode']}\n"
                + '\n'.join(tails)
            )
        return (log / 'stdout.log').read_text(encoding='utf-8')

def prepare(host: Path, work: Path, python_label: str) -> dict:
    import re
    if '.'.join(map(str, sys.version_info[:2])) != python_label:
        raise ValueError('actual interpreter differs from requested matrix lane')
    work.mkdir(parents=True, exist_ok=False)
    command = Commands(work)
    bundle = work / 'bundle'
    bundle.mkdir()
    stack = work / 'stack'
    stack.mkdir()
    pins = read_json(host / 'docs/RC6_LIFECYCLE_SOURCES.json')
    if set(pins) != set(COMPONENTS) - {'openpine'} or any((not re.fullmatch('[0-9a-f]{40}', v) for v in pins.values())):
        raise ValueError('exact seven-library pins required')
    git = shutil.which('git')
    if git is None:
        raise ValueError('git is required to prepare pinned CI sources')
    head = command.run([git, 'rev-parse', 'HEAD'], cwd=host).strip()
    command.run([git, 'diff', '--exit-code', 'HEAD'], cwd=host)
    host_tar = work / 'host.tar'
    command.run([git, 'archive', '--format=tar', '--output=' + str(host_tar), head], cwd=host)
    (stack / 'openpine').mkdir()
    with tarfile.open(host_tar) as archive:
        for member in archive:
            if member.isdir():
                continue
            pure = PurePosixPath(member.name)
            if not member.isfile() or pure.is_absolute() or '..' in pure.parts:
                raise ValueError('unexpected committed host archive member')
            target = stack / 'openpine' / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError('missing committed host bytes')
            with stream, target.open('xb') as dst:
                shutil.copyfileobj(stream, dst)
            target.chmod(493 if member.mode & 73 else 420)
    for name, sha in pins.items():
        command.run([git, 'clone', '--quiet', 'https://github.com/s7cret/' + name + '.git', str(stack / name)], cwd=work)
        command.run([git, 'checkout', '--detach', sha], cwd=stack / name)
        if command.run([git, 'rev-parse', 'HEAD'], cwd=stack / name).strip() != sha:
            raise ValueError('sibling source commit mismatch')
    roots = {name: stack / name for name in COMPONENTS}
    source = create_source_archive(roots, bundle / 'sources.tar.gz')
    for name in COMPONENTS:
        checkout = host if name == 'openpine' else roots[name]
        export_git_provenance(checkout, bundle / 'git' / (name + '.bundle'), head if name == 'openpine' else pins[name])
    env_root = work / 'venv'
    venv.EnvBuilder(with_pip=True, symlinks=True).create(env_root)
    executable = str(env_root / 'bin/python')
    tools_lock = host / 'verification/ci-bootstrap-requirements.txt'
    if not tools_lock.is_file():
        raise ValueError('hashed CI bootstrap lock is required')
    command.run(
        [
            executable,
            '-m',
            'pip',
            'install',
            '--require-hashes',
            '--only-binary=:all:',
            '-r',
            str(tools_lock),
        ],
        cwd=work,
    )
    runtime_lock = host / 'verification/ci-runtime-requirements.txt'
    if not runtime_lock.is_file():
        raise ValueError('hashed CI runtime lock is required')
    command.run(
        [
            executable,
            '-m',
            'pip',
            'install',
            '--require-hashes',
            '--only-binary=:all:',
            '-r',
            str(runtime_lock),
        ],
        cwd=work,
    )
    wheels = bundle / 'wheelhouse'
    wheels.mkdir()
    projects = work / 'build-sources'
    projects.mkdir()
    project_wheels = []
    for name, root in roots.items():
        destination = projects / name
        destination.mkdir()
        for relative, entry in source['components'][name]['files'].items():
            file = destination / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, file)
        command.run([executable, '-m', 'build', '--no-isolation', '--outdir', str(wheels), str(destination)], cwd=work)
    for path in wheels.glob('*.whl'):
        name = path.name.split('-')[0]
        owner = next((component for component in COMPONENTS if component.replace('-', '_') == name))
        import tomllib
        metadata = tomllib.loads((roots[owner] / 'pyproject.toml').read_text())
        declared = metadata.get('project', {}).get('optional-dependencies', {})
        requested = [extra for extra in ('dev', 'parquet', 'stream', 'zstd') if extra in declared]
        extra = '[' + ','.join(requested) + ']' if requested else ''
        project_wheels.append(str(path) + extra)
    if len(project_wheels) != 8:
        raise ValueError('eight real wheels required')
    command.run(
        [
            executable,
            '-m',
            'pip',
            'install',
            '--no-index',
            '--no-deps',
            '--find-links',
            str(wheels),
            *project_wheels,
        ],
        cwd=work,
    )
    command.run([executable, '-m', 'pip', 'check'], cwd=work)
    smoke = command.run([executable, '-c', 'import importlib,json,pathlib,sys; names=' + repr([name.replace('-', '_') for name in ('openpine', 'openpine-contracts', 'pine2ast', 'ast2python', 'pinelib', 'backtest_engine', 'marketdata-provider', 'optimizer')]) + '; origins={name:str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}; assert all(pathlib.Path(value).is_relative_to(pathlib.Path(sys.prefix).resolve()) for value in origins.values()), origins; print(json.dumps(origins))'], cwd=work)
    write_once_json(bundle / 'installed-origins.json', json.loads(smoke))
    command.run([executable, '-c', 'import json; from openpine.verification.execution_preflight import catalog_source_preflight; r=catalog_source_preflight(); print(json.dumps(r)); raise SystemExit(0 if r["ok"] else 1)'], cwd=work)
    command.run([executable, '-c', 'import json; from openpine.verification.execution_preflight import optimizer_process_preflight; r=optimizer_process_preflight(); print(json.dumps(r)); raise SystemExit(0 if r["ok"] else 1)'], cwd=work, roots=roots)
    observed = json.loads(command.run([executable, '-c', 'import json; from openpine.verification.execution_identity import environment_snapshot; print(json.dumps(environment_snapshot()))'], cwd=work, roots=roots))
    locked = '\n'.join((n + '==' + v for n, v in observed['distributions'].items())) + '\n'
    (bundle / 'locked-versions.txt').write_text(locked)
    command.run([executable, '-m', 'pip', 'download', '--require-hashes', '--no-deps', '--only-binary=:all:', '--dest', str(wheels), '-r', str(tools_lock), '-r', str(runtime_lock)], cwd=work)
    argv = [executable, '-m', 'openpine.verification', 'test-collect', '--host-root', str(roots['openpine']), '--stack-root', str(stack), '--python', 'py' + python_label.replace('.', '') + '=' + executable, '--output', str(bundle / 'collection.json'), '--collection-timeout', '600']
    for name in COMPONENTS:
        if name != 'openpine' or python_label in {'3.11', '3.13'}:
            argv += ['--component', name]
    command.run(argv, cwd=work, roots=roots)
    for private in (bundle / 'collection.evidence').rglob('private'):
        shutil.rmtree(private)
    if python_label == '3.13':
        command.run([executable, str(roots['openpine'] / 'scripts/export_openapi.py'), str(bundle / 'openapi.json')], cwd=roots['openpine'], roots=roots)
    result = bundle_manifest(bundle, source=source, environment=observed, source_pins=pins, host_commit=head)
    return result

def restore(bundle: Path, work: Path, *, expected_source_hash: str | None=None, expected_commits: dict[str, str] | None=None) -> tuple[dict, dict[str, Path], Path]:
    report = verify_bundle(bundle, expected_source_hash=expected_source_hash)
    source_commits = attest_ci_source_commits([report])
    if expected_commits is not None and source_commits != expected_commits:
        raise ValueError('restored producer commits differ from frozen plan')
    work.mkdir(parents=True, exist_ok=False)
    command = Commands(work)
    roots = unpack_source_archive(bundle / 'sources.tar.gz', work / 'stack', report['source'])
    for name, commit in source_commits.items():
        restore_git_provenance(roots[name], bundle / 'git' / (name + '.bundle'), name, commit)
    if source_snapshot(roots) != report['source']:
        raise ValueError('restored Git provenance changed archived source')
    environment = work / 'venv'
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
    executable = environment / 'bin/python'
    command.run([str(executable), '-m', 'pip', 'install', '--no-index', '--no-deps', '--find-links', str(bundle / 'wheelhouse'), '-r', str(bundle / 'locked-versions.txt')], cwd=work)
    command.run([str(executable), '-m', 'pip', 'check'], cwd=work)
    observed = json.loads(command.run([str(executable), '-c', 'import json; from openpine.verification.execution_identity import environment_snapshot; print(json.dumps(environment_snapshot()))'], cwd=work, roots=roots))
    if observed != report['environment']:
        raise ValueError('restored interpreter/environment differs from prepared lane')
    write_once_json(work / 'restored.json', {'candidate_hash': report['source']['content_hash'], 'roots': {n: str(p) for n, p in roots.items()}, 'executable': str(executable), 'environment': observed, 'source_commits': source_commits})
    return (report, roots, executable)

def make_owner_locations(reports: dict, *, stack_root: Path, attempt: Path, package_attempt: Path,
                         npm: Path, node: Path, chromium: Path) -> dict:
    """Locate existing verified preparations; do not create an acceptance receipt."""
    if set(reports) != {'py311', 'py312', 'py313'}:
        raise ValueError('all three owner preparation reports required')
    first = reports['py313']
    for report in reports.values():
        roots = {n: Path(p) for n, p in report['roots'].items()}
        if (report['candidate_hash'] != first['candidate_hash'] or
                report['source_commits'] != first['source_commits'] or
                source_snapshot(roots)['content_hash'] != first['candidate_hash']):
            raise ValueError('owner preparations belong to different candidates')
    host = Path(first['roots']['openpine'])
    locations = {'host': str(host), 'stack': str(stack_root),
                 'helpers': str(host / 'scripts/rc6_stabilization'),
                 'attempt': str(attempt), 'package_attempt': str(package_attempt),
                 'runner': first['executable'], 'uv': str(Path(first['executable']).parent / 'uv'),
                 'npm': str(npm), 'node': str(node), 'chromium': str(chromium)}
    locations.update({f'python{label[2:]}': report['executable'] for label, report in reports.items()})
    return locations


def make_ci_plan(collection_files: list[Path], roots: dict[str, Path], output: Path, source_commits: dict[str, str], *, owner_locations: dict | None = None) -> dict:
    from openpine.verification.execution_collections import join_collections
    from openpine.verification.execution_plan import make_plan, validate_plan
    collected = join_collections([read_json(p) for p in collection_files], roots)
    policy = read_json(roots['openpine'] / 'verification/execution-policy.json')
    from openpine.verification.execution_owner_launch import freeze_owner_launch
    owner_launch = freeze_owner_launch(policy, owner_locations) if owner_locations is not None else None
    plan = make_plan(profile='stage-full', policy=policy, roots=roots, source=collected['source'], inventories=collected['inventories'], environments=collected['environments'], shard_count=4, coverage=True, owner_launch=owner_launch)
    plan = seal({**{key: value for key, value in plan.items() if key != 'content_hash'}, 'source_commits': source_commits})
    validate_plan(plan)
    write_once_json(output, plan)
    return plan

def run_ci_task(plan: dict, plan_path: Path, roots: dict[str, Path], task_id: str, output: Path, run_id: str) -> dict:
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
    from openpine.verification.execution_coverage import combine_task_coverage
    matches = [t for t in plan['tasks'] if t['id'] == task_id]
    if len(matches) != 1:
        raise ValueError('unknown CI task')
    task = matches[0]
    binding = make_binding(plan, roots, {task['environment']: sys.executable})
    keys = [(task_id, s['id']) for s in task['shards']]
    from openpine.verification.execution_resources import resource_profile
    resources = resource_profile()
    memory_mib = min(6144, resources['memory_limit_bytes']//(1024*1024)*3//4) if resources['memory_limit_bytes'] else 6144
    run_campaign(plan, plan_path, output, jobs=min(2, resources['cpu_slots']), run_id=run_id, shard_keys=keys, binding=binding, memory_mib=memory_mib, build_commit=plan['source_commits']['openpine'])
    report = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=run_id, expected_shards=keys)
    write_once_json(output / 'aggregate.json', report)
    if not report['pytest_scope_passed']:
        raise ValueError('functional task failed; evidence retained')
    cov = combine_task_coverage(plan, output, task_id, output / 'coverage-owner', run_id=run_id, binding=binding)
    if not cov['ok']:
        raise ValueError('owner coverage threshold failed; not reduced')
    write_once_json(output / 'ci-task.json', seal({'schema_id': 'openpine.ci_task.v1', 'task': task_id, 'plan_hash': plan['content_hash'], 'run_id': run_id, 'run_sha256': hash_file(output / 'run.json'), 'coverage_sha256': hash_file(output / 'coverage-owner/receipt.json')}))
    return seal({'schema_id': 'openpine.ci_task_result.v1', 'ok': True, 'pytest_scope_passed': True, 'coverage_scope_passed': True, 'task': task_id, 'plan_hash': plan['content_hash'], 'run_id': run_id, 'scope': 'single component/interpreter only', 'full_stage_accepted': False})

def finalize_foundation(plan: dict, roots: dict[str, Path], fragments: list[Path], output: Path, run_id: str) -> dict:
    from openpine.verification.execution_fragments import merge_fragments
    from openpine.verification.execution_campaign import export_suite_receipts
    from openpine.verification.stage_gate import run_stage_gate
    from openpine.verification.execution_coverage import verify_task_coverage
    if source_snapshot(roots) != plan['source']:
        raise ValueError('foundation source differs from frozen candidate')
    covered = set()
    for fragment in fragments:
        descriptor = read_json(fragment / 'ci-task.json')
        verify(descriptor, 'openpine.ci_task.v1')
        task_id = descriptor['task']
        if task_id in covered or descriptor['plan_hash'] != plan['content_hash'] or descriptor['run_id'] != run_id:
            raise ValueError('duplicate or mismatched CI owner receipt')
        if descriptor['run_sha256'] != hash_file(fragment / 'run.json'):
            raise ValueError('CI task run changed')
        if descriptor['coverage_sha256'] != hash_file(fragment / 'coverage-owner/receipt.json'):
            raise ValueError('CI task coverage changed')
        verify_task_coverage(plan, fragment, task_id, fragment / 'coverage-owner', run_id=run_id, source_roots=roots)
        target = output / 'owner-coverage' / task_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fragment / 'coverage-owner', target, ignore=shutil.ignore_patterns('private'))
        covered.add(task_id)
    if covered != {t['id'] for t in plan['tasks']}:
        raise ValueError('missing mandatory component coverage owner receipt')
    combined = output / 'merged'
    aggregate = merge_fragments(plan, fragments, combined, expected_plan_hash=plan['content_hash'], expected_run_id=run_id)
    if not aggregate['pytest_scope_passed']:
        raise ValueError('incomplete common campaign')
    exports = output / 'suites'
    export_suite_receipts(plan, combined, exports, expected_plan_hash=plan['content_hash'], expected_run_id=run_id)
    env_id = 'py' + ''.join(map(str, sys.version_info[:2]))
    if env_id not in {'py311', 'py313'}:
        raise ValueError('foundation requires actual 3.11 or 3.13')
    expected = plan['environments'][env_id]['identity']
    if environment_snapshot() != expected:
        raise ValueError('foundation interpreter differs from its receipts')
    evidence = exports / env_id
    write_once_json(evidence / 'source-pins.json', read_json(roots['openpine'] / 'docs/RC6_LIFECYCLE_SOURCES.json'))
    raw = read_json(combined / 'run.json')
    for attempt in raw['attempts']:
        if not attempt['task'].endswith('@' + env_id):
            continue
        for name, desc in attempt['artifacts'].items():
            if not name.startswith('owner:observations/'):
                continue
            relative = name[len('owner:'):]
            target = evidence / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            origin = combined / desc['path']
            if target.exists():
                if hash_file(target) != desc['sha256']:
                    raise ValueError('conflicting frozen-corpus observations')
            else:
                shutil.copy2(origin, target)
    result = run_stage_gate(roots['openpine'], roots, evidence)
    return result

def finalize_stabilization(plan: dict, host: Path, foundations: list[Path], output: Path,
                           run_id: str, *, binding: dict | None = None) -> dict:
    """Join real native locators, then invoke the existing current CLI owner.

    Native artifacts do not qualify packages, frontend or performance. Absent
    owners remain absent; only stage_gate can decide acceptance. Historical
    command receipts and execution bindings are copied byte-for-byte.
    """
    import argparse
    from openpine.verification.execution_binding import checked_locations
    from openpine.verification.execution_campaign import aggregate_campaign
    from openpine.verification.execution_cli import add_commands, run_command
    from openpine.verification.execution_identity import ensure_external_output
    from openpine.verification.execution_plan import validate_plan

    validate_plan(plan)
    roots, _ = checked_locations(plan, binding)
    ensure_external_output(output, {name: Path(path) for name, path in roots.items()})
    if Path(roots['openpine']).resolve() != host.resolve():
        raise ValueError('common owner host differs from checked candidate')
    if not foundations:
        raise ValueError('common owner requires real native campaign primaries')
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / 'evidence'
    evidence.mkdir()

    def copy_primary(source: Path, destination: Path) -> None:
        if any(path.is_symlink() for path in (source, *source.parents)) or any(
            path.is_symlink() for path in source.rglob('*')
        ):
            raise ValueError('detached/symlink native owner primary')
        for path in sorted(source.rglob('*')):
            if not path.is_file() or 'private' in path.relative_to(source).parts:
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise ValueError('conflicting native owner primary')
            else:
                shutil.copy2(path, target)

    aggregate_hash = None
    environments, coverage = {}, {}
    for foundation in foundations:
        # Reject detached artifacts before a filesystem copy can hide them.
        if any(path.is_symlink() for path in (foundation, *foundation.parents)) or any(
            path.is_symlink() for path in foundation.rglob('*')
        ):
            raise ValueError('detached/symlink native owner primary')
        run = read_json(foundation / 'merged/run.json')
        if (run.get('plan_hash') != plan['content_hash'] or run.get('run_id') != run_id
                or run.get('candidate_hash') != plan['source']['content_hash']):
            raise ValueError('native primary is historical/stale or foreign')
        aggregate = aggregate_campaign(plan, foundation / 'merged',
                                       expected_plan_hash=plan['content_hash'], expected_run_id=run_id)
        if aggregate_hash is None:
            aggregate_hash = aggregate['content_hash']
            copy_primary(foundation / 'merged', evidence / 'merged')
        elif aggregate_hash != aggregate['content_hash']:
            raise ValueError('foundation owners refer to different common campaigns')
        for environment in plan['environments']:
            source = foundation / 'suites' / environment
            if (source / 'stage1.json').is_file():
                relative = 'suites/' + environment
                copy_primary(source, evidence / relative)
                environments[environment] = relative
        for task in plan['tasks']:
            source = foundation / 'owner-coverage' / task['id']
            if (source / 'receipt.json').is_file():
                relative = 'owner-coverage/' + task['id']
                copy_primary(source, evidence / relative)
                coverage[task['id']] = relative
    gates = {'branch-reconciliation': {}, 'protected-workers': {}}
    if environments:
        gates['foundation'] = {'environments': environments}
    if coverage:
        gates['coverage'] = {'tasks': coverage}
    write_once_json(evidence / 'stabilization-inputs.json', {
        'schema_id': 'openpine.rc6_stabilization_inputs.v1', 'plan_hash': plan['content_hash'],
        'candidate_hash': plan['source']['content_hash'], 'run_id': run_id,
        'campaign': 'merged', 'gates': gates,
    })
    plan_path = output / 'plan.json'
    write_once_json(plan_path, plan)
    common = ['--host-root', str(host), '--plan', str(plan_path), '--expected-plan-hash',
              plan['content_hash'], '--evidence', str(evidence), '--run-id', run_id]
    if binding is not None:
        write_once_json(output / 'binding.json', binding)
        common += ['--binding', str(output / 'binding.json')]
    parser = argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest='command', required=True))
    invocations, exits = [], []
    for command, filename, view in (
        ('test-stabilization', 'current.json', 'current'),
        ('test-current', 'replayed-current.json', 'current'),
        ('test-current', 'summary.json', 'summary'),
        ('test-current', 'remainder.json', 'remainder'),
    ):
        argv = [command, *common, '--output', str(output / filename), '--view', view]
        if command == 'test-current':
            argv += ['--saved-current', str(output / 'current.json')]
        invocations.append(argv)
        # Exit 1 is a recorded nonaccepted verdict, not a command success.
        # Reader/infrastructure exceptions propagate without a fake receipt.
        exits.append(run_command(parser.parse_args(argv)))
    write_once_json(output / 'invocations.json', {'argv': invocations, 'exits': exits})
    return read_json(output / 'current.json')


def add_ci_commands(commands):
    ci = commands.add_parser('test-ci', help='Prepare, restore and coordinate existing verification owners')
    sub = ci.add_subparsers(dest='ci_action', required=True)
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--host-root', type=Path, required=True)
    prepare_parser.add_argument('--work', type=Path, required=True)
    prepare_parser.add_argument('--python-version', required=True)
    planner = sub.add_parser('plan')
    planner.add_argument('--bundle', type=Path, action='append', required=True)
    planner.add_argument('--work', type=Path, required=True)
    planner.add_argument('--output', type=Path, required=True)
    planner.add_argument('--owner-locations', type=Path)
    owner_environment = sub.add_parser('owner-environment')
    owner_environment.add_argument('--bundle', type=Path, required=True)
    owner_environment.add_argument('--work', type=Path, required=True)
    for name in ('restore', 'task', 'foundation'):
        item = sub.add_parser(name)
        item.add_argument('--bundle', type=Path, required=True)
        item.add_argument('--work', type=Path, required=True)
        item.add_argument('--plan', type=Path, required=True)
        if name != 'restore':
            item.add_argument('--output', type=Path, required=True)
            item.add_argument('--run-id', required=True)
        if name == 'task':
            item.add_argument('--task', required=True)
        if name == 'foundation':
            item.add_argument('--fragment', type=Path, action='append', required=True)
    common = sub.add_parser('stabilization')
    common.add_argument('--restored', type=Path, required=True)
    common.add_argument('--plan', type=Path, required=True)
    common.add_argument('--foundation', type=Path, action='append', required=True)
    common.add_argument('--output', type=Path, required=True)
    common.add_argument('--run-id', required=True)
    inner = sub.add_parser('execute')
    inner.add_argument('--restored', type=Path, required=True)
    inner.add_argument('--plan', type=Path, required=True)
    inner.add_argument('--task')
    inner.add_argument('--fragment', type=Path, action='append', default=[])
    inner.add_argument('--output', type=Path, required=True)
    inner.add_argument('--run-id', required=True)

def run_ci_command(args):
    from openpine.verification.execution_plan import validate_plan
    if args.ci_action == 'owner-environment':
        restore(args.bundle.resolve(), args.work.resolve())
        return {'scope': 'owner-environment-preparation', 'restored': str(args.work / 'restored.json'), 'full_stage_accepted': False}
    if args.ci_action == 'prepare':
        return prepare(args.host_root.resolve(), args.work.resolve(), args.python_version)
    if args.ci_action == 'plan':
        reports = [verify_bundle(path) for path in args.bundle]
        source_commits = attest_ci_source_commits(reports)
        if any((row['source'] != reports[0]['source'] for row in reports)):
            raise ValueError('prepared interpreter lanes have different source candidates')
        roots = unpack_source_archive(args.bundle[0] / 'sources.tar.gz', args.work / 'stack', reports[0]['source'])
        owner_locations = read_json(args.owner_locations) if args.owner_locations else None
        plan = make_ci_plan([path / 'collection.json' for path in args.bundle], roots, args.output, source_commits, owner_locations=owner_locations)
        matrix = {'include': [{'task': task['id'], 'python': '.'.join(plan['environments'][task['environment']]['identity']['python'].split('.')[:2])} for task in plan['tasks']]}
        write_once_json(args.output.with_name('matrix.json'), matrix)
        return plan
    plan = validate_plan(read_json(args.plan))
    if 'source_commits' not in plan:
        raise ValueError('CI plan must bind exact producer commits')
    if args.ci_action in {'execute', 'stabilization'}:
        observed = read_json(args.restored)
        roots = {name: Path(path) for name, path in observed['roots'].items()}
        if source_snapshot(roots) != plan['source'] or observed['environment'] != environment_snapshot() or observed.get('source_commits') != plan['source_commits']:
            raise ValueError('restored interpreter, source or producer commits changed before execution')
        if args.ci_action == 'stabilization':
            from openpine.verification.execution_binding import make_binding
            environment = 'py' + ''.join(map(str, sys.version_info[:2]))
            binding = make_binding(plan, roots, {environment: sys.executable})
            return finalize_stabilization(plan, roots['openpine'], args.foundation,
                                          args.output.resolve(), args.run_id, binding=binding)
        if args.task:
            return run_ci_task(plan, args.plan.resolve(), roots, args.task, args.output.resolve(), args.run_id)
        if not args.fragment:
            raise ValueError('foundation execution requires all raw fragments')
        return finalize_foundation(plan, roots, args.fragment, args.output.resolve(), args.run_id)
    _, roots, executable = restore(args.bundle.resolve(), args.work.resolve(), expected_source_hash=plan['source']['content_hash'], expected_commits=plan['source_commits'])
    if args.ci_action == 'restore':
        return {'ok': True, 'restored': str(args.work / 'restored.json'), 'full_stage_accepted': False}
    argv = [str(executable), '-m', 'openpine.verification', 'test-ci', 'execute', '--restored', str(args.work.resolve() / 'restored.json'), '--plan', str(args.plan.resolve()), '--output', str(args.output.resolve()), '--run-id', args.run_id]
    if args.ci_action == 'task':
        argv += ['--task', args.task]
    else:
        for path in args.fragment:
            argv += ['--fragment', str(path.resolve())]
    Commands(args.work / 'execute-log').run(argv, cwd=roots['openpine'], roots=roots, timeout=7200)
    return {'ok': True, 'scope': args.ci_action, 'full_stage_accepted': False}
