"""Raw cross-runner evidence and relocatable candidate bindings, real processes."""
from __future__ import annotations
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from openpine.verification.execution_binding import make_binding, validate_binding
from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_fragments import merge_fragments
from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import read_json
from rc6_tests.test_rc6_execution_platform import HOST, reseal, tiny_plan

def all_keys(plan):
    return [(task['id'], shard['id']) for task in plan['tasks'] for shard in task['shards']]

@pytest.fixture(scope='module')
def split_run(tmp_path_factory):
    root = tmp_path_factory.mktemp('fragment-campaign')
    plan, path = tiny_plan(root)
    keys = all_keys(plan)
    fragments = []
    for index, key in enumerate(keys):
        relocated = root / ('runner-' + str(index))
        roots = {}
        for name, origin in plan['roots'].items():
            target = relocated / name
            shutil.copytree(origin, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            roots[name] = target
        binding = make_binding(plan, roots, {'py': sys.executable})
        output = root / ('result-' + str(index))
        run = run_campaign(plan, path, output, run_id='distributed', shard_keys=[key], binding=binding)
        report = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'], expected_run_id='distributed', expected_shards=[key])
        assert report['pytest_scope_passed'], report
        assert report['is_fragment'] and (not report['full_stage_accepted'])
        assert run['binding'] is not None
        fragments.append(output)
    return (plan, path, fragments)

def test_fragments_merge_with_different_locations_and_one_unchanged_plan(split_run, tmp_path):
    plan, _, fragments = split_run
    before = copy.deepcopy(plan)
    report = merge_fragments(plan, fragments, tmp_path / 'merged', expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert report['ok'], report
    assert report['executed_obligations'] == 2 and report['required_shards'] == 2
    assert plan == before
    run = read_json(tmp_path / 'merged/run.json')
    assert len(run['merged_fragments']) == 2
    assert len({a['binding_hash'] for a in run['attempts']}) == 2
    assert not report['full_stage_accepted'] and (not report['full_release_accepted'])
    transported = tmp_path / 'transported'
    shutil.copytree(tmp_path / 'merged', transported)
    checked = aggregate_campaign(plan, transported, expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert checked['ok']

def test_single_fragment_does_not_export_complete_plan(split_run, tmp_path):
    plan, _, fragments = split_run
    checked = aggregate_campaign(plan, fragments[0], expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert not checked['ok'] and checked['executed_obligations'] == 1
    report = merge_fragments(plan, fragments[:1], tmp_path / 'missing', expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert not report['ok'] and any(('missing shard' in e for e in report['errors']))

@pytest.mark.parametrize('mutation', ['duplicate', 'wrong-run', 'wrong-plan', 'wrong-source', 'failed', 'broken-xml', 'missing-output', 'summary-only', 'wrong-binding', 'wrong-selector'])
def test_merge_rejects_bad_fragment_and_keeps_failed_evidence(split_run, tmp_path, mutation):
    plan, _, originals = split_run
    sources = []
    for index, original in enumerate(originals):
        dst = tmp_path / ('f' + str(index))
        shutil.copytree(original, dst)
        sources.append(dst)
    if mutation == 'duplicate':
        shutil.rmtree(sources[1])
        shutil.copytree(sources[0], sources[1])
    else:
        root = sources[0]
        run = read_json(root / 'run.json')
        attempt = run['attempts'][0]
        if mutation == 'wrong-run':
            run['run_id'] = 'other'
        elif mutation == 'wrong-plan':
            run['plan_hash'] = 'sha256:' + '0' * 64
        elif mutation == 'wrong-source':
            run['source_after'] = 'sha256:' + '0' * 64
        elif mutation == 'failed':
            attempt['status'], attempt['returncode'] = ('failed', 1)
        elif mutation == 'summary-only':
            run['attempts'] = []
        elif mutation == 'wrong-binding':
            binding = read_json(root / 'binding.json')
            binding['plan_hash'] = 'sha256:' + '0' * 64
            (root / 'binding.json').write_text(json.dumps(reseal(binding)))
            run['binding']['sha256'] = hash_file(root / 'binding.json')
        elif mutation == 'missing-output':
            (root / attempt['artifacts']['junit']['path']).unlink()
        else:
            desc = attempt['artifacts']['junit' if mutation == 'broken-xml' else 'selectors']
            target = root / desc['path']
            target.write_text('<broken>' if mutation == 'broken-xml' else 'test_missing.py::test_nope\n')
            desc['sha256'] = hash_file(target)
            (root / f"{attempt['task']}/{attempt['shard']}/{attempt['attempt_id']}/execution.json").write_text(json.dumps(attempt))
        (root / 'run.json').write_text(json.dumps(reseal(run)))
    report = merge_fragments(plan, sources, tmp_path / 'merged', expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert not report['ok'] and report['errors'], mutation
    assert (tmp_path / 'merged/run.json').is_file()
    assert (tmp_path / 'merged/aggregate.json').is_file()

@pytest.mark.parametrize('mutation', ['remove-provenance', 'change-fragment', 'duplicate-provenance', 'invent-binding'])
def test_merged_provenance_rechecked_not_trusted_as_a_flag(split_run, tmp_path, mutation):
    plan, _, fragments = split_run
    root = tmp_path / 'merged'
    assert merge_fragments(plan, fragments, root, expected_plan_hash=plan['content_hash'], expected_run_id='distributed')['ok']
    run = read_json(root / 'run.json')
    if mutation == 'remove-provenance':
        run['merged_fragments'] = True
    elif mutation == 'change-fragment':
        entry = run['merged_fragments'][0]['run']
        raw_path = root / entry['path']
        raw = read_json(raw_path)
        raw['source_after'] = 'sha256:' + '0' * 64
        raw_path.write_text(json.dumps(reseal(raw)))
        entry['sha256'] = hash_file(raw_path)
    elif mutation == 'duplicate-provenance':
        run['merged_fragments'].append(copy.deepcopy(run['merged_fragments'][0]))
    else:
        attempt = run['attempts'][0]
        attempt['binding_hash'] = 'sha256:' + '0' * 64
        (root / f"{attempt['task']}/{attempt['shard']}/{attempt['attempt_id']}/execution.json").write_text(json.dumps(attempt))
    (root / 'run.json').write_text(json.dumps(reseal(run)))
    checked = aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'], expected_run_id='distributed')
    assert not checked['ok']
    assert any(('provenance' in e for e in checked['errors']))

@pytest.mark.parametrize('mutation', ['missing-root', 'wrong-plan', 'relative-path', 'unknown-python', 'changed-bytes'])
def test_binding_cannot_redefine_candidate_or_environment(tmp_path, mutation):
    plan, _ = tiny_plan(tmp_path)
    roots = {n: Path(p) for n, p in plan['roots'].items()}
    binding = make_binding(plan, roots, {'py': sys.executable})
    if mutation == 'changed-bytes':
        (roots['tiny'] / 'test_a.py').write_text('def test_value():\n    assert False\n')
        with pytest.raises(ValueError, match='source'):
            make_binding(plan, roots, {'py': sys.executable})
        return
    if mutation == 'missing-root':
        binding['roots'].pop('tiny')
    elif mutation == 'wrong-plan':
        binding['plan_hash'] = 'sha256:' + '0' * 64
    elif mutation == 'relative-path':
        binding['roots']['tiny'] = '../tiny'
    else:
        binding['interpreters']['unknown'] = sys.executable
    with pytest.raises(ValueError):
        validate_binding(plan, reseal(binding))

@pytest.mark.parametrize('keys', [[], [('unknown', 's000')], [('tiny@py', 's000'), ('tiny@py', 's000')]])
def test_runner_rejects_empty_unknown_duplicate_assignment_before_execution(tmp_path, keys):
    plan, path = tiny_plan(tmp_path)
    with pytest.raises(ValueError, match='fragment'):
        run_campaign(plan, path, tmp_path / 'output', shard_keys=keys)
    assert not (tmp_path / 'output').exists()

@pytest.mark.parametrize('budget', [True, 0, 63, 128])
def test_invalid_or_under_reserved_memory_budget_fails_before_execution(tmp_path, budget):
    plan, path = tiny_plan(tmp_path)
    with pytest.raises(ValueError, match='memory'):
        run_campaign(plan, path, tmp_path / 'output', memory_mib=budget)
    assert not (tmp_path / 'output').exists()

def test_memory_budget_records_sampled_enforcement_not_a_claimed_kernel_limit(tmp_path):
    plan, path = tiny_plan(tmp_path, shards=1)
    run = run_campaign(plan, path, tmp_path / 'output', memory_mib=1024, run_id='memory')
    report = aggregate_campaign(plan, tmp_path / 'output', expected_plan_hash=plan['content_hash'], expected_run_id='memory')
    assert report['ok'], report
    assert run['rss_samples'] > 0 and run['memory_budget_mib'] == 1024
    assert 'not a kernel hard limit' in run['memory_enforcement']

def test_public_cli_binds_runs_and_merges_real_fragments(tmp_path):
    plan, path = tiny_plan(tmp_path)
    common = ['--plan', str(path), '--expected-plan-hash', plan['content_hash']]
    binding = tmp_path / 'binding.json'
    bind_args = ['test-bind', *common, '--output', str(binding), '--python', 'py=' + sys.executable]
    for name, root in plan['roots'].items():
        bind_args += ['--root', name + '=' + root]

    def cli(arguments):
        result = subprocess.run([sys.executable, '-m', 'openpine.verification', *arguments], cwd=HOST, capture_output=True, text=True, timeout=45)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
        assert result.returncode == 0, result.stdout + result.stderr
    cli(bind_args)
    outputs = []
    for index, (task, shard) in enumerate(all_keys(plan)):
        output = tmp_path / ('out' + str(index))
        cli(['test-run', *common, '--binding', str(binding), '--run-id', 'cli', '--shard', task + '/' + shard, '--output', str(output)])
        outputs += ['--fragment', str(output)]
    cli(['test-merge', *common, '--run-id', 'cli', '--output', str(tmp_path / 'merged'), *outputs])
    assert read_json(tmp_path / 'merged/aggregate.json')['ok']

def test_explicit_memory_budget_cannot_pass_when_observation_is_denied(tmp_path, monkeypatch):
    import psutil
    plan, path = tiny_plan(tmp_path)

    class HiddenProcess:

        def children(self, recursive=False):
            raise psutil.AccessDenied(42)
    monkeypatch.setattr(psutil, 'Process', HiddenProcess)
    run = run_campaign(plan, path, tmp_path / 'run', jobs=1, run_id='denied-memory', memory_mib=1024)
    assert any(('memory' in error for error in run['errors']))
    report = aggregate_campaign(plan, tmp_path / 'run', expected_plan_hash=plan['content_hash'], expected_run_id='denied-memory')
    assert not report['pytest_scope_passed']
