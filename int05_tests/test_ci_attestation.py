"""Targeted source-attestation/launch contract; fixture is not product qualification."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_ci import COMPONENTS, attest_ci_source_commits, bundle_manifest, create_source_archive, make_ci_plan
from openpine.verification.execution_identity import environment_snapshot, source_snapshot, write_once_json
from openpine.verification.identity import digest, read_json, seal
from int05_tests.affected_from_ci import affected_from_ci
from int05_tests.check_full_comparison import check_pair
from rc6_tests.test_rc6_execution_platform import HOST, lock


def test_ci_owner_attestation_reaches_real_openpine_shard_with_exact_producers(tmp_path, monkeypatch):
    git_exe = shutil.which('git')
    assert git_exe is not None

    def git(root, *args):
        return subprocess.run([git_exe, '-C', str(root), *args], capture_output=True,  # noqa: S603 -- fixed Git executable and local fixture argv
            text=True, check=True).stdout.strip()

    roots, commits = {}, {}
    for component in COMPONENTS:
        root = tmp_path / 'stack' / component
        root.mkdir(parents=True)
        roots[component] = root
        if component != 'openpine':
            (root / 'test_value.py').write_text('def test_value():\n    assert True\n')
            git(root, 'init', '-q')
            git(root, '-c', 'user.name=INT05 fixture', '-c', 'user.email=int05@example.invalid',
                'add', 'test_value.py')
            git(root, '-c', 'user.name=INT05 fixture', '-c', 'user.email=int05@example.invalid',
                'commit', '-qm', component)
            commits[component] = git(root, 'rev-parse', 'HEAD')
    root = roots['openpine']
    package = root / 'openpine'
    package.mkdir()
    for filename in ('__init__.py', 'build_identity.py'):
        shutil.copy2(HOST / 'openpine' / filename, package / filename)
    shutil.copytree(HOST / 'openpine/verification', package / 'verification',
                    ignore=shutil.ignore_patterns('__pycache__'))
    expected_producers = {n: commits[n] for n in ('pine2ast', 'ast2python', 'pinelib', 'openpine-contracts')}
    (root / 'test_value.py').write_text(
        'import json, os\nfrom pathlib import Path\n'
        'from openpine.build_identity import compiler_producer_commits, current_build_identity\n'
        'def test_value():\n'
        f'    assert compiler_producer_commits() == {expected_producers!r}\n'
        '    evidence = Path(os.environ["OPENPINE_STAGE1_EVIDENCE"])\n'
        '    evidence.mkdir()\n'
        '    (evidence / "producer-observation.json").write_text(json.dumps({'
        '"commits":compiler_producer_commits(),"host":current_build_identity().commit}))\n'
    )
    (root / 'pyproject.toml').write_text('[tool.coverage.run]\nsource=["openpine"]\n')
    policy = {'components': {n: {} for n in COMPONENTS}, 'required_gates': {'stage-full': ['foundation']}}
    write_once_json(root / 'verification/execution-policy.json', policy)
    git(root, 'init', '-q')
    git(root, 'add', '.')
    git(root, '-c', 'user.name=INT05 fixture', '-c', 'user.email=int05@example.invalid',
        'commit', '-qm', 'openpine attestation fixture')
    commits['openpine'] = git(root, 'rev-parse', 'HEAD')
    attested = attest_ci_source_commits([{'host_commit': commits['openpine'],
                                       'source_pins': {n: commits[n] for n in COMPONENTS if n != 'openpine'}}])
    assert attested == commits
    source, environment = source_snapshot(roots), environment_snapshot()
    nodes = ['test_value.py::test_value']
    collection = seal({'schema_id': 'openpine.test_collection_set.v1',
        'source': source, 'roots': {n: str(p) for n, p in roots.items()},
        'policy_hash': digest(policy), 'environments': {'py313': {'identity': environment,
        'executable': sys.executable}}, 'inventories': {n + '@py313': {
            'nodeids': nodes, 'node_markers': {nodes[0]: []}, 'reviewed_lock': lock(nodes),
            'source_hash': source['content_hash'], 'environment_hash': environment['content_hash'],
            'deselected': 0} for n in COMPONENTS},
        'ok': True, 'errors': [], 'collect_only': True, 'execution_pass': False})
    collection_path = tmp_path / 'collection.json'
    write_once_json(collection_path, collection)
    plan_path = tmp_path / 'attested-full.json'
    plan = make_ci_plan([collection_path], roots, plan_path, attested)
    assert plan['source_commits'] == attested
    bundle = tmp_path / 'source-attestation-fixture'
    bundle.mkdir()
    shutil.copy2(collection_path, bundle / 'collection.json')
    create_source_archive(roots, bundle / 'sources.tar.gz')
    prepared = bundle_manifest(bundle, source=source, environment=environment,
        source_pins={n: commits[n] for n in COMPONENTS if n != 'openpine'}, host_commit=commits['openpine'])
    affected = affected_from_ci(bundle, plan, expected_bundle_hash=prepared['content_hash'],
        expected_full_hash=plan['content_hash'], changes=['openpine/test_value.py'],
        expected_owners=['openpine'], output=tmp_path / 'attested-affected.json')
    assert affected['source_commits'] == attested
    assert affected['source'] == plan['source']
    assert [t['component'] for t in affected['tasks']] == ['openpine']
    planning_only = [seal({k: v for k, v in item.items() if k not in {'source_commits', 'content_hash'}})
                     for item in (affected, plan)]
    with pytest.raises(ValueError, match='exact plan producer commits are required'):
        check_pair(policy, *planning_only, affected_evidence=tmp_path / 'absent-affected',
            full_evidence=tmp_path / 'absent-full', affected_hash=planning_only[0]['content_hash'],
            full_hash=planning_only[1]['content_hash'], affected_run='unattested', full_run='unattested',
            expected_owners=['openpine'])
    monkeypatch.setenv('OPENPINE_PRODUCER_COMMITS_JSON', '{"stale":"value"}')
    selected = [('openpine@py313', 's000')]
    output = tmp_path / 'actual-shard'
    run_campaign(plan, plan_path, output, jobs=1, run_id='attestation-proof',
                 shard_keys=selected, build_commit=attested['openpine'])
    report = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'],
                                expected_run_id='attestation-proof', expected_shards=selected)
    assert report['pytest_scope_passed'], report
    observation = read_json(output / 'openpine@py313/s000/a001/owner-evidence/producer-observation.json')
    assert observation == {'commits': expected_producers, 'host': attested['openpine']}
    assert report['is_fragment'] and not report['full_stage_accepted']
    # test-run invokes this same existing owner without an explicit build_commit.
    # The restored Git identity must therefore supply the attested host commit.
    affected_output = tmp_path / 'actual-affected-shard'
    run_campaign(affected, tmp_path / 'attested-affected.json', affected_output,
                 jobs=1, run_id='affected-attestation-proof')
    affected_report = aggregate_campaign(affected, affected_output,
        expected_plan_hash=affected['content_hash'], expected_run_id='affected-attestation-proof')
    assert affected_report['pytest_scope_passed'], affected_report
    observation = read_json(affected_output / 'openpine@py313/s000/a001/owner-evidence/producer-observation.json')
    assert observation == {'commits': expected_producers, 'host': attested['openpine']}
