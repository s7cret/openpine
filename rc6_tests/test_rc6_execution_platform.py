"""Execution-platform contract tests; synthetic failed suites are negative fixtures.

No Pine expectation, inventory threshold or product skip is changed here.
"""
from __future__ import annotations
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from openpine.verification.execution_identity import environment_snapshot, evidence_path, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import assign_shards, make_plan, select_components, validate_plan
from openpine.verification.execution_campaign import aggregate_campaign, clean_environment, run_campaign, validate_junit
from openpine.verification.identity import read_json, seal
from openpine.verification.pytest_gate import collection_hash, validate_inventory, validate_phase_reports
HOST = Path(__file__).resolve().parents[1]

def reseal(value):
    result = copy.deepcopy(value)
    result.pop('content_hash', None)
    return seal(result)

@pytest.mark.parametrize('mutation', ['wrong-executable', 'traversal', 'source-drift'])
def test_binding_checks_live_locations_before_freezing(tmp_path, mutation):
    from openpine.verification.execution_binding import make_binding

    plan, _ = tiny_plan(tmp_path)
    roots = {name: Path(value) for name, value in plan['roots'].items()}
    executable = sys.executable
    if mutation == 'wrong-executable':
        executable = str(tmp_path / 'impostor')
        Path(executable).write_text('#!/bin/sh\nexit 0\n')
        Path(executable).chmod(0o755)
    elif mutation == 'traversal':
        roots['tiny'] = roots['tiny'] / '..' / 'tiny'
    else:
        (roots['tiny'] / 'test_a.py').write_text('changed = True\n')
    with pytest.raises(ValueError):
        make_binding(plan, roots, {'py': executable})


def test_binding_structural_replay_does_not_require_historical_locations(tmp_path):
    from openpine.verification.execution_binding import make_binding, validate_binding
    import shutil

    plan, _ = tiny_plan(tmp_path)
    roots = {name: Path(value) for name, value in plan['roots'].items()}
    binding = make_binding(plan, roots, {'py': sys.executable})
    for root in roots.values():
        shutil.rmtree(root)
    assert validate_binding(plan, binding) == binding


def test_binding_rejects_resealed_authority_override(tmp_path):
    from openpine.verification.execution_binding import make_binding, validate_binding

    plan, _ = tiny_plan(tmp_path)
    binding = make_binding(plan, {name: Path(value) for name, value in plan['roots'].items()}, {'py': sys.executable})
    binding['expected_stdout'] = {'passed': True}
    with pytest.raises(ValueError, match='fields'):
        validate_binding(plan, reseal(binding))


def test_binding_rejects_source_symlink_ancestor(tmp_path):
    from openpine.verification.execution_binding import make_binding
    plan, _ = tiny_plan(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    roots = {name: Path(value) for name, value in plan['roots'].items()}
    roots['tiny'] = alias / 'tiny'
    with pytest.raises(ValueError, match='symlink'):
        make_binding(plan, roots, {'py': sys.executable})


def test_two_launch_roots_and_third_archive_replay_preserve_execution_binding(tmp_path):
    """Actual tiny pytest launches exercise transport, not seven-owner product proof."""
    import shutil
    from openpine.verification.execution_binding import make_binding, checked_locations
    from openpine.verification.execution_ci import create_source_archive, unpack_source_archive

    plan, path = tiny_plan(tmp_path)
    roots = {name: Path(value) for name, value in plan['roots'].items()}
    archive = tmp_path / 'sources.tar.gz'
    create_source_archive(roots, archive)
    campaigns = []
    for index in range(2):
        launch_roots = unpack_source_archive(archive, tmp_path / f'launch-{index}', plan['source'])
        binding = make_binding(plan, launch_roots, {'py': sys.executable})
        output = tmp_path / f'campaign-{index}'
        run_campaign(plan, path, output, binding=binding, run_id=f'launch-{index}')
        report = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id=f'launch-{index}')
        assert report['ok'], report
        campaigns.append((output, report, (output / 'binding.json').read_bytes()))
        shutil.rmtree(tmp_path / f'launch-{index}')
    replay_roots = unpack_source_archive(archive, tmp_path / 'replay-third', plan['source'])
    replay_binding = make_binding(plan, replay_roots, {'py': sys.executable})
    assert checked_locations(plan, replay_binding)[0] == {n: str(p) for n, p in replay_roots.items()}
    for index, (output, report, execution_binding) in enumerate(campaigns):
        moved = tmp_path / f'archived-{index}'
        shutil.copytree(output, moved)
        shutil.rmtree(output)
        assert aggregate_campaign(plan, moved, expected_plan_hash=plan['content_hash'], expected_run_id=f'launch-{index}') == report
        assert (moved / 'binding.json').read_bytes() == execution_binding


