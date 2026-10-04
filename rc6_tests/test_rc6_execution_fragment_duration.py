"""Bounded real producers with synthetic durations from the hosted 23-fragment vector.

These miniature receipts are regression fixtures, never product acceptance evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_fragments import merge_fragments
from openpine.verification.execution_identity import write_once_json
from openpine.verification.identity import read_json, verify
from rc6_tests.test_rc6_execution_platform import reseal, tiny_plan

# Independently retained from both hosted foundation merges, in fragment order.
HOSTED_WALL_SECONDS = (
    682.075115392, 753.317748493, 617.50584317, 41.452320046,
    34.364913005999995, 45.05757242600001, 52.380830497999995,
    32.289826188999996, 60.995280296999994, 46.822497686999995,
    52.208036436000015, 55.22072006900001, 2185.132674844,
    2344.512166898, 96.31994617599999, 106.956808747,
    63.263463974000004, 518.747595843, 368.66971004699997,
    488.8811640839999, 176.13897797699997, 167.110655748,
    189.319796066,
)
# Rounded exact sum of the input binary64 values, not computed by the producer.
EXPECTED_WALL_SECONDS = 9178.743664113
RUN_ID = 'duration-regression'


def duration_fixture(root):
    bodies = {f'test_{index:02d}.py': 'def test_value():\n    assert True\n'
              for index in range(len(HOSTED_WALL_SECONDS))}
    bodies['test_a.py'] = bodies.pop('test_00.py')  # tiny_plan smoke selector
    plan, path = tiny_plan(root, bodies=bodies, shards=len(bodies))
    keys = [(task['id'], shard['id']) for task in plan['tasks'] for shard in task['shards']]
    assert len(keys) == len(HOSTED_WALL_SECONDS) == 23
    fragments = []
    for index, (key, wall) in enumerate(zip(keys, HOSTED_WALL_SECONDS, strict=True)):
        output = root / f'raw-{index:02d}'
        raw = run_campaign(plan, path, output, run_id=RUN_ID, shard_keys=[key], jobs=1)
        # Only fresh owned test fixtures receive synthetic duration values.
        raw['wall_seconds'] = wall
        (output / 'run.json').write_text(json.dumps(reseal(raw)))
        checked = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'],
                                     expected_run_id=RUN_ID, expected_shards=[key])
        assert checked['pytest_scope_passed'], checked
        fragments.append(output)
    write_once_json(root / 'fragment-paths.json', [str(path) for path in fragments])
    return plan, fragments


@pytest.fixture(scope='module')
def duration_fragments(tmp_path_factory):
    return duration_fixture(tmp_path_factory.mktemp('duration-fixture'))


def checked_merge(plan, fragments, output):
    before = [(root / 'run.json').read_bytes() for root in fragments]
    report = merge_fragments(plan, fragments, output, expected_plan_hash=plan['content_hash'],
                             expected_run_id=RUN_ID)
    assert report['pytest_scope_passed'], report
    run = read_json(output / 'run.json')
    verify(run, run['schema_id'])
    verify(report, report['schema_id'])
    assert aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'],
                              expected_run_id=RUN_ID) == report
    for index, (root, primary) in enumerate(zip(fragments, before, strict=True)):
        desc = run['merged_fragments'][index]['run']
        copied = (output / desc['path']).read_bytes()
        assert copied == primary == (root / 'run.json').read_bytes()
        assert desc['sha256'] == 'sha256:' + hashlib.sha256(copied).hexdigest()
    return run, report


@pytest.mark.parametrize('reverse', [False, True])
def test_real_merge_uses_exact_stable_hosted_duration(duration_fragments, tmp_path, reverse):
    plan, fragments = duration_fragments
    ordered = list(reversed(fragments)) if reverse else fragments
    run, _ = checked_merge(plan, ordered, tmp_path / 'merged')
    assert run['fragment_wall_seconds'] == list(reversed(HOSTED_WALL_SECONDS) if reverse else HOSTED_WALL_SECONDS)
    assert run['wall_seconds'] == EXPECTED_WALL_SECONDS


def test_common_join_accepts_equal_real_merge_hashes(duration_fragments, tmp_path, monkeypatch):
    from openpine.verification.execution_ci import finalize_stabilization
    from openpine.verification import execution_cli

    plan, fragments = duration_fragments
    first, second = tmp_path / 'first', tmp_path / 'second'
    checked_merge(plan, fragments, first / 'merged')
    checked_merge(plan, fragments, second / 'merged')

    class ReachedOwnerDispatch(Exception):
        pass

    # Exercise the real strict join and locator producer, not expensive owners.
    def dispatch(args):
        assert args.command == 'test-stabilization'
        raise ReachedOwnerDispatch

    monkeypatch.setattr(execution_cli, 'run_command', dispatch)
    output = tmp_path / 'common'
    with pytest.raises(ReachedOwnerDispatch):
        finalize_stabilization(plan, Path(plan['roots']['openpine']), [first, second], output, RUN_ID)
    packet = read_json(output / 'evidence/stabilization-inputs.json')
    assert packet['campaign'] == 'merged' and packet['run_id'] == RUN_ID
    assert (output / 'evidence/merged/run.json').read_bytes() == (first / 'merged/run.json').read_bytes()


def test_common_join_keeps_strict_hash_rejection(duration_fragments, tmp_path):
    from openpine.verification.execution_ci import finalize_stabilization

    plan, fragments = duration_fragments
    first, second = tmp_path / 'first', tmp_path / 'second'
    checked_merge(plan, fragments, first / 'merged')
    shutil.copytree(first, second)
    changed = copy.deepcopy(read_json(second / 'merged/run.json'))
    changed['wall_seconds'] = EXPECTED_WALL_SECONDS + 1.0
    (second / 'merged/run.json').write_text(json.dumps(reseal(changed)))
    with pytest.raises(ValueError, match='different common campaigns'):
        finalize_stabilization(plan, Path(plan['roots']['openpine']), [first, second],
                               tmp_path / 'common', RUN_ID)
