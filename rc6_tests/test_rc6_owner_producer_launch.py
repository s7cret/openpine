"""Producer launch locations are not policy or semantic authority."""
import runpy
from pathlib import Path

import pytest

from openpine.verification.execution_owner_launch import freeze_owner_launch, resolve_owner_policy


def locator_policy():
    return {'schema_id': 'openpine.execution_policy.v2',
            'owner_locator_slots': {'attempt': 'directory', 'python': 'executable'},
            'stabilization': {'frontend': {'commands': [
                {'role': 'frontend-tests',
                 'argv': [{'owner_locator': 'python'}, '-I',
                          {'owner_locator': 'attempt', 'relative': 'results.json', 'prefix': '--output='}],
                 'cwd': {'owner_locator': 'attempt', 'relative': 'source'},
                 'expected_stdout': {'count': 7}}]}}}


@pytest.mark.parametrize('name', ['launch-a', 'launch-b'])
def test_owner_launch_resolves_only_declared_locations(tmp_path, name):
    import sys
    from openpine.verification.identity import digest
    policy = locator_policy()
    attempt = tmp_path / name
    paths = {'attempt': str(attempt), 'python': sys.executable}
    launch = freeze_owner_launch(policy, paths)
    resolved = resolve_owner_policy(policy, launch)
    assert launch['policy_hash'] == digest(policy)
    spec = resolved['stabilization']['frontend']['commands'][0]
    assert spec['argv'] == [sys.executable, '-I', '--output=' + str(attempt / 'results.json')]
    assert spec['cwd'] == str(attempt / 'source')
    assert spec['expected_stdout'] == {'count': 7}
    assert not attempt.exists()


@pytest.mark.parametrize('mutation', ['extra-authority', 'missing', 'traversal', 'relative', 'symlink', 'wrong-policy', 'tool-drift', 'semantic-locator', 'nested-semantic-locator', 'unknown-slot', 'relative-escape', 'shell-prefix'])
def test_owner_launch_cannot_override_authority(tmp_path, mutation):
    import sys
    from openpine.verification.identity import seal
    policy = locator_policy()
    tool = tmp_path / 'python'
    tool.write_bytes(Path(sys.executable).read_bytes())
    tool.chmod(0o700)
    paths = {'attempt': str(tmp_path / 'attempt'), 'python': str(tool)}
    if mutation == 'extra-authority':
        paths['expected_stdout'] = '{}'
    elif mutation == 'missing':
        paths.pop('python')
    elif mutation == 'traversal':
        paths['attempt'] = str(tmp_path / 'x' / '..' / 'attempt')
    elif mutation == 'relative':
        paths['attempt'] = 'relative'
    elif mutation == 'symlink':
        link = tmp_path / 'link'
        link.symlink_to(tmp_path, target_is_directory=True)
        paths['attempt'] = str(link / 'attempt')
    elif mutation == 'semantic-locator':
        policy['stabilization']['frontend']['commands'][0]['expected_stdout'] = {'owner_locator': 'attempt'}
    elif mutation == 'nested-semantic-locator':
        policy['stabilization']['frontend']['commands'][0]['expected_stdout'] = {'path': {'owner_locator': 'attempt'}}
    elif mutation == 'unknown-slot':
        policy['stabilization']['frontend']['commands'][0]['cwd'] = {'owner_locator': 'undeclared'}
    elif mutation == 'relative-escape':
        policy['stabilization']['frontend']['commands'][0]['cwd']['relative'] = '../escape'
    elif mutation == 'shell-prefix':
        policy['stabilization']['frontend']['commands'][0]['argv'][-1]['prefix'] = ';exec '
    with pytest.raises((ValueError, OSError)):
        launch = freeze_owner_launch(policy, paths)
        if mutation == 'wrong-policy':
            launch = {k: v for k, v in launch.items() if k != 'content_hash'}
            launch['policy_hash'] = 'sha256:' + '0' * 64
            launch = seal(launch)
        if mutation == 'tool-drift':
            tool.write_bytes(b'changed')
        resolve_owner_policy(policy, launch, check_live=True)


