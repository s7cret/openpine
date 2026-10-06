"""CLI adapters for existing verification owners and isolated CI preparation.

Source-mode testing is explicit. test-ci installs only into newly created build
venvs; it never publishes Git refs or alters production data/sandbox settings.
Neither a successful shard nor prepared wheels authorize full-stage acceptance.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from openpine.verification.execution_identity import clean_environment, ensure_external_output, environment_snapshot, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import PROFILES, make_plan
from openpine.verification.identity import digest, read_json, seal, verify
COMPONENTS = ('openpine', 'openpine-contracts', 'pine2ast', 'ast2python', 'pinelib', 'backtest_engine', 'marketdata-provider', 'optimizer')
COLLECTION_SCHEMA = 'openpine.test_collection_set.v1'

def add_commands(commands):
    from openpine.verification.execution_ci import add_ci_commands
    add_ci_commands(commands)
    for name in ('test-stabilization', 'test-current'):
        current = commands.add_parser(name, help='Re-read all raw RC6 owner evidence; Stage 2 remains separate')
        current.add_argument('--host-root', type=Path, required=True)
        current.add_argument('--plan', type=Path, required=True)
        current.add_argument('--expected-plan-hash', required=True)
        current.add_argument('--evidence', type=Path, required=True)
        current.add_argument('--run-id', required=True)
        current.add_argument('--output', type=Path, required=True)
        current.add_argument('--saved-current', type=Path)
        current.add_argument('--binding', type=Path, help='Checked replay source/interpreter locators; never rewrites execution provenance')
        current.add_argument('--view', choices=('current', 'progress', 'remainder', 'summary'), default='current')
    preflight = commands.add_parser('test-preflight', help='Read-only executable environment preflight')
    collect = commands.add_parser('test-collect', help='Collect and verify frozen inventories; never execution PASS')
    for command in (preflight, collect):
        command.add_argument('--host-root', type=Path, required=True)
        command.add_argument('--stack-root', type=Path, required=True)
        command.add_argument('--python', dest='interpreters', action='append', default=[], metavar='ID=EXECUTABLE')
        command.add_argument('--policy', type=Path)
        command.add_argument('--component', action='append', default=[])
        command.add_argument('--output', type=Path, required=True)
    preflight.add_argument('--level', choices=('pytest', 'full'), default='full')
    collect.add_argument('--inventory-lock', type=Path)
    collect.add_argument('--collection-timeout', type=int, default=120)
    join = commands.add_parser('test-join-collections', help='Join observed interpreter collections without changing source identity')
    join.add_argument('--collection', action='append', type=Path, required=True)
    join.add_argument('--host-root', type=Path, required=True)
    join.add_argument('--stack-root', type=Path, required=True)
    join.add_argument('--output', type=Path, required=True)
    planner = commands.add_parser('test-plan', help='Plan source-bound test shards from reviewed collections')
    planner.add_argument('--collection', type=Path, required=True)
    planner.add_argument('--policy', type=Path, required=True)
    planner.add_argument('--owner-locations', type=Path, help='Explicit typed owner locations frozen before execution')
    planner.add_argument('--profile', choices=PROFILES, required=True)
    planner.add_argument('--component', action='append', default=[])
    planner.add_argument('--changed', action='append', default=[], metavar='COMPONENT/PATH')
    planner.add_argument('--shards', type=int, default=1)
    planner.add_argument('--durations', type=Path)
    planner.add_argument('--coverage', action='store_true')
    planner.add_argument('--output', type=Path, required=True)
    runner = commands.add_parser('test-run', help='Execute bounded isolated process shards and aggregate')
    runner.add_argument('--plan', type=Path, required=True)
    runner.add_argument('--expected-plan-hash', required=True)
    runner.add_argument('--output', type=Path, required=True)
    runner.add_argument('--jobs', type=int, default=1)
    runner.add_argument('--max-parallel-shards', type=int)
    runner.add_argument('--run-id')
    runner.add_argument('--binding', type=Path)
    runner.add_argument('--task', action='append', default=[])
    runner.add_argument('--shard', action='append', default=[], metavar='TASK/SHARD')
    runner.add_argument('--memory-mib', type=int)
    bind = commands.add_parser('test-bind', help='Bind frozen inputs to verified local source locations')
    bind.add_argument('--plan', type=Path, required=True)
    bind.add_argument('--expected-plan-hash', required=True)
    bind.add_argument('--root', action='append', default=[], metavar='COMPONENT=PATH')
    bind.add_argument('--python', action='append', default=[], metavar='ID=EXECUTABLE')
    bind.add_argument('--output', type=Path, required=True)
    merge = commands.add_parser('test-merge', help='Transfer and verify raw results from all planned runners')
    merge.add_argument('--plan', type=Path, required=True)
    merge.add_argument('--expected-plan-hash', required=True)
    merge.add_argument('--fragment', action='append', type=Path, required=True)
    merge.add_argument('--run-id', required=True)
    merge.add_argument('--output', type=Path, required=True)
    aggregate = commands.add_parser('test-aggregate', help='Read and verify every shard, phase and JUnit')
    exporter = commands.add_parser('test-export-suites', help='Export verified complete component shards to the existing foundation owner')
    exporter.add_argument('--plan', type=Path, required=True)
    exporter.add_argument('--expected-plan-hash', required=True)
    exporter.add_argument('--evidence', type=Path, required=True)
    exporter.add_argument('--run-id', required=True)
    exporter.add_argument('--output', type=Path, required=True)
    aggregate.add_argument('--plan', type=Path, required=True)
    aggregate.add_argument('--expected-plan-hash', required=True)
    aggregate.add_argument('--evidence', type=Path, required=True)
    aggregate.add_argument('--run-id', required=True)
    aggregate.add_argument('--output', type=Path, required=True)

def _roots(args):
    roots = {name: (args.host_root if name == 'openpine' else args.stack_root / name).resolve() for name in COMPONENTS}
    for name, path in roots.items():
        if not path.is_dir():
            raise ValueError('missing component source: ' + name)
    return roots

def _python_specs(values):
    if not values:
        values = ['current=' + sys.executable]
    result = {}
    import re
    for value in values:
        name, separator, executable = value.partition('=')
        if not separator or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', name) or name in result:
            raise ValueError('interpreter must be unique ID=EXECUTABLE')
        found = shutil.which(executable)
        result[name] = str(Path(found or executable).absolute())
    return result

def probe_environment(roots, policy, components, level):
    """Executed inside the requested interpreter; returned facts are observed."""
    from packaging.requirements import Requirement
    from packaging.version import Version
    import importlib
    result = {'identity': environment_snapshot(), 'imports': {}, 'requirements': [], 'errors': []}
    from openpine.verification.execution_identity import validate_python_support
    try:
        validate_python_support(policy, result['identity'])
    except ValueError as error:
        result['errors'].append(str(error))
    source_versions = {}
    for root in roots.values():
        metadata_path = Path(root) / 'pyproject.toml'
        project = tomllib.loads(metadata_path.read_text())['project']
        source_versions[project['name'].lower().replace('_', '-')] = project.get('version')
    modules = ['pytest', 'openpine.verification.pytest_gate']
    for name in components:
        modules.extend(policy['components'][name].get('imports', []))
    for module in sorted(set(modules)):
        try:
            loaded = importlib.import_module(module)
            result['imports'][module] = getattr(loaded, '__file__', None)
            top = module.split('.')[0]
            owners = [n for n in components if top in [m.split('.')[0] for m in policy['components'][n].get('imports', [])]]
            for owner in owners:
                if loaded.__file__ is None or not Path(loaded.__file__).resolve().is_relative_to(Path(roots[owner]).resolve()):
                    result['errors'].append('import source origin mismatch: ' + module)
        except Exception as error:  # noqa: BLE001 -- supervision boundary records the real failure
            result['errors'].append(f'import {module}: {type(error).__name__}: {error}')
    requirements = ['pytest>=8.2', 'packaging']
    if level == 'full':
        requirements.extend(['build', 'wheel', 'setuptools', 'hatchling'])
        for name in components:
            data = tomllib.loads((Path(roots[name]) / 'pyproject.toml').read_text())
            requirements.extend(data.get('build-system', {}).get('requires', []))
            project = data.get('project', {})
            requirements.extend(project.get('dependencies', []))
            for extra in policy['components'][name].get('extras', []):
                requirements.extend(project.get('optional-dependencies', {}).get(extra, []))
    for text in sorted(set(requirements)):
        requirement = Requirement(text)
        if requirement.marker and (not requirement.marker.evaluate()):
            continue
        key = requirement.name.lower().replace('_', '-')
        version = source_versions.get(key) or result['identity']['distributions'].get(key)
        good = version is not None and (not requirement.specifier or Version(version) in requirement.specifier)
        result['requirements'].append({'requirement': text, 'observed_version': version, 'ok': good, 'origin': 'source-project' if source_versions.get(key) else 'installed-distribution'})
        if not good:
            result['errors'].append(f'unsatisfied requirement: {text}; observed={version}')
    if 'optimizer' in components:
        from openpine.verification.execution_preflight import optimizer_process_preflight
        capability = optimizer_process_preflight()
        result['optimizer_process_capability'] = capability
        result['errors'].extend(capability['errors'])
    result['ok'] = not result['errors']
    return result

def _probe(executable, roots, policy, components, level, folder):
    argv = [executable, '-c', 'import json,sys; from openpine.verification.execution_cli import probe_environment; print(json.dumps(probe_environment(**json.load(sys.stdin))))']
    env = clean_environment({n: str(p) for n, p in roots.items()}, folder / 'private')
    payload = json.dumps({'roots': {n: str(p) for n, p in roots.items()}, 'policy': policy, 'components': components, 'level': level})
    try:
        result = subprocess.run(argv, input=payload, text=True, capture_output=True, env=env, cwd=folder, timeout=60)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
        (folder / 'probe.stderr.log').write_text(result.stderr)
        if result.returncode:
            return {'ok': False, 'errors': ['interpreter probe failed'], 'returncode': result.returncode}
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {'ok': False, 'errors': [type(error).__name__ + ': ' + str(error)]}

def _prepare(args):
    roots = _roots(args)
    policy_path = args.policy or args.host_root / 'verification/execution-policy.json'
    policy = read_json(policy_path)
    components = args.component or list(COMPONENTS)
    if any((name not in policy['components'] for name in components)):
        raise ValueError('unknown selected component')
    return (roots, policy, sorted(set(components)), _python_specs(args.interpreters))

def preflight(args):
    roots, policy, components, interpreters = _prepare(args)
    ensure_external_output(args.output, roots)
    evidence = args.output.with_suffix('.evidence')
    evidence.mkdir(parents=True, exist_ok=False)
    results, errors = ({}, [])
    for name, executable in interpreters.items():
        folder = evidence / name
        folder.mkdir()
        results[name] = _probe(executable, roots, policy, components, args.level, folder)
        errors.extend((name + ': ' + e for e in results[name].get('errors', [])))
    versions = {'.'.join(r['identity']['python'].split('.')[:2]) for r in results.values() if r.get('identity')}
    worker = {'status': 'not_run', 'protected_worker_verified': False}
    node = {'status': 'not_run'}
    if args.level == 'full':
        required = set().union(*(set(policy['components'][n]['pythons']) for n in components))
        errors.extend(('mandatory Python not observed: ' + v for v in sorted(required - versions)))
        try:
            command = subprocess.run(['node', '--version'], text=True, capture_output=True, timeout=10)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
            actual = command.stdout.strip()
            node = {'version': actual, 'returncode': command.returncode, 'required_major': str(policy.get('node_major', 24))}
            if command.returncode or actual.lstrip('v').split('.')[0] != node['required_major']:
                errors.append('Node toolchain mismatch')
        except (OSError, subprocess.TimeoutExpired) as error:
            node = {'error': str(error)}
            errors.append('Node probe unavailable')
        bwrap = shutil.which('bwrap')
        if bwrap:
            command = subprocess.run([bwrap, '--ro-bind', '/', '/', '--unshare-net', '--', '/usr/bin/true'], text=True, capture_output=True, timeout=15)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
            worker['isolation_probe_returncode'] = command.returncode
            worker['isolation_probe_stderr'] = command.stderr
        else:
            worker['reason'] = 'bubblewrap executable not available'
        errors.append('protected-worker owner verification is not executed by this preflight')
    from openpine.verification.execution_resources import resource_profile
    report = seal({'schema_id': 'openpine.test_preflight.v1', 'scope': args.level, 'source': source_snapshot(roots), 'policy_hash': digest(policy), 'environments': results, 'worker': worker, 'node': node, 'disk_free_bytes': shutil.disk_usage(evidence).free, 'resource_profile': resource_profile(), 'affinity_cpus': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count(), 'errors': errors, 'ok': not errors, 'full_stage_accepted': False})
    write_once_json(args.output, report)
    return report

def collect_inventories(args):
    roots, policy, components, interpreters = _prepare(args)
    ensure_external_output(args.output, roots)
    evidence = args.output.with_suffix('.evidence')
    evidence.mkdir(parents=True, exist_ok=False)
    lock_path = (args.inventory_lock or args.host_root / 'verification/inventory.json').resolve()
    locks = read_json(lock_path)
    source = source_snapshot(roots)
    inventories, environments, errors = ({}, {}, [])
    for env_id, executable in interpreters.items():
        env_folder = evidence / env_id
        env_folder.mkdir()
        observed = _probe(executable, roots, policy, [], 'pytest', env_folder)
        if not observed['ok']:
            errors.extend((env_id + ': ' + e for e in observed['errors']))
            continue
        identity = observed['identity']
        environments[env_id] = {'executable': executable, 'identity': identity}
        for name in components:
            folder = env_folder / name
            folder.mkdir()
            settings = policy['components'][name]
            selection = list(settings.get('selectors', []))
            if name == 'openpine':
                selection += read_json(args.host_root / 'rc6_tests/selected_regressions.json')
            argv = [executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'openpine.verification.pytest_gate', '--verification-lock=' + str(lock_path), '--verification-suite=' + name, '--verification-output=' + str(folder / 'inventory.json')]
            for plugin in settings.get('plugins', []):
                argv.extend(['-p', plugin])
            if settings.get('markers'):
                argv.extend(['-m', settings['markers']])
            argv.extend(selection)
            started = time.perf_counter()
            env = clean_environment({k: str(v) for k, v in roots.items()}, folder / 'private')
            try:
                with (folder / 'collection.log').open('x') as log:
                    process = subprocess.run(argv, cwd=roots[name], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.collection_timeout)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
                collected = read_json(folder / 'inventory.json')
                if process.returncode or collected.get('errors') or (not collected.get('collect_only')):
                    raise ValueError('collection/locked inventory validation failed')
                inventories[name + '@' + env_id] = {'nodeids': collected['nodeids'], 'node_markers': collected.get('node_markers', {}), 'deselected': collected['deselected'], 'reviewed_lock': locks[name], 'source_hash': source['content_hash'], 'environment_hash': identity['content_hash'], 'argv': argv, 'collection_wall_seconds': time.perf_counter() - started, 'receipt_file_sha256': hash_file(folder / 'inventory.json')}
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                errors.append(name + '@' + env_id + ': ' + str(error))
    after = source_snapshot(roots)
    if after['content_hash'] != source['content_hash']:
        errors.append('source changed during collection')
    report = seal({'schema_id': COLLECTION_SCHEMA, 'source': source, 'roots': {k: str(v) for k, v in roots.items()}, 'policy_hash': digest(policy), 'environments': environments, 'inventories': inventories, 'errors': errors, 'ok': not errors, 'collect_only': True, 'execution_pass': False})
    write_once_json(args.output, report)
    return report

def run_command(args):
    if args.command in {'test-stabilization', 'test-current'}:
        from openpine.verification.stage_gate import current_views, run_stabilization_gate
        plan = read_json(args.plan)
        from openpine.verification.execution_binding import checked_locations
        binding = read_json(args.binding) if args.binding else None
        roots, _ = checked_locations(plan, binding)
        ensure_external_output(args.output, {n: Path(p) for n, p in roots.items()})
        report = run_stabilization_gate(args.host_root, plan, args.evidence,
                                        expected_plan_hash=args.expected_plan_hash, run_id=args.run_id,
                                        binding=binding)
        if args.saved_current is not None and read_json(args.saved_current) != report:
            raise ValueError('saved current verdict differs from fresh raw-evidence replay')
        view = report if args.view == 'current' else current_views(report)[args.view]
        write_once_json(args.output, view)
        print(json.dumps(current_views(report)['summary'], indent=2))
        return 0 if report['ok'] else 1
    if args.command in {'test-run', 'test-aggregate', 'test-export-suites'}:
        from openpine.verification.execution_campaign import aggregate_campaign, export_suite_receipts, run_campaign
    if args.command == 'test-ci':
        from openpine.verification.execution_ci import run_ci_command
        report = run_ci_command(args)
    elif args.command == 'test-bind':
        from openpine.verification.execution_binding import make_binding
        from openpine.verification.execution_plan import validate_plan
        plan = validate_plan(read_json(args.plan), expected_hash=args.expected_plan_hash)

        def pairs(values):
            result = {}
            for item in values:
                key, sep, value = item.partition('=')
                if not key or not sep or (not value) or (key in result):
                    raise ValueError('need unique NAME=PATH bindings')
                result[key] = value
            return result
        roots = {n: Path(p) for n, p in pairs(args.root).items()}
        report = make_binding(plan, roots, pairs(args.python))
        ensure_external_output(args.output, roots)
        write_once_json(args.output, report)
    elif args.command == 'test-merge':
        from openpine.verification.execution_fragments import merge_fragments
        report = merge_fragments(read_json(args.plan), args.fragment, args.output, expected_plan_hash=args.expected_plan_hash, expected_run_id=args.run_id)
    elif args.command == 'test-preflight':
        report = preflight(args)
    elif args.command == 'test-collect':
        report = collect_inventories(args)
    elif args.command == 'test-join-collections':
        from openpine.verification.execution_collections import join_collections
        roots = _roots(args)
        ensure_external_output(args.output, roots)
        report = join_collections([read_json(path) for path in args.collection], roots)
        write_once_json(args.output, report)
    elif args.command == 'test-plan':
        collection = read_json(args.collection)
        verify(collection, COLLECTION_SCHEMA)
        policy = read_json(args.policy)
        if not collection['ok'] or collection['errors'] or collection['policy_hash'] != digest(policy):
            raise ValueError('collection is incomplete or belongs to a different policy')
        roots = {n: Path(p) for n, p in collection['roots'].items()}
        ensure_external_output(args.output, roots)
        if source_snapshot(roots)['content_hash'] != collection['source']['content_hash']:
            raise ValueError('source changed after collection')
        from openpine.verification.execution_owner_launch import freeze_owner_launch
        owner_launch = freeze_owner_launch(policy, read_json(args.owner_locations)) if args.owner_locations else None
        report = make_plan(profile=args.profile, policy=policy, roots=roots, source=collection['source'], inventories=collection['inventories'], environments=collection['environments'], requested=args.component, changes=args.changed, shard_count=args.shards, durations=read_json(args.durations) if args.durations else None, coverage=args.coverage, owner_launch=owner_launch)
        write_once_json(args.output, report)
    elif args.command == 'test-run':
        from openpine.verification.execution_plan import validate_plan
        plan = validate_plan(read_json(args.plan), expected_hash=args.expected_plan_hash)
        keys = None
        if args.task or args.shard:
            task_ids = {task['id'] for task in plan['tasks']}
            if len(set(args.task)) != len(args.task) or not set(args.task).issubset(task_ids):
                raise ValueError('unknown or duplicate task selection')
            keys = [(t['id'], s['id']) for t in plan['tasks'] if t['id'] in args.task for s in t['shards']]
            for value in args.shard:
                task, separator, shard = value.partition('/')
                if not separator:
                    raise ValueError('shard needs TASK/SHARD')
                keys.append((task, shard))
        binding = read_json(args.binding) if args.binding else None
        run = run_campaign(plan, args.plan, args.output, jobs=args.jobs, max_parallel_shards=args.max_parallel_shards, run_id=args.run_id, shard_keys=keys, binding=binding, memory_mib=args.memory_mib)
        report = aggregate_campaign(plan, args.output, expected_plan_hash=args.expected_plan_hash, expected_run_id=run['run_id'], expected_shards=keys)
        write_once_json(args.output / 'aggregate.json', report)
    elif args.command == 'test-export-suites':
        report = export_suite_receipts(read_json(args.plan), args.evidence, args.output, expected_plan_hash=args.expected_plan_hash, expected_run_id=args.run_id)
    else:
        report = aggregate_campaign(read_json(args.plan), args.evidence, expected_plan_hash=args.expected_plan_hash, expected_run_id=args.run_id)
        write_once_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('schema_id', 'content_hash', 'ok', 'pytest_scope_passed', 'errors') if k in report}, indent=2))
    return 0 if report.get('ok', True) else 1