def test_campaign_rechecks_resealed_live_binding_before_launch(tmp_path):
    from openpine.verification.execution_binding import make_binding
    plan, path = tiny_plan(tmp_path)
    roots = {name: Path(value) for name, value in plan['roots'].items()}
    binding = make_binding(plan, roots, {'py': sys.executable})
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    binding['roots']['tiny'] = str(alias / 'tiny')
    with pytest.raises(ValueError, match='symlink'):
        run_campaign(plan, path, tmp_path / 'refused', binding=reseal(binding))
    assert not (tmp_path / 'refused').exists()


def phases():
    return [{'when': phase, 'outcome': 'passed', 'xfail': False, 'duration': 0.001} for phase in ('setup', 'call', 'teardown')]

def lock(nodes):
    return {'count': len(nodes), 'sha256': collection_hash(nodes), 'deselected': 0}

def tiny_plan(tmp_path, *, bodies=None, shards=2, timeout=30, profile='component'):
    root = tmp_path / 'tiny'
    root.mkdir()
    bodies = bodies or {'test_a.py': 'def test_value(tmp_path):\n    assert 2 + 2 == 4\n', 'test_b.py': 'def test_value(tmp_path):\n    assert sorted([3, 1]) == [1, 3]\n'}
    for filename, content in bodies.items():
        (root / filename).write_text(content)
    nodes = sorted((filename + '::test_value' for filename in bodies))
    import shutil
    platform_root = tmp_path / 'platform-source'
    package = platform_root / 'openpine'
    package.mkdir(parents=True)
    shutil.copy2(HOST / 'openpine/__init__.py', package / '__init__.py')
    shutil.copytree(HOST / 'openpine/verification', package / 'verification', ignore=shutil.ignore_patterns('__pycache__'))
    roots = {'openpine': platform_root, 'tiny': root}
    source = source_snapshot(roots)
    environment = environment_snapshot()
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}'], 'smoke': ['test_a.py::test_value'], 'timeout_seconds': timeout}}, 'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']}}
    inventories = {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'deselected': 0, 'source_hash': source['content_hash'], 'environment_hash': environment['content_hash']}}
    plan = make_plan(profile=profile, policy=policy, roots=roots, source=source, inventories=inventories, environments={'py': {'identity': environment, 'executable': sys.executable}}, requested=['tiny'], shard_count=shards)
    path = tmp_path / 'plan.json'
    write_once_json(path, plan)
    return (plan, path)

@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unexpected', 'teardown', 'xfail', 'duration'])
def test_phase_mutations_rejected(mutation):
    nodes = ['test_a.py::test_value']
    reports = {nodes[0]: phases()}
    assert validate_phase_reports(nodes, reports) == []
    if mutation == 'missing':
        reports[nodes[0]].pop()
    elif mutation == 'duplicate':
        reports[nodes[0]].append(phases()[0])
    elif mutation == 'unexpected':
        reports['test_b.py::test_value'] = phases()
    elif mutation == 'teardown':
        reports[nodes[0]][2]['outcome'] = 'failed'
    elif mutation == 'xfail':
        reports[nodes[0]][1]['xfail'] = True
    else:
        reports[nodes[0]][0]['duration'] = float('nan')
    assert validate_phase_reports(nodes, reports)

@pytest.mark.parametrize('field,value', [('count', True), ('deselected', False), ('count', 2)])
def test_inventory_does_not_accept_bool_counts_or_changed_denominator(field, value):
    nodes = ['test_a.py::test_value']
    expected = lock(nodes)
    expected[field] = value
    with pytest.raises(ValueError):
        validate_inventory(nodes, expected, 0)