def test_portable_owner_launch_is_frozen_into_plan(tmp_path):
    import sys
    from rc6_tests.test_rc6_execution_platform import tiny_plan, lock
    from openpine.verification.execution_plan import make_plan, validate_plan
    from openpine.verification.identity import seal
    previous, _ = tiny_plan(tmp_path)
    policy = locator_policy()
    policy['components'] = {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']}}
    roots = {n: Path(p) for n, p in previous['roots'].items()}
    nodes = previous['tasks'][0]['nodeids']
    inv = {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': previous['source']['content_hash'], 'environment_hash': previous['environments']['py']['identity']['content_hash']}}
    launch = freeze_owner_launch(policy, {'attempt': str(tmp_path / 'attempt'), 'python': sys.executable})
    args = dict(profile='component', policy=policy, roots=roots, source=previous['source'], inventories=inv, environments=previous['environments'], requested=['tiny'])
    with pytest.raises(ValueError, match='launch'):
        make_plan(**args)
    plan = make_plan(**args, owner_launch=launch)
    assert plan['owner_launch'] == launch
    assert validate_plan(plan) == plan
    changed = {k: v for k, v in plan.items() if k != 'content_hash'}
    changed['owner_launch'] = {k: v for k, v in launch.items() if k != 'content_hash'}
    changed['owner_launch']['policy_hash'] = 'sha256:' + '0' * 64
    changed['owner_launch'] = seal(changed['owner_launch'])
    with pytest.raises(ValueError, match='launch'):
        validate_plan(seal(changed))


def test_common_gate_reads_frozen_portable_owner_launch(tmp_path):
    from rc6_tests.stabilization_fixture import build_fixture
    from openpine.verification.stage_gate import run_stabilization_gate
    host, plan, evidence, packet = build_fixture(tmp_path, portable=True)
    verdict = run_stabilization_gate(host, plan, evidence, expected_plan_hash=plan['content_hash'], run_id=packet['run_id'])
    assert all(g['status'] == 'passed' for g in verdict['stabilization']['gates'].values()), verdict['stabilization']['gates']
    assert verdict['stage2']['full_stage2_accepted'] is False
    assert verdict['full_release_accepted'] is False


@pytest.mark.parametrize('name', ['producer-a', 'producer-b'])
def test_frontend_declaration_uses_explicit_tools_and_optional_proxy(tmp_path, monkeypatch, name):
    import sys
    root = tmp_path / name
    monkeypatch.setenv('OPENPINE_ATTEMPT_ROOT', str(root))
    module = runpy.run_path(str(HOST / 'scripts/rc6_stabilization/frontend_exact.py'))
    commands = module['specifications'](attempt_root=root, npm=str(tmp_path / 'toolchain/npm'), python=sys.executable)
    assert commands[0]['argv'][0] == sys.executable
    assert commands[0]['cwd'] == str(root / 'build-copies/openpine')
    assert commands[1]['argv'] == [str(tmp_path / 'toolchain/npm'), 'ci']
    assert commands[-1]['argv'] == [str(tmp_path / 'toolchain/npm'), 'audit', '--audit-level=moderate', '--json']
    assert 'rc6-stabilization-20260930' not in str(commands).replace(sys.executable, '')
    proxied = module['specifications'](attempt_root=root, npm=str(tmp_path / 'toolchain/npm'), python=sys.executable, proxy='http://localhost:1083')
    assert proxied[1]['argv'][-2:] == ['--proxy=http://localhost:1083', '--https-proxy=http://localhost:1083']
    assert not root.exists()
    host = tmp_path / 'independent-host'
    relocated = module['specifications'](attempt_root=root, host_root=host, npm=str(tmp_path/'toolchain/npm'), python=sys.executable)
    assert relocated[0]['cwd'] == str(host)
    assert relocated[0]['argv'][2] == str(host/'scripts/export_openapi.py')
    assert relocated[1]['cwd'] == str(root/'frontend/source')


@pytest.mark.parametrize('mutation', [None, 'policy', 'output', 'helper-path', 'helper-bytes', 'helper-symlink', 'tool-bytes'])
def test_producer_policy_is_checked_before_execution(tmp_path, mutation):
    import sys
    from openpine.verification.execution_owner_launch import resolve_producer_policy
    from openpine.verification.execution_identity import hash_file
    from openpine.verification.identity import digest, seal
    helpers = tmp_path / 'helpers'
    helpers.mkdir()
    helper = helpers / 'harness.py'
    helper.write_text('# synthetic unit fixture\n')
    tool = tmp_path / 'python'
    tool.write_bytes(Path(sys.executable).read_bytes())
    policy = locator_policy()
    policy['owner_locator_slots']['helpers'] = 'directory'
    policy['stabilization']['package_harness_inputs'] = {'harness.py': {'path': {'owner_locator': 'helpers', 'relative': 'harness.py'}, 'sha256': hash_file(helper)}}
    attempt = tmp_path / 'attempt'
    launch = freeze_owner_launch(policy, {'attempt': str(attempt), 'python': str(tool), 'helpers': str(helpers)})
    plan = seal({'schema_id': 'openpine.test_execution_plan.v1', 'policy_hash': digest(policy), 'owner_launch': launch})
    source_policy = __import__('copy').deepcopy(policy)
    if mutation == 'policy':
        source_policy['stabilization']['frontend']['commands'][0]['expected_stdout']['count'] = 8
    elif mutation == 'output':
        attempt = tmp_path / 'foreign-attempt'
    elif mutation == 'helper-path':
        foreign = tmp_path / 'foreign-helpers'
        foreign.mkdir()
        (foreign / 'harness.py').write_bytes(helper.read_bytes())
        helpers = foreign
    elif mutation == 'helper-bytes':
        helper.write_text('# unreviewed change\n')
    elif mutation == 'helper-symlink':
        target = tmp_path / 'target.py'
        helper.rename(target)
        helper.symlink_to(target)
    elif mutation == 'tool-bytes':
        tool.write_bytes(b'unreviewed executable')
    if mutation is not None:
        with pytest.raises((ValueError, OSError)):
            resolve_producer_policy(plan, source_policy, attempt=attempt, helpers=helpers)
    else:
        resolved = resolve_producer_policy(plan, source_policy, attempt=attempt, helpers=helpers)
        assert resolved['stabilization']['package_harness_inputs']['harness.py']['path'] == str(helper)
        assert resolved['stabilization']['frontend']['commands'][0]['expected_stdout'] == {'count': 7}
    assert not attempt.exists()
    assert digest(policy) == plan['policy_hash']


def test_plan_cli_freezes_explicit_owner_locations(tmp_path):
    import argparse
    import json
    import sys
    from openpine.verification import execution_cli as cli
    from openpine.verification.identity import digest, seal
    from rc6_tests.test_rc6_execution_platform import tiny_plan, lock
    previous, _ = tiny_plan(tmp_path)
    policy = locator_policy()
    policy['components'] = {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']}}
    nodes = previous['tasks'][0]['nodeids']
    collection = seal({'schema_id': cli.COLLECTION_SCHEMA, 'ok': True, 'errors': [], 'policy_hash': digest(policy), 'roots': previous['roots'], 'source': previous['source'], 'environments': previous['environments'], 'inventories': {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': previous['source']['content_hash'], 'environment_hash': previous['environments']['py']['identity']['content_hash']}}})
    for name, value in [('collection', collection), ('policy', policy), ('locations', {'attempt': str(tmp_path / 'attempt'), 'python': sys.executable})]:
        (tmp_path / (name + '.json')).write_text(json.dumps(value))
    parser = argparse.ArgumentParser()
    cli.add_commands(parser.add_subparsers(dest='command'))
    output = tmp_path / 'portable-plan.json'
    args = parser.parse_args(['test-plan', '--collection', str(tmp_path/'collection.json'), '--policy', str(tmp_path/'policy.json'), '--owner-locations', str(tmp_path/'locations.json'), '--profile', 'component', '--component', 'tiny', '--output', str(output)])
    assert cli.run_command(args) == 0
    plan = json.loads(output.read_text())
    assert plan['owner_launch']['paths']['attempt'] == str(tmp_path/'attempt')
    assert plan['owner_launch']['policy_hash'] == digest(policy)
    assert not (tmp_path/'attempt').exists()


def test_reviewed_owner_policy_has_no_machine_locations():
    import json
    from openpine.verification.execution_identity import hash_file
    policy = json.loads((HOST/'verification/execution-policy.json').read_text())
    assert policy['schema_id'] == 'openpine.execution_policy.v2'
    assert '/home/moltbot1/' not in json.dumps(policy)
    assert policy['owner_locator_slots']['package_attempt'] == 'directory'
    for name, declaration in policy['stabilization']['package_harness_inputs'].items():
        assert declaration['path'] == {'owner_locator': 'helpers', 'relative': name}
        assert declaration['sha256'] == hash_file(HOST/'scripts/rc6_stabilization'/name)
    for spec in policy['stabilization']['frontend']['commands']:
        assert not any(isinstance(arg, str) and arg.startswith(('--proxy=', '--https-proxy=')) for arg in spec['argv'])


def test_ci_planner_freezes_owner_locations_before_existing_plan(tmp_path, monkeypatch):
    import json
    import sys
    from openpine.verification import execution_ci as ci, execution_collections as collections, execution_plan as planner
    policy = locator_policy()
    host = tmp_path/'host'
    (host/'verification').mkdir(parents=True)
    (host/'verification/execution-policy.json').write_text(json.dumps(policy))
    roots = {'openpine': host}
    monkeypatch.setattr(collections, 'join_collections', lambda rows, checked_roots: {'source': {}, 'inventories': {}, 'environments': {}})
    class PlannerBoundaryReached(Exception):
        pass
    def existing_plan(**kwargs):
        assert kwargs['policy'] == policy
        assert kwargs['roots'] == roots
        resolved = resolve_owner_policy(policy, kwargs['owner_launch'], check_live=True)
        assert resolved['stabilization']['frontend']['commands'][0]['expected_stdout'] == {'count': 7}
        raise PlannerBoundaryReached
    monkeypatch.setattr(planner, 'make_plan', existing_plan)
    with pytest.raises(PlannerBoundaryReached):
        ci.make_ci_plan([], roots, tmp_path/'plan.json', {}, owner_locations={'attempt': str(tmp_path/'attempt'), 'python': sys.executable})
    assert not (tmp_path/'plan.json').exists()


def test_ci_plan_parser_accepts_explicit_owner_locations(tmp_path):
    import argparse
    from openpine.verification.execution_cli import add_commands
    parser=argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest='command'))
    args=parser.parse_args(['test-ci','plan','--bundle',str(tmp_path/'bundle'),'--work',str(tmp_path/'work'),'--output',str(tmp_path/'plan.json'),'--owner-locations',str(tmp_path/'locations.json')])
    assert args.owner_locations == tmp_path/'locations.json'


def test_owner_launch_freezes_command_tool_inputs(tmp_path):
    import sys
    from openpine.verification.execution_identity import hash_file
    policy=locator_policy()
    policy['capture_owner_executables']=True
    policy['owner_locator_slots']['node']='executable'
    policy['owner_locator_slots']['unused']='executable'
    policy['owner_tool_dependencies']={'python':['node']}
    paths={'attempt':str(tmp_path/'attempt'),'python':sys.executable,'node':sys.executable,'unused':sys.executable}
    launch=freeze_owner_launch(policy,paths)
    resolved=resolve_owner_policy(policy,launch)
    inputs=resolved['stabilization']['frontend']['commands'][0]['inputs']
    assert resolved['stabilization']['frontend']['commands'][0]['argv'][0] == sys.executable
    assert inputs == {'owner-tool:python':{'path':str(Path(sys.executable).resolve()),'sha256':hash_file(Path(sys.executable).resolve())}, 'owner-tool:node':{'path':str(Path(sys.executable).resolve()),'sha256':hash_file(Path(sys.executable).resolve())}}
    assert 'owner-tool:unused' not in inputs


def test_owner_tool_capture_preserves_resolved_target_on_replay(tmp_path):
    import sys
    from openpine.verification.identity import seal
    tool=tmp_path/'real-python'
    tool.write_bytes(Path(sys.executable).read_bytes())
    link=tmp_path/'python'
    link.symlink_to(tool)
    policy=locator_policy()
    policy['capture_owner_executables']=True
    launch=freeze_owner_launch(policy,{'attempt':str(tmp_path/'attempt'),'python':str(link)})
    assert launch['executable_targets']['python'] == str(tool)
    resolved=resolve_owner_policy(policy,launch,check_live=True)
    expected=resolved['stabilization']['frontend']['commands'][0]['inputs']
    assert expected['owner-tool:python']['path'] == str(tool)
    link.unlink(); tool.unlink()
    assert resolve_owner_policy(policy,launch)['stabilization']['frontend']['commands'][0]['inputs'] == expected
    historical=seal({'schema_id':'openpine.owner_launch.v1','policy_hash':launch['policy_hash'],'paths':launch['paths'],'executables':launch['executables']})
    with pytest.raises(ValueError,match='target'):
        resolve_owner_policy(policy,historical)
    legacy_policy=locator_policy()
    from openpine.verification.identity import digest
    historical=seal({'schema_id':'openpine.owner_launch.v1','policy_hash':digest(legacy_policy),'paths':launch['paths'],'executables':launch['executables']})
    assert resolve_owner_policy(legacy_policy,historical)['stabilization']['frontend']['commands'][0]['argv'][0] == str(link)


def test_package_runner_captures_tools_from_independent_command_specs(tmp_path):
    import json
    import sys
    from openpine.verification.execution_identity import hash_file
    module=runpy.run_path(str(HOST/'scripts/rc6_stabilization/harness.py'))
    output=tmp_path/'attempt'
    output.mkdir()
    command=[sys.executable,'-c','print("synthetic unit fixture")']
    expected=[sys.executable,str(HOST/'scripts/rc6_stabilization/measure.py'),str(output/'logs/000-unit/resources.json'),*command]
    tool={'path':str(Path(sys.executable).resolve()),'sha256':hash_file(Path(sys.executable).resolve())}
    runner=module['Runner'](output,{},command_specs=[{'role':'unit','argv':expected,'cwd':str(output/'cwd'),'inputs':{'owner-tool:runner':tool}}])
    row=runner.run('unit',command)
    assert row['ok']
    receipt=json.loads((output/row['command']['path']).read_text())
    captured=receipt['input_provenance']['owner-tool:runner']
    assert captured['source'] == tool
    assert captured['after_sha256'] == tool['sha256']
    assert hash_file((output/row['command']['path']).parent/captured['captured']['path']) == tool['sha256']


def test_package_runner_uses_explicit_uv_location(tmp_path):
    import sys
    module=runpy.run_path(str(HOST/'scripts/rc6_stabilization/harness.py'))
    tool=tmp_path/'uv-unit-fixture'
    tool.symlink_to(Path(sys.executable).resolve())
    output=tmp_path/'attempt'
    output.mkdir()
    runner=module['Runner'](output,{},uv=str(tool))
    row=runner.run('unit-explicit-tool',['uv','--version'])
    assert row['ok']
    assert row['argv'] == [str(tool),'--version']
    assert row['measured_argv'][3:] == [str(tool),'--version']


def test_frontend_producer_checks_its_tools_without_unrelated_package_locations(tmp_path):
    import sys
    from openpine.verification.execution_owner_launch import resolve_producer_policy
    from openpine.verification.identity import seal,digest
    policy=locator_policy()
    policy['capture_owner_executables']=True
    policy['owner_tool_scopes']={'frontend':['python'],'packages':['python','unrelated']}
    policy['owner_locator_slots']['unrelated']='executable'
    unused=tmp_path/'unused-package-python'
    unused.write_bytes(Path(sys.executable).read_bytes())
    attempt=tmp_path/'attempt'
    launch=freeze_owner_launch(policy,{'attempt':str(attempt),'python':sys.executable,'unrelated':str(unused)})
    plan=seal({'schema_id':'openpine.test_execution_plan.v1','policy_hash':digest(policy),'owner_launch':launch})
    unused.unlink()
    result=resolve_producer_policy(plan,policy,attempt=attempt,owner='frontend')
    assert result['stabilization']['frontend']['commands'][0]['expected_stdout'] == {'count':7}
    with pytest.raises((ValueError,OSError)):
        resolve_producer_policy(plan,policy,attempt=attempt,owner='packages')
    wrong=__import__('copy').deepcopy(policy)
    wrong['owner_tool_scopes']['frontend']=[]
    wrong_launch=seal({**{k:v for k,v in launch.items() if k!='content_hash'},'policy_hash':digest(wrong)})
    wrong_plan=seal({'schema_id':'openpine.test_execution_plan.v1','policy_hash':digest(wrong),'owner_launch':wrong_launch})
    with pytest.raises(ValueError,match='scope'):
        resolve_producer_policy(wrong_plan,wrong,attempt=attempt,owner='frontend')


@pytest.mark.parametrize('unknown', [False, True])
def test_owner_extra_tool_is_captured_without_changing_argv(tmp_path, unknown):
    import sys
    policy=locator_policy()
    policy['capture_owner_executables']=True
    policy['owner_locator_slots']['browser']='executable'
    command=policy['stabilization']['frontend']['commands'][0]
    command['owner_extra_tools']=['unknown' if unknown else 'browser']
    launch=freeze_owner_launch(policy,{'attempt':str(tmp_path/'attempt'),'python':sys.executable,'browser':sys.executable})
    if unknown:
        with pytest.raises(ValueError,match='tool'):
            resolve_owner_policy(policy,launch)
    else:
        resolved=resolve_owner_policy(policy,launch)['stabilization']['frontend']['commands'][0]
        assert set(resolved['inputs']) == {'owner-tool:python','owner-tool:browser'}
        assert resolved['argv'][0] == sys.executable
        assert resolved['expected_stdout'] == {'count':7}


@pytest.mark.parametrize('drift', [False, True])
def test_owner_interpreter_identity_matches_collected_environment(tmp_path, drift):
    import sys
    from rc6_tests.test_rc6_execution_platform import tiny_plan,lock
    from openpine.verification.execution_plan import make_plan
    previous,_=tiny_plan(tmp_path)
    policy=locator_policy()
    policy['components']={'tiny':{'dependencies':[],'pythons':[f'{sys.version_info.major}.{sys.version_info.minor}']}}
    policy['owner_environment_slots']={'python':'py'}
    tool=tmp_path/'observed-python'
    tool.write_bytes(Path(sys.executable).read_bytes()+(b'drift' if drift else b''))
    launch=freeze_owner_launch(policy,{'attempt':str(tmp_path/'attempt'),'python':str(tool)})
    nodes=previous['tasks'][0]['nodeids']
    inventories={'tiny@py':{'nodeids':nodes,'reviewed_lock':lock(nodes),'deselected':0,'source_hash':previous['source']['content_hash'],'environment_hash':previous['environments']['py']['identity']['content_hash']}}
    args=dict(profile='component',policy=policy,roots={n:Path(p) for n,p in previous['roots'].items()},source=previous['source'],inventories=inventories,environments=previous['environments'],requested=['tiny'],owner_launch=launch)
    if drift:
        with pytest.raises(ValueError,match='environment'):
            make_plan(**args)
    else:
        assert make_plan(**args)['owner_launch'] == launch


def test_owner_environment_preparation_reuses_existing_verified_restore(tmp_path, monkeypatch):
    import argparse
    from openpine.verification import execution_ci as ci
    from openpine.verification.execution_cli import add_commands
    parser=argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest='command'))
    bundle=tmp_path/'bundle'; work=tmp_path/'owner-env313'
    args=parser.parse_args(['test-ci','owner-environment','--bundle',str(bundle),'--work',str(work)])
    called=[]
    def verified_restore(b,w):
        called.append((b,w))
        return ({},{},w/'venv/bin/python')
    monkeypatch.setattr(ci,'restore',verified_restore)
    result=ci.run_ci_command(args)
    assert called == [(bundle,work)]
    assert result['scope'] == 'owner-environment-preparation'
    assert result['restored'] == str(work/'restored.json')
    assert result['full_stage_accepted'] is False


@pytest.mark.parametrize('foreign', [False,True])
def test_ci_owner_locations_use_checked_reports_without_machine_defaults(tmp_path,foreign):
    import sys
    from openpine.verification.execution_ci import make_owner_locations
    from openpine.verification.execution_identity import source_snapshot
    roots={}
    for name in ('openpine','pine2ast'):
        folder=tmp_path/'source'/name; folder.mkdir(parents=True)
        (folder/'project.py').write_text('VALUE=7\n')
        roots[name]=str(folder)
    source=source_snapshot({n:Path(p) for n,p in roots.items()})
    reports={label:{'candidate_hash':source['content_hash'],'roots':roots,'executable':sys.executable,'source_commits':{'openpine':'a'*40}} for label in ('py311','py312','py313')}
    if foreign:reports['py312']={**reports['py312'],'candidate_hash':'sha256:'+'0'*64}
    kwargs=dict(stack_root=tmp_path/'source',attempt=tmp_path/'frontend',package_attempt=tmp_path/'packages',npm=Path(sys.executable),node=Path(sys.executable),chromium=Path(sys.executable))
    if foreign:
        with pytest.raises(ValueError,match='candidate'):
            make_owner_locations(reports,**kwargs)
    else:
        locations=make_owner_locations(reports,**kwargs)
        assert locations['host']==roots['openpine']
        assert locations['helpers']==str(Path(roots['openpine'])/'scripts/rc6_stabilization')
        assert locations['python312']==sys.executable
        assert locations['uv']==str(Path(sys.executable).parent/'uv')
        assert locations['package_attempt']==str(tmp_path/'packages')
        assert all(Path(v).is_relative_to(tmp_path) for k,v in locations.items() if k not in {'runner','python311','python312','python313','npm','node','chromium','uv'})


def test_package_producer_cli_accepts_checked_binding():
    import subprocess,sys
    script=HOST/'scripts/rc6_stabilization/harness.py'
    result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0
    assert '--binding' in result.stdout


def test_frontend_rejects_source_drift_before_attempt_creation(tmp_path,monkeypatch):
    import importlib.util
    from rc6_tests.test_rc6_execution_platform import tiny_plan
    plan,path=tiny_plan(tmp_path)
    (Path(plan['roots']['tiny'])/'test_a.py').write_text('def test_value(tmp_path):\n    assert False\n')
    monkeypatch.setenv('OPENPINE_ATTEMPT_ROOT',str(tmp_path/'untouched-attempt'))
    spec=importlib.util.spec_from_file_location('frontend_source_guard',HOST/'scripts/rc6_stabilization/frontend_exact.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.HOST=HOST
    monkeypatch.setenv('OPENPINE_PLAN',str(path));monkeypatch.delenv('OPENPINE_BINDING',raising=False)
    with pytest.raises(ValueError,match='relocated source is not the frozen candidate'):
        module.main()
    assert not module.OUT.exists()


def test_producer_preflight_accepts_real_sealed_execution_plan(tmp_path):
    import sys
    from rc6_tests.test_rc6_execution_platform import tiny_plan,lock
    from openpine.verification.execution_plan import make_plan
    from openpine.verification.execution_owner_launch import resolve_producer_policy
    previous,_=tiny_plan(tmp_path)
    policy=locator_policy()
    policy['components']={'tiny':{'dependencies':[],'pythons':[f'{sys.version_info.major}.{sys.version_info.minor}']}}
    launch=freeze_owner_launch(policy,{'attempt':str(tmp_path/'attempt'),'python':sys.executable})
    nodes=previous['tasks'][0]['nodeids']
    inventories={'tiny@py':{'nodeids':nodes,'reviewed_lock':lock(nodes),'deselected':0,'source_hash':previous['source']['content_hash'],'environment_hash':previous['environments']['py']['identity']['content_hash']}}
    plan=make_plan(profile='component',policy=policy,roots={n:Path(p) for n,p in previous['roots'].items()},source=previous['source'],inventories=inventories,environments=previous['environments'],requested=['tiny'],owner_launch=launch)
    resolved=resolve_producer_policy(plan,policy,attempt=tmp_path/'attempt')
    assert resolved['stabilization']['frontend']['commands'][0]['argv'][0] == sys.executable


@pytest.mark.parametrize('case',['new','existing','source','symlink'])
def test_frontend_creates_only_new_safe_external_attempt(tmp_path,monkeypatch,case):
    import importlib.util
    source=tmp_path/'source';source.mkdir()
    output=tmp_path/'attempt'
    if case=='existing':output.mkdir()
    if case=='source':output=source/'attempt'
    if case=='symlink':
        foreign=tmp_path/'foreign';foreign.mkdir()
        link=tmp_path/'link';link.symlink_to(foreign,target_is_directory=True)
        output=link/'attempt'
    monkeypatch.setenv('OPENPINE_ATTEMPT_ROOT',str(output))
    spec=importlib.util.spec_from_file_location('frontend_attempt_guard',HOST/'scripts/rc6_stabilization/frontend_exact.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.OUT == output
    if case=='new':
        module.prepare_attempt({'openpine':str(source)})
        assert module.BASE.is_dir()
    else:
        expected_error = FileExistsError if case == 'existing' else ValueError
        with pytest.raises(expected_error):module.prepare_attempt({'openpine':str(source)})
        assert not (output/'frontend').exists()


def test_frontend_readback_preserves_frozen_command_tool_inputs(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from openpine.verification.execution_identity import hash_file
    import openpine.verification.execution_binding as binding_module
    import openpine.verification.execution_owner_launch as owner_module
    import openpine.verification.execution_process as process_module
    import openpine.verification.execution_campaign as campaign_module
    import openpine.verification.stabilization_evidence as evidence_module
    output=tmp_path/'attempt'
    monkeypatch.setenv('OPENPINE_ATTEMPT_ROOT',str(output))
    monkeypatch.setenv('OPENPINE_NPM',sys.executable)
    monkeypatch.setenv('OPENPINE_PYTHON',sys.executable)
    monkeypatch.setenv('TMPDIR',str(tmp_path))
    from openpine.verification.execution_identity import source_snapshot
    plan_file=tmp_path/'plan.json';plan_file.write_text(__import__('json').dumps({'source':source_snapshot({'openpine':HOST}),'content_hash':'sha256:'+'0'*64}))
    monkeypatch.setenv('OPENPINE_PLAN',str(plan_file))
    spec=importlib.util.spec_from_file_location('frontend_tool_input_execution',HOST/'scripts/rc6_stabilization/frontend_exact.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.HOST=HOST
    commands=module.specifications(attempt_root=output,host_root=HOST,npm=sys.executable,python=sys.executable)
    tool={'path':str(Path(sys.executable).resolve()),'sha256':hash_file(Path(sys.executable).resolve())}
    commands[0]['inputs']={'owner-tool:python':tool}
    helpers={'frontend_exact.py':{'path':str(HOST/'scripts/rc6_stabilization/frontend_exact.py'),'sha256':hash_file(HOST/'scripts/rc6_stabilization/frontend_exact.py')}}
    policy={'stabilization':{'frontend':{'commands':commands},'package_harness_inputs':helpers}}
    monkeypatch.setattr(binding_module,'checked_locations',lambda *args:({'openpine':str(HOST)},{}))
    monkeypatch.setattr(owner_module,'resolve_producer_policy',lambda *args,**kwargs:policy)
    captured=[]
    def stop_after_capture(plan,root,entry,policy):
        captured.append(policy['commands'][0]['inputs']);raise InterruptedError('synthetic readback boundary')
    monkeypatch.setattr(process_module,'run_logged',lambda *args,**kwargs:{'ok':True,'returncode':0})
    monkeypatch.setattr(campaign_module,'descriptor',lambda root,path:{'path':str(path),'sha256':'sha256:'+'0'*64})
    monkeypatch.setattr(evidence_module,'verify_frontend',stop_after_capture)
    with pytest.raises(InterruptedError,match='synthetic readback boundary'):module.main()
    assert captured[0]['owner-tool:python']==tool
    assert captured[0]['frontend_exact.py']==helpers['frontend_exact.py']


def frontend_launch_fixture(tmp_path, monkeypatch, *, host_kind='original', drift=None, nested_attempt=False):
    """Real sealed plan/launch and checked candidate; only stop at command launch."""
    import importlib.util
    import json
    import shutil
    import sys
    from rc6_tests.test_rc6_execution_platform import tiny_plan, lock
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.execution_identity import source_snapshot, hash_file
    from openpine.verification.execution_plan import make_plan
    previous, _ = tiny_plan(tmp_path)
    roots = {n: Path(p) for n, p in previous['roots'].items()}
    source_host = roots['openpine']
    for name in ('scripts/export_openapi.py', 'scripts/generate_openapi_ts.py', 'backend/application.py', 'openpine-ui/src/api/generated/openapi.ts'):
        path = source_host / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# checked candidate input\n')
    output = tmp_path / 'frontend-attempt'
    if nested_attempt:
        output = (source_host if host_kind == 'original' else tmp_path/'staged-host')/'nested-attempt'
    monkeypatch.setenv('OPENPINE_ATTEMPT_ROOT', str(output))
    monkeypatch.setenv('OPENPINE_NPM', sys.executable)
    monkeypatch.setenv('OPENPINE_PYTHON', sys.executable)
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    monkeypatch.delenv('OPENPINE_PROXY', raising=False)
    monkeypatch.delenv('OPENPINE_BINDING', raising=False)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec = importlib.util.spec_from_file_location('frontend_actual_host_guard', HOST/'scripts/rc6_stabilization/frontend_exact.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commands = json.loads((HOST/'verification/execution-policy.json').read_text())['stabilization']['frontend']['commands']
    for command in commands:
        command.pop('owner_extra_tools', None)
    helpers = HOST/'scripts/rc6_stabilization'
    policy = {'schema_id': 'openpine.execution_policy.v2',
              'components': {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']}},
              'owner_locator_slots': {'host': 'directory', 'attempt': 'directory', 'helpers': 'directory', 'npm': 'executable', 'python313': 'executable'},
              'stabilization': {'frontend': {'commands': commands}, 'package_harness_inputs': {
                  'frontend_exact.py': {'path': {'owner_locator': 'helpers', 'relative': 'frontend_exact.py'}, 'sha256': hash_file(helpers/'frontend_exact.py')}}}}
    (source_host/'verification').mkdir(exist_ok=True)
    (source_host/'verification/execution-policy.json').write_text(json.dumps(policy))
    source = source_snapshot(roots)
    host = source_host
    if host_kind != 'original':
        host = tmp_path/'staged-host'
        shutil.copytree(source_host, host)
    launch = freeze_owner_launch(policy, {'host': str(host), 'attempt': str(output), 'helpers': str(helpers), 'npm': sys.executable, 'python313': sys.executable})
    nodes = previous['tasks'][0]['nodeids']
    inventories = {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': source['content_hash'], 'environment_hash': previous['environments']['py']['identity']['content_hash']}}
    plan = make_plan(profile='component', policy=policy, roots=roots, source=source, inventories=inventories, environments=previous['environments'], requested=['tiny'], owner_launch=launch)
    path = tmp_path/'frontend-plan.json'
    path.write_text(json.dumps(plan))
    monkeypatch.setenv('OPENPINE_PLAN', str(path))
    if host_kind == 'binding':
        relocated = {n: tmp_path/'relocated'/n for n in roots}
        for n, root in roots.items():
            shutil.copytree(root, relocated[n])
        binding = make_binding(plan, relocated, {'py': sys.executable})
        binding_path = tmp_path/'binding.json'
        binding_path.write_text(json.dumps(binding))
        monkeypatch.setenv('OPENPINE_BINDING', str(binding_path))
        for root in roots.values():
            shutil.rmtree(root)
    if drift:
        (host/drift).write_text('# foreign unchecked executable/backend\n')
    return module, host


@pytest.mark.parametrize('drift', ['backend/application.py', 'scripts/export_openapi.py', 'scripts/generate_openapi_ts.py'])
def test_frontend_rejects_foreign_host_before_imports_or_attempt(tmp_path, monkeypatch, drift):
    import sys
    import openpine.verification.execution_process as process_module
    module, host = frontend_launch_fixture(tmp_path, monkeypatch, host_kind='staged', drift=drift)
    launched = []
    def forbidden_launch(*args, **kwargs):
        launched.append(args)
        raise RuntimeError('unchecked foreign host reached subprocess boundary')
    monkeypatch.setattr(process_module, 'run_logged', forbidden_launch)
    with pytest.raises(ValueError, match='frontend host is not the frozen OpenPine component'):
        module.main()
    assert not module.OUT.exists()
    assert not launched
    assert str(host) not in sys.path


@pytest.mark.parametrize('host_kind', ['original', 'staged', 'binding'])
def test_frontend_accepts_identical_checked_host_staging(tmp_path, monkeypatch, host_kind):
    import openpine.verification.execution_process as process_module
    module, host = frontend_launch_fixture(tmp_path, monkeypatch, host_kind=host_kind)
    launched = []
    def stop_at_command(argv, **kwargs):
        launched.append((argv, kwargs))
        raise InterruptedError('checked producer command boundary')
    monkeypatch.setattr(process_module, 'run_logged', stop_at_command)
    with pytest.raises(InterruptedError, match='checked producer command boundary'):
        module.main()
    assert module.OUT.is_dir()
    assert launched[0][0][2] == str(host/'scripts/export_openapi.py')
    assert launched[0][1]['cwd'] == host


@pytest.mark.parametrize('host_kind', ['staged', 'binding'])
def test_frontend_rejects_attempt_inside_actual_host(tmp_path, monkeypatch, host_kind):
    import openpine.verification.execution_process as process_module
    from openpine.verification.execution_identity import source_snapshot

    module, host = frontend_launch_fixture(tmp_path, monkeypatch, host_kind=host_kind, nested_attempt=True)
    frozen = source_snapshot({'openpine': host})
    launched = []

    def forbidden_launch(*args, **kwargs):
        launched.append(args)
        raise InterruptedError('overlapping host reached command boundary')

    monkeypatch.setattr(process_module, 'run_logged', forbidden_launch)
    with pytest.raises(ValueError, match='attempt output overlaps source or harness inputs'):
        module.main()
    assert not module.OUT.exists()
    assert not launched
    assert source_snapshot({'openpine': host}) == frozen


HOST = Path(__file__).resolve().parents[1]


def harness():
    return runpy.run_path(str(HOST / 'scripts/rc6_stabilization/harness.py'))


def test_package_producer_allows_new_external_attempt(tmp_path):
    module = harness()
    source = tmp_path / 'sources'
    source.mkdir()
    output = tmp_path / 'attempts' / 'new'
    assert module['validate_attempt_output'](output, {'openpine': source}) == output
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['existing', 'source-child', 'source-parent', 'symlink', 'traversal', 'relative', 'helper-child'])
def test_package_producer_rejects_unsafe_output(tmp_path, mutation):
    module = harness()
    source = tmp_path / 'sources'
    source.mkdir()
    output = tmp_path / 'attempts' / 'new'
    if mutation == 'existing':
        output.mkdir(parents=True)
    elif mutation == 'source-child':
        output = source / 'new'
    elif mutation == 'source-parent':
        output = tmp_path
    elif mutation == 'symlink':
        target = tmp_path / 'target'
        target.mkdir()
        link = tmp_path / 'link'
        link.symlink_to(target, target_is_directory=True)
        output = link / 'new'
    elif mutation == 'traversal':
        output = tmp_path / 'one' / '..' / 'new'
    elif mutation == 'relative':
        output = Path('relative-attempt')
    else:
        output = module['BASE'] / 'new-attempt'
    with pytest.raises((ValueError, FileExistsError)):
        module['validate_attempt_output'](output, {'openpine': source})
    assert not (source / 'new').exists()


def test_owner_launch_binds_declared_tool_aliases_when_reviewed(tmp_path):
    import sys
    from openpine.verification.execution_identity import hash_file
    policy = locator_policy()
    policy['capture_owner_executables'] = True
    policy['bind_owner_tool_aliases'] = True
    policy['owner_locator_slots']['node'] = 'executable'
    policy['owner_tool_dependencies'] = {'python': ['node']}
    python = tmp_path / 'python-alias'
    node = tmp_path / 'node'
    python.symlink_to(Path(sys.executable).resolve())
    node.symlink_to(Path(sys.executable).resolve())
    paths = {'attempt': str(tmp_path / 'attempt'), 'python': str(python), 'node': str(node)}
    launch = freeze_owner_launch(policy, paths)
    spec = resolve_owner_policy(policy, launch)['stabilization']['frontend']['commands'][0]
    for name, primary in [('python', True), ('node', False)]:
        row = spec['inputs']['owner-tool:' + name]
        assert row.get('declared_path') == paths[name]
        assert row.get('is_argv_executable') is primary
        assert row['path'] == str(Path(sys.executable).resolve())
        assert row['sha256'] == hash_file(Path(sys.executable).resolve())
    assert spec['argv'][0] == str(python)


@pytest.mark.parametrize('foreign_argv', [False, True])
def test_logged_bound_primary_alias_checks_actual_argv(tmp_path, foreign_argv):
    import os, sys
    from openpine.verification.execution_identity import hash_file, read_json
    from openpine.verification.execution_process import run_logged
    frozen = tmp_path / 'frozen-tool'
    foreign = tmp_path / 'foreign-tool'
    for path, marker in [(frozen, 'FROZEN'), (foreign, 'FOREIGN')]:
        path.write_text('#!' + sys.executable + '\nprint(' + repr(marker) + ')\n')
        path.chmod(0o700)
    alias = tmp_path / 'declared-tool'
    alias.symlink_to(frozen)
    spec = {'path': str(frozen), 'sha256': hash_file(frozen),
            'declared_path': str(alias), 'is_argv_executable': True}
    argv = [str(foreign if foreign_argv else alias)]
    output = tmp_path / 'command'
    report = run_logged(argv, cwd=tmp_path, output=output, env=dict(os.environ),
                        inputs={'owner-tool:python': spec})
    assert report['argv'] == argv
    assert read_json(output / 'command.json') == report
    assert (output / 'input-0.bin').read_bytes() == frozen.read_bytes()
    if foreign_argv:
        assert report['ok'] is False
        assert report['returncode'] is None
        assert (output / 'stdout.log').read_bytes() == b''
    else:
        assert report['ok'] is True
        assert (output / 'stdout.log').read_text() == 'FROZEN\n'


@pytest.mark.parametrize('invalid', [None, 0, 'true'])
def test_owner_tool_alias_binding_requires_reviewed_boolean(tmp_path, invalid):
    import sys
    policy = locator_policy()
    policy['capture_owner_executables'] = True
    policy['bind_owner_tool_aliases'] = invalid
    with pytest.raises(ValueError, match='alias binding'):
        launch = freeze_owner_launch(policy, {'attempt': str(tmp_path / 'attempt'), 'python': sys.executable})
        resolve_owner_policy(policy, launch)
