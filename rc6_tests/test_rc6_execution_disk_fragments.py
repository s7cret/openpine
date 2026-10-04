"""Disk observations survive genuine fragment merge and checked offline replay."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from openpine.verification import execution_campaign as campaign
from openpine.verification.execution_fragments import merge_fragments
from openpine.verification.execution_identity import write_once_json, hash_file
from openpine.verification.identity import read_json, seal
from rc6_tests.test_rc6_execution_disk import observe, planned


def guarded_fragments(tmp_path, monkeypatch, values=None, bodies=None):
    plan, path = planned(tmp_path, monkeypatch, {'minimum_free_bytes': 100}, bodies=bodies)
    observe(monkeypatch, [200])
    keys = [(t['id'], s['id']) for t in plan['tasks'] for s in t['shards']]
    assert len(keys) == 2
    fragments = []
    for index, key in enumerate(keys):
        if values is not None:
            observe(monkeypatch, [values[index]])
        output = tmp_path / f'fragment-{index}'
        run = campaign.run_campaign(plan, path, output, run_id='disk-fragments', shard_keys=[key])
        report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments', expected_shards=[key])
        assert report['pytest_scope_passed'], report
        write_once_json(output / 'aggregate.json', report)
        fragments.append((output, run))
    return plan, fragments


def test_overflow_fragment_transport_offline_exact_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2)
    bodies = {'test_a.py': 'def test_value():\n    assert True\n',
              'test_b.py': 'import time\ndef test_value():\n    time.sleep(.5)\n'}
    plan, fragments = guarded_fragments(tmp_path, monkeypatch, values=[200, 300], bodies=bodies)
    assert fragments[0][1]['disk_free_guard']['samples'] != fragments[1][1]['disk_free_guard']['samples']
    primaries = {}
    for index, (root, raw) in enumerate(fragments):
        assert raw['disk_free_guard']['observations'] == []
        desc = raw['disk_free_guard']['observation_stream']
        primaries[index] = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file() and 'private' not in p.parts}
        assert hash_file(root / desc['path']) == desc['sha256']
    output = tmp_path / 'merged'
    report = merge_fragments(plan, [root for root, _ in fragments], output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert report['pytest_scope_passed'], report
    merged = read_json(output / 'run.json')['disk_free_guard']
    assert merged['observations'] == [] and merged['observation_stream']
    assert merged['samples'] == sum(raw['disk_free_guard']['samples'] for _, raw in fragments)
    assert merged['minimum_observed_free_bytes'] == 200
    for index, (root, _) in enumerate(fragments):
        for name, data in primaries[index].items():
            if name == 'aggregate.json':
                continue  # Aggregate is a local convenience, not primary input.
            assert (root / name).read_bytes() == data
            assert (output / 'fragments' / f'f{index:04d}' / name).read_bytes() == data
        shutil.rmtree(root)
    for root in plan['roots'].values():
        shutil.rmtree(root)
    archived = tmp_path / 'archive'
    shutil.copytree(output, archived)
    shutil.rmtree(output)
    assert campaign.aggregate_campaign(plan, archived, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments') == report


def test_guarded_fragment_merge_preserves_observations_and_archive(tmp_path, monkeypatch):
    plan, fragments = guarded_fragments(tmp_path, monkeypatch)
    primaries = {}
    for index, (root, run) in enumerate(fragments):
        names = {'run.json'}
        for attempt in run['attempts']:
            names.update(artifact['path'] for artifact in attempt['artifacts'].values())
            names.add('/'.join((attempt['task'], attempt['shard'], attempt['attempt_id'], 'execution.json')))
        primaries[index] = {name: (root / name).read_bytes() for name in names}
    output = tmp_path / 'merged'
    report = merge_fragments(plan, [root for root, _ in fragments], output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert report['pytest_scope_passed'], report
    run = read_json(output / 'run.json')
    expected = [row for _, fragment in fragments for row in fragment['disk_free_guard']['observations']]
    assert run['disk_free_guard']['observations'] == expected
    assert run['disk_free_guard']['samples'] == len(expected)
    assert run['disk_free_guard']['minimum_free_bytes'] == 100
    assert run['disk_free_guard']['minimum_observed_free_bytes'] == 200
    for index, (root, _) in enumerate(fragments):
        for name, content in primaries[index].items():
            assert (root / name).read_bytes() == content
            assert (output / 'fragments' / f'f{index:04d}' / name).read_bytes() == content
        shutil.rmtree(root)
    for root in plan['roots'].values():
        shutil.rmtree(root)
    archived = tmp_path / 'archive'
    shutil.copytree(output, archived)
    shutil.rmtree(output)
    assert campaign.aggregate_campaign(plan, archived, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments') == report


@pytest.mark.parametrize('damage', ['missing', 'tampered'])
def test_bad_overflow_merge_retains_raw_primaries(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2)
    plan, fragments = guarded_fragments(tmp_path, monkeypatch)
    clones = []
    original_bytes = {}
    for index, (root, raw) in enumerate(fragments):
        clone = tmp_path / f'bad-clone-{index}'
        shutil.copytree(root, clone)
        clones.append(clone)
        original_bytes[index] = (root / 'run.json').read_bytes()
    detail = clones[0] / fragments[0][1]['disk_free_guard']['observation_stream']['path']
    if damage == 'missing':
        detail.unlink()
    else:
        detail.write_bytes(detail.read_bytes() + b'{}\n')
    output = tmp_path / 'rejected'
    report = merge_fragments(plan, clones, output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert not report['pytest_scope_passed']
    assert any('disk' in error or 'checksum' in error for error in report['errors'])
    for index, (root, raw) in enumerate(fragments):
        copied = output / 'fragments' / f'f{index:04d}'
        assert (root / 'run.json').read_bytes() == original_bytes[index]
        assert (copied / 'run.json').read_bytes() == original_bytes[index]
        for attempt in raw['attempts']:
            for desc in attempt['artifacts'].values():
                assert (copied / desc['path']).read_bytes() == (root / desc['path']).read_bytes()


def test_malformed_merged_disk_receipt_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2)
    plan, fragments = guarded_fragments(tmp_path, monkeypatch)
    output = tmp_path / 'merged'
    merge_fragments(plan, [root for root, _ in fragments], output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    changed = copy.deepcopy(read_json(output / 'run.json'))
    changed['disk_free_guard'] = ['invalid']
    changed = seal({key: value for key, value in changed.items() if key != 'content_hash'})
    original = campaign.read_json
    monkeypatch.setattr(campaign, 'read_json', lambda path: changed if Path(path) == output / 'run.json' else original(path))
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert not report['pytest_scope_passed']


def test_merged_stream_reordering_rejected_even_with_valid_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign, 'DISK_INLINE_LIMIT', 2)
    plan, fragments = guarded_fragments(tmp_path, monkeypatch, values=[200, 300])
    output = tmp_path / 'merged'
    report = merge_fragments(plan, [root for root, _ in fragments], output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert report['pytest_scope_passed']
    changed = copy.deepcopy(read_json(output / 'run.json'))
    desc = changed['disk_free_guard']['observation_stream']
    new_detail = output / 'reordered.jsonl'
    new_detail.write_bytes(b''.join(reversed((output / desc['path']).read_bytes().splitlines(keepends=True))))
    changed['disk_free_guard']['observation_stream'] = {'path': new_detail.name, 'sha256': hash_file(new_detail)}
    changed = seal({key: value for key, value in changed.items() if key != 'content_hash'})
    original = campaign.read_json
    monkeypatch.setattr(campaign, 'read_json', lambda path: changed if Path(path) == output / 'run.json' else original(path))
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert not report['pytest_scope_passed'] and any('fragment provenance' in e for e in report['errors'])


@pytest.mark.parametrize('mutation', ['invented-sample', 'omitted-sample', 'reordered-samples', 'summary-floor', 'raw-missing', 'raw-tripped'])
def test_guarded_fragment_replay_rejects_unbound_diagnostics(tmp_path, monkeypatch, mutation):
    plan, fragments = guarded_fragments(tmp_path, monkeypatch)
    output = tmp_path / 'merged'
    merge_fragments(plan, [root for root, _ in fragments], output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    changed = copy.deepcopy(read_json(output / 'run.json'))
    assert 'disk_free_guard' in changed, 'merged receipt lost frozen disk diagnostics'
    injected_path = output / 'run.json'
    if mutation.startswith('raw-'):
        injected_path = output / changed['merged_fragments'][0]['run']['path']
        changed = copy.deepcopy(read_json(injected_path))
        if mutation == 'raw-missing':
            del changed['disk_free_guard']
        else:
            changed['disk_free_guard']['tripped'] = True
    elif mutation == 'summary-floor':
        changed['disk_free_guard']['minimum_free_bytes'] = 101
    else:
        rows = changed['disk_free_guard']['observations']
        if mutation == 'invented-sample':
            rows.append({'observed_at': '2000-01-01T00:00:00+00:00', 'free_bytes': 200, 'error': None})
        elif mutation == 'omitted-sample':
            rows.pop()
        else:
            rows.reverse()
        changed['disk_free_guard']['samples'] = len(rows)
    changed = seal({key: value for key, value in changed.items() if key != 'content_hash'})
    original = campaign.read_json
    # Inject only reader input; immutable source and transferred receipt files stay untouched.
    monkeypatch.setattr(campaign, 'read_json', lambda path: changed if Path(path) == injected_path else original(path))
    report = campaign.aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id='disk-fragments')
    assert not report['pytest_scope_passed'], report
    assert any('disk' in error or 'fragment' in error for error in report['errors'])