def test_sharding_deterministic_groups_files_and_preserves_every_node():
    nodes = [f'test_{file}.py::test_{index}' for file in range(7) for index in range(4)]
    durations = {node: 10.0 if 'test_2.py' in node else 0.1 for node in nodes}
    before = copy.deepcopy(nodes)
    first = assign_shards(nodes, 3, durations)
    assert first == assign_shards(list(reversed(nodes)), 3, durations)
    assert nodes == before
    assert sorted((n for shard in first for n in shard['nodeids'])) == nodes
    placements = {}
    for shard in first:
        for node in shard['nodeids']:
            file = node.split('::')[0]
            assert placements.setdefault(file, shard['id']) == shard['id']
    assert len(first) == 3

@pytest.mark.parametrize('nodes', [[], ['test.py::a', 'test.py::a'], ['../x.py::a'], ['test.py::a\n--deselect=x'], ['test.py::a\r'], ['-x.py::a'], ['@selectors.py::a'], ['tests\\bad.py::a']])
def test_sharding_rejects_ambiguous_selectors(nodes):
    with pytest.raises(ValueError):
        assign_shards(nodes, 2)

def test_affected_graph_escalation_and_cycle_rejection(tmp_path):
    policy = {'components': {'a': {'dependencies': []}, 'b': {'dependencies': ['a']}, 'c': {'dependencies': ['b']}, 'd': {'dependencies': []}, 'host': {'dependencies': ['a', 'b', 'c', 'd']}}, 'critical_paths': ['schemas/*']}
    selected, _ = select_components(policy, 'affected', [], ['a/source.py'])
    assert selected == ['a', 'b', 'c', 'host']
    # b's full tests plus its consumers are required, but unrelated host
    # prerequisites a/d are environment preparation, not test obligations.
    assert select_components(policy, 'affected', [], ['b/source.py'])[0] == ['b', 'c', 'host']
    assert select_components(policy, 'affected', ['host'])[0] == ['host']
    for change in ('a/schemas/types.json', 'unknown/file', 'invalid'):
        assert select_components(policy, 'affected', [], [change])[0] == ['a', 'b', 'c', 'd', 'host']
    policy['components']['a']['dependencies'] = ['c']
    with pytest.raises(ValueError, match='cyclic'):
        select_components(policy, 'affected', ['a'])

    roots = {}
    for component in ('a', 'b', 'c'):
        root = tmp_path / component
        root.mkdir()
        (root / 'source.py').write_text(f'COMPONENT = {component!r}\n')
        roots[component] = root
    source = source_snapshot(roots)
    environment = environment_snapshot()
    nodes = ['test_contract.py::test_value']
    policy = {
        'components': {
            'a': {'dependencies': [], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']},
            'b': {'dependencies': ['a'], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']},
            'c': {'dependencies': ['b'], 'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}']},
        },
        'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']},
    }
    inventories = {
        f'{component}@py': {
            'nodeids': nodes,
            'reviewed_lock': lock(nodes),
            'source_hash': source['content_hash'],
            'environment_hash': environment['content_hash'],
        }
        for component in ('b', 'c')
    }
    plan = make_plan(
        profile='affected',
        policy=policy,
        roots=roots,
        source=source,
        inventories=inventories,
        environments={'py': {'identity': environment, 'executable': sys.executable}},
        changes=['b/source.py'],
    )
    assert [task['component'] for task in plan['tasks']] == ['b', 'c']
    assert plan['preparation_components'] == ['a']


def test_snapshot_includes_tests_expected_and_verification_but_not_generated_cache(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'source.py').write_text('x = 1\n')
    (root / 'verification').mkdir()
    (root / 'verification/expected.json').write_text('{"value":1}\n')
    before = source_snapshot({'tiny': root})
    (root / '__pycache__').mkdir()
    (root / '__pycache__/ignored.pyc').write_bytes(b'generated')
    assert source_snapshot({'tiny': root}) == before
    (root / 'verification/expected.json').write_text('{"value":2}\n')
    assert source_snapshot({'tiny': root})['content_hash'] != before['content_hash']
    (root / 'tests').mkdir()
    (root / 'tests/test_x.py').write_text('assert 1\n')
    assert 'tests/test_x.py' in source_snapshot({'tiny': root})['components']['tiny']['files']
    (root / 'escape').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='symlink'):
        source_snapshot({'tiny': root})

