"""Negative verifier tests only; the two-owner fixture is not qualification."""
from pathlib import Path

import pytest

from int05_tests.check_full_comparison import check_pair
from openpine.verification.execution_campaign import run_campaign
from openpine.verification.execution_identity import source_snapshot, write_once_json
from openpine.verification.execution_plan import make_plan
from rc6_tests.test_rc6_execution_platform import lock, tiny_plan


@pytest.mark.parametrize('omitted_fails', [False, True])
def test_full_failure_outside_affected_scope_blocks_trust(tmp_path, omitted_fails):
    seed, _ = tiny_plan(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'test_other.py').write_text('def test_value():\n    assert ' + str(not omitted_fails) + '\n')
    roots = {n: Path(p) for n, p in seed['roots'].items()} | {'other': other}
    source = source_snapshot(roots)
    environments = seed['environments']
    policy = {'components': {'tiny': {}, 'other': {}},
              'required_gates': {'stage-full': ['foundation']}}
    inventories = {}
    for owner, nodes in [('tiny', seed['tasks'][0]['nodeids']), ('other', ['test_other.py::test_value'])]:
        inventories[owner + '@py'] = {
            'nodeids': nodes, 'reviewed_lock': lock(nodes),
            'source_hash': source['content_hash'],
            'environment_hash': environments['py']['identity']['content_hash'],
        }
    plans = {}
    for profile in ('affected', 'stage-full'):
        plan = make_plan(profile=profile, policy=policy, roots=roots, source=source,
            environments=environments, inventories=inventories, requested=['tiny'])
        plans[profile] = plan
        path = tmp_path / (profile + '.json')
        write_once_json(path, plan)
        run_campaign(plan, path, tmp_path / profile, jobs=1, run_id=profile)
    result = check_pair(policy, plans['affected'], plans['stage-full'],
        affected_evidence=tmp_path / 'affected', full_evidence=tmp_path / 'stage-full',
        affected_hash=plans['affected']['content_hash'], full_hash=plans['stage-full']['content_hash'],
        affected_run='affected', full_run='stage-full', expected_owners=['tiny'])
    assert result['selection_trusted_for_this_scenario'] is (not omitted_fails)
    assert result['full_failed_while_affected_passed'] is omitted_fails
    assert result['full']['required_obligations'] == 3
    assert result['affected']['required_obligations'] == 2
    assert not result['full_acceptance']