@pytest.mark.parametrize('relative', ['../x', '/x', 'x/../y', 'x//y', './x', 'C:/x', 'x\\y'])
def test_evidence_paths_reject_escape(tmp_path, relative):
    with pytest.raises(ValueError):
        evidence_path(tmp_path, relative, must_exist=False)

def test_evidence_is_write_once_and_rejects_symlink(tmp_path):
    output = tmp_path / 'receipt.json'
    write_once_json(output, {'value': 1})
    with pytest.raises(ValueError):
        write_once_json(output, {'value': 2})
    assert read_json(output) == {'value': 1}
    (tmp_path / 'alias').symlink_to(output)
    with pytest.raises(ValueError, match='symlink'):
        evidence_path(tmp_path, 'alias')

def test_gate_rejects_bad_inventory_before_test_body(tmp_path):
    root = tmp_path / 'tests'
    root.mkdir()
    (root / 'test_x.py').write_text('from pathlib import Path\ndef test_value():\n    Path("body-ran").write_text("unsafe")\n')
    expected = lock(['test_x.py::test_value'])
    expected['count'] = 2
    write_once_json(tmp_path / 'lock.json', {'tiny': expected})
    output = tmp_path / 'receipt.json'
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'openpine.verification.pytest_gate', '--verification-suite=tiny', '--verification-lock=' + str(tmp_path / 'lock.json'), '--verification-output=' + str(output)], cwd=root, env=clean_environment({'openpine': str(HOST)}, tmp_path / 'private'), text=True, capture_output=True, timeout=30)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
    assert result.returncode != 0
    assert not (root / 'body-ran').exists(), result.stdout + result.stderr
    assert read_json(output)['ok'] is False

def test_plan_mutations_require_anchor_and_complete_assignment(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    validate_plan(plan, expected_hash=plan['content_hash'])
    changed = copy.deepcopy(plan)
    changed['tasks'][0]['shards'].pop()
    changed = reseal(changed)
    with pytest.raises(ValueError, match='identity'):
        validate_plan(changed, expected_hash=plan['content_hash'])
    with pytest.raises(ValueError):
        validate_plan(changed)
    duplicate = copy.deepcopy(plan)
    duplicate['tasks'][0]['shards'].append(copy.deepcopy(duplicate['tasks'][0]['shards'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        validate_plan(reseal(duplicate))

@pytest.fixture(scope='module')
def valid_campaign(tmp_path_factory):
    temporary = tmp_path_factory.mktemp('real-shards')
    plan, path = tiny_plan(temporary)
    run = run_campaign(plan, path, temporary / 'evidence', jobs=2, run_id='test-campaign')
    report = aggregate_campaign(plan, temporary / 'evidence', expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    return (plan, temporary / 'evidence', run, report)

def test_real_parallel_shards_have_exact_phases_junit_and_scoped_acceptance(valid_campaign):
    plan, root, run, report = valid_campaign
    assert report['ok'], json.dumps(report, indent=2)
    assert report['executed_obligations'] == 2
    assert report['required_shards'] == 2
    assert not report['full_stage_accepted'] and (not report['full_release_accepted'])
    for attempt in run['attempts']:
        phases_receipt = read_json(root / attempt['artifacts']['phases']['path'])
        assert phases_receipt['source_before'] == plan['source']['content_hash']
        assert len(phases_receipt['reports']) == 1
        assert phases_receipt['timing']['fixtures']

@pytest.mark.parametrize('mutation', ['missing-shard', 'duplicate-shard', 'wrong-run', 'wrong-env', 'missing-teardown', 'xml-tamper', 'empty-xml', 'collect-only', 'stdout-tamper', 'nonzero-exit'])
def test_aggregate_rejects_mutated_real_evidence(valid_campaign, tmp_path, mutation):
    import shutil
    plan, original, run, initial = valid_campaign
    assert initial['ok'], json.dumps(initial, indent=2)
    root = tmp_path / 'copied'
    shutil.copytree(original, root)
    current = copy.deepcopy(run)
    attempt = current['attempts'][0]
    if mutation == 'missing-shard':
        current['attempts'].pop()
    elif mutation == 'duplicate-shard':
        current['attempts'].append(copy.deepcopy(attempt))
    elif mutation == 'wrong-run':
        current['run_id'] = 'other'
    elif mutation == 'nonzero-exit':
        attempt['returncode'] = 1
    elif mutation in {'wrong-env', 'missing-teardown', 'collect-only'}:
        desc = attempt['artifacts']['phases']
        path = root / desc['path']
        value = read_json(path)
        if mutation == 'wrong-env':
            value['environment_hash'] = 'sha256:' + '0' * 64
        elif mutation == 'missing-teardown':
            next(iter(value['reports'].values())).pop()
        else:
            value['collect_only'] = True
        path.write_text(json.dumps(reseal(value)))
        desc['sha256'] = hash_file(path)
    else:
        desc = attempt['artifacts']['stdout' if mutation == 'stdout-tamper' else 'junit']
        path = root / desc['path']
        path.write_text('' if mutation == 'empty-xml' else '<broken>')
        if mutation == 'empty-xml':
            desc['sha256'] = hash_file(path)
    (root / 'run.json').write_text(json.dumps(reseal(current)))
    result = aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert result['ok'] is False and result['errors']

@pytest.mark.parametrize('body', ['import pytest\n@pytest.fixture(autouse=True)\ndef fail():\n    raise ValueError("setup")\ndef test_value():\n    assert True\n', 'import pytest\n@pytest.fixture(autouse=True)\ndef fail():\n    yield\n    raise ValueError("teardown")\ndef test_value():\n    assert True\n', 'import pytest\ndef test_value():\n    pytest.skip("synthetic verifier negative case")\n', 'import os\ndef test_value():\n    os._exit(9)\n'])
def test_real_unsuccessful_shards_never_pass(tmp_path, body):
    plan, path = tiny_plan(tmp_path, bodies={'test_bad.py': body}, shards=1)
    run = run_campaign(plan, path, tmp_path / 'evidence', run_id='negative')
    result = aggregate_campaign(plan, tmp_path / 'evidence', expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert not result['ok'] and result['errors']

def test_changed_source_rejects_campaign_before_execution(tmp_path):
    plan, path = tiny_plan(tmp_path)
    (tmp_path / 'tiny/test_a.py').write_text('def test_value():\n    assert False\n')
    with pytest.raises(ValueError, match='candidate changed'):
        run_campaign(plan, path, tmp_path / 'evidence')
    assert not (tmp_path / 'evidence').exists()

def test_stage_profile_cannot_be_accepted_as_pytest_only(tmp_path):
    plan, path = tiny_plan(tmp_path, shards=1, profile='stage-full')
    run = run_campaign(plan, path, tmp_path / 'evidence', run_id='not-stage-acceptance')
    result = aggregate_campaign(plan, tmp_path / 'evidence', expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    assert result['pytest_scope_passed'], result
    assert result['owner_gates_required_separately'] == ['foundation']
    assert not result['ok'] and (not result['full_stage_accepted'])

@pytest.mark.parametrize('xml', ['', '<broken', '<testsuites/>', '<!DOCTYPE a><testsuites/>'])
def test_invalid_empty_xml_does_not_prove_execution(tmp_path, xml):
    path = tmp_path / 'junit.xml'
    path.write_text(xml)
    with pytest.raises(ValueError):
        validate_junit(path, ['test.py::test_value'])

def test_full_suite_export_retains_inventory_and_does_not_accept_stage(valid_campaign, tmp_path):
    from openpine.verification.execution_campaign import export_suite_receipts
    plan, evidence, run, _ = valid_campaign
    result = export_suite_receipts(plan, evidence, tmp_path / 'export', expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])
    receipt = read_json(tmp_path / 'export/py/tiny.inventory.json')
    validate_inventory(receipt['nodeids'], lock(plan['tasks'][0]['nodeids']), 0)
    assert result['ok'] and receipt['ok'] and (not result['full_stage_accepted'])
    with pytest.raises(ValueError, match='exists'):
        export_suite_receipts(plan, evidence, tmp_path / 'export', expected_plan_hash=plan['content_hash'], expected_run_id=run['run_id'])

def test_actual_timeout_is_reported_and_not_replaced_by_a_passing_receipt(tmp_path):
    body = 'import threading\ndef test_value():\n    threading.Event().wait(60)\n'
    plan, path = tiny_plan(tmp_path, bodies={'test_timeout.py': body}, shards=1, timeout=2)
    run = run_campaign(plan, path, tmp_path / 'evidence', run_id='timeout')
    assert run['attempts'][0]['status'] == 'timeout'
    report = aggregate_campaign(plan, tmp_path / 'evidence', expected_plan_hash=plan['content_hash'], expected_run_id='timeout')
    assert not report['ok'] and report['executed_obligations'] == 0

def test_nested_output_and_over_budget_parallelism_are_rejected(tmp_path):
    plan, path = tiny_plan(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        run_campaign(plan, path, tmp_path / 'tiny/evidence')
    with pytest.raises(ValueError, match='CPU'):
        run_campaign(plan, path, tmp_path / 'evidence', jobs=10000)

def test_full_profile_does_not_drop_missing_python(tmp_path):
    plan, path = tiny_plan(tmp_path)
    policy = {'components': {'tiny': {'dependencies': [], 'pythons': ['3.11', '3.12', '3.13']}}, 'required_gates': {'stage-full': ['foundation']}}
    task = plan['tasks'][0]
    inv = {'tiny@py': {'nodeids': task['nodeids'], 'reviewed_lock': lock(task['nodeids']), 'source_hash': plan['source']['content_hash'], 'environment_hash': plan['environments']['py']['identity']['content_hash']}}
    with pytest.raises(ValueError, match='mandatory interpreter missing'):
        make_plan(profile='stage-full', policy=policy, roots={n: Path(p) for n, p in plan['roots'].items()}, source=plan['source'], inventories=inv, environments=plan['environments'])

def test_normal_verification_cli_does_not_eagerly_import_pytest(tmp_path):
    code = "import sys, importlib.abc\nsys.path.insert(0, sys.argv[1])\nclass NoPytest(importlib.abc.MetaPathFinder):\n    def find_spec(self, fullname, path=None, target=None):\n        if fullname == 'pytest' or fullname.startswith('pytest.'):\n            raise ImportError('pytest is not installed in this production-shaped probe')\nsys.meta_path.insert(0, NoPytest())\nfrom openpine.verification.__main__ import main\nassert 'pytest' not in sys.modules\nmain(['--help'])\n"
    result = subprocess.run([sys.executable, '-I', '-c', code, str(HOST)], text=True, capture_output=True, timeout=20)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'test-plan' in result.stdout

def test_resource_and_selector_policy_validation(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    for field, value in [('cpu_slots', True), ('timeout_seconds', 0), ('exclusive_group', []), ('plugins', ['../../unexpected'])]:
        changed = copy.deepcopy(plan)
        changed['tasks'][0][field] = value
        with pytest.raises(ValueError):
            validate_plan(reseal(changed))

    root = tmp_path / 'smoke-tiny'
    root.mkdir()
    (root / 'source.py').write_text('VALUE = 1\n')
    source = source_snapshot({'tiny': root})
    environment = environment_snapshot()
    nodes = ['test_a.py::test_value', 'test_b.py::test_value']
    smoke_policy = {
        'components': {
            'tiny': {
                'dependencies': [],
                'pythons': [f'{sys.version_info.major}.{sys.version_info.minor}'],
                'smoke': ['test_*.py::*'],
            }
        },
        'required_gates': {'stage-full': ['foundation'], 'release-full': ['release-owner']},
    }
    inventory = {
        'tiny@py': {
            'nodeids': nodes,
            'reviewed_lock': lock(nodes),
            'source_hash': source['content_hash'],
            'environment_hash': environment['content_hash'],
        }
    }
    with pytest.raises(ValueError, match='explicit node ID'):
        make_plan(
            profile='smoke',
            policy=smoke_policy,
            roots={'tiny': root},
            source=source,
            inventories=inventory,
            environments={'py': {'identity': environment, 'executable': sys.executable}},
            requested=['tiny'],
        )

    release_policy = json.loads((HOST / 'verification/execution-policy.json').read_text())
    expected = {
        'openpine-contracts': ['tests/test_admit.py::test_admit_ok'],
        'pine2ast': [
            'tests/coverage/test_contracts_and_reference.py::test_generated_public_contracts_validate_and_round_trip'
        ],
        'pinelib': ['tests/test_abi_surface_invariants.py::test_runtime_value_abi_exposes_injected_bar_context_and_metadata_exactly'],
        'ast2python': [
            'tests/pass3/test_release_candidate.py::test_workflow_pin_gate'
        ],
        'backtest_engine': ['tests/unit/test_contracts_pin.py::test_contracts_pin_and_catalog'],
        'marketdata-provider': ['tests/test_smoke.py::test_runtime_contract_version'],
        'optimizer': ['tests/unit/test_contracts_pin.py::test_contracts_catalog'],
        'openpine': [
            'rc6_tests/test_rc6_execution_platform.py::test_affected_graph_escalation_and_cycle_rejection'
        ],
    }
    assert {name: settings['smoke'] for name, settings in release_policy['components'].items()} == expected

def test_smoke_exact_collected_parameter_ids(tmp_path):
    root = tmp_path / 'exact-smoke'
    root.mkdir()
    (root / 'pytest.ini').write_text('[pytest]\ndisable_test_id_escaping_and_forfeit_all_rights_to_community_support = True\n')
    (root / 'test_exact.py').write_text(
        'import pytest\n'
        '@pytest.mark.parametrize("value", [1]*7, ids=["type-parameters", "звезда*", "question?", "bracket[x]", "slash/../x", "-k option", "back\\\\slash"])\n'
        'def test_value(value): assert value == 1\n'
        'def test_plain(): assert True\n'
    )
    collected = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q'], cwd=root, text=True, capture_output=True, check=True)
    nodes = sorted(line for line in collected.stdout.splitlines() if line.startswith('test_exact.py::'))
    assert len(nodes) == 8
    source = source_snapshot({'tiny': root})
    env = environment_snapshot()
    inventory = {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'source_hash': source['content_hash'], 'environment_hash': env['content_hash']}}
    for node in nodes:
        policy = {'components': {'tiny': {'smoke': [node]}}}
        plan = make_plan(profile='smoke', policy=policy, roots={'tiny': root}, source=source, inventories=inventory, environments={'py': {'identity': env, 'executable': sys.executable}}, requested=['tiny'])
        assert plan['tasks'][0]['nodeids'] == [node]
        assert plan['tasks'][0]['shards'][0]['nodeids'] == [node]
        executed = subprocess.run([sys.executable, '-m', 'pytest', '-q', node], cwd=root, text=True, capture_output=True, check=True)
        assert '1 passed' in executed.stdout


@pytest.mark.parametrize('selector', ['test_*.py::test_value', 'tests//test_a.py::test_value', './test_a.py::test_value', 'C:/test_a.py::test_value', 'test_a.txt::test_value', 'test_a.py::'])
def test_sharding_rejects_unsafe_frozen_file_selectors(selector):
    with pytest.raises(ValueError, match='node ID'):
        assign_shards([selector], 1)


@pytest.mark.parametrize('selector', ['test_a.py::test_missing', 'test_*.py::*', '--maxfail=0', '../test_a.py::test_value', '/test_a.py::test_value', 'test_a.py::', 'test_a.py/../test_b.py::test_value', 'test_a.py::test_value\n--help', 'test_a.py::test_value\x00', 'duplicate'])
def test_smoke_rejects_unknown_duplicate_and_unsafe_selectors(tmp_path, selector):
    plan, _ = tiny_plan(tmp_path)
    nodes = plan['tasks'][0]['nodeids']
    smoke = [nodes[0], nodes[0]] if selector == 'duplicate' else [selector]
    policy = {'components': {'tiny': {'smoke': smoke}}}
    inventory = {'tiny@py': {'nodeids': nodes, 'reviewed_lock': lock(nodes), 'source_hash': plan['source']['content_hash'], 'environment_hash': plan['environments']['py']['identity']['content_hash']}}
    with pytest.raises(ValueError):
        make_plan(profile='smoke', policy=policy, roots={n: Path(p) for n,p in plan['roots'].items()}, source=plan['source'], inventories=inventory, environments=plan['environments'], requested=['tiny'])


def test_sharding_preserves_escaped_unicode_parameter_ids():
    nodes = ['tests/test_x.py::test_value[\\u0422\\u0435\\u0441\\u0442]', 'tests/test_y.py::test_value[line\\nnext]']
    shards = assign_shards(nodes, 2)
    assert sorted((node for shard in shards for node in shard['nodeids'])) == nodes
