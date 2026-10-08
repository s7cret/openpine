"""Current status uses raw owner execution, never self-sealed PASS summaries."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

from openpine.verification.execution_process import run_logged
from openpine.verification.identity import read_json, seal
from openpine.verification.stabilization_evidence import verify_command


def reseal(value):
    value = copy.deepcopy(value)
    value.pop("content_hash", None)
    return seal(value)


@pytest.fixture
def command(tmp_path):
    argv = [sys.executable, "-I", "-c", 'import json; print(json.dumps({"value": 42}))']
    root = tmp_path / "command"
    run_logged(argv, cwd=tmp_path, output=root, env={"PATH": os.environ["PATH"]})
    return root, {"argv": argv, "cwd": str(tmp_path)}, {"value": 42}


def test_real_command_raw_result(command):
    root, expected, output = command
    assert (
        verify_command(root, expected, expected_stdout=output)["status"] == "completed"
    )


@pytest.mark.parametrize(
    "mutation", ["stdout", "resealed-output", "argv", "cwd", "missing-log", "failed"]
)
def test_command_tampering_cannot_be_resealed(command, mutation):
    root, expected, output = command
    receipt = read_json(root / "command.json")
    if mutation in {"stdout", "resealed-output"}:
        (root / "stdout.log").write_text('{"value": 41}\n')
        if mutation == "resealed-output":
            from openpine.verification.execution_identity import hash_file

            receipt["files"]["stdout.log"] = hash_file(root / "stdout.log")
    elif mutation == "missing-log":
        (root / "stderr.log").unlink()
    elif mutation in {"argv", "cwd"}:
        receipt[mutation] = ["true"] if mutation == "argv" else "/"
    else:
        receipt["returncode"] = 1
    (root / "command.json").write_text(json.dumps(reseal(receipt)))
    with pytest.raises((ValueError, OSError)):
        verify_command(root, expected, expected_stdout=output)


@pytest.fixture(scope="module")
def full_fixture(tmp_path_factory):
    from rc6_tests.stabilization_fixture import build_fixture

    return build_fixture(tmp_path_factory.mktemp("real-minimal-stabilization"))


def replay(fixture):
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = fixture
    return run_stabilization_gate(
        host,
        plan,
        evidence,
        expected_plan_hash=plan["content_hash"],
        run_id=packet["run_id"],
    )


def test_ci_common_owner_invokes_current_and_keeps_missing_owners_negative(full_fixture, tmp_path, monkeypatch):
    import shutil
    from openpine.verification import execution_ci as ci

    host, plan, evidence, packet = full_fixture
    native = tmp_path / 'native'
    shutil.copytree(evidence / packet['campaign'], native / 'merged')
    for env, relative in packet['gates']['foundation']['environments'].items():
        shutil.copytree(evidence / relative, native / 'suites' / env)
    for task, relative in packet['gates']['coverage']['tasks'].items():
        shutil.copytree(evidence / relative, native / 'owner-coverage' / task)
    second = tmp_path / 'native-second'
    shutil.copytree(native, second)
    frozen = {path: path.read_bytes() for path in native.rglob('*') if path.is_file()}
    output = tmp_path / 'common'
    from openpine.verification import stage_gate
    original_gate = stage_gate.run_stabilization_gate
    owner_results = []

    def counted_gate(*args, **kwargs):
        fresh = original_gate(*args, **kwargs)
        owner_results.append(fresh)
        return fresh

    monkeypatch.setattr(stage_gate, 'run_stabilization_gate', counted_gate)
    result = ci.finalize_stabilization(plan, host, [native, second], output, packet['run_id'])
    assert len(owner_results) == 2
    assert owner_results[0] is not owner_results[1]
    assert owner_results == [result, result]
    views = stage_gate.current_views(owner_results[1])
    for view in ('summary', 'remainder'):
        assert read_json(output / (view + '.json')) == views[view]
    assert (output / 'current.json').read_bytes() == (output / 'replayed-current.json').read_bytes()
    assert result['full_release_accepted'] is False
    assert {path: path.read_bytes() for path in frozen} == frozen
    saved = read_json(output / 'current.json')
    assert result == saved and not result['ok']
    assert read_json(output / 'replayed-current.json') == saved
    assert read_json(output / 'summary.json')['candidate_hash'] == plan['source']['content_hash']
    assert read_json(output / 'remainder.json')['items']
    locators = read_json(output / 'evidence/stabilization-inputs.json')
    assert set(locators['gates']) == {'branch-reconciliation', 'foundation', 'coverage', 'protected-workers'}
    for gate in ('frontend', 'packages', 'test-performance'):
        assert saved['stabilization']['gates'][gate]['status'] == 'not_run'
    for gate in ('foundation', 'coverage', 'protected-workers'):
        assert saved['stabilization']['gates'][gate]['status'] == 'passed'
    assert saved['stage2']['status'] == 'in_progress'
    assert saved['full_stage2_accepted'] is False
    provenance = read_json(output / 'invocations.json')
    assert provenance['exits'] == [1, 1]
    assert [argv[0] for argv in provenance['argv']] == ['test-stabilization', 'test-current']
    assert provenance['projections'] == [
        {'operation': 'current_views', 'source': 'replayed-current.json',
         'source_content_hash': saved['content_hash'], 'view': view, 'output': view + '.json'}
        for view in ('summary', 'remainder')
    ]


@pytest.mark.parametrize('mutation', ['fresh-result', 'saved-current', 'raw-evidence'])
def test_ci_common_fresh_replay_fails_closed(full_fixture, tmp_path, monkeypatch, mutation):
    import shutil
    from openpine.verification import execution_ci as ci, stage_gate

    host, plan, evidence, packet = full_fixture
    native = tmp_path / 'native'
    shutil.copytree(evidence / packet['campaign'], native / 'merged')
    for env, relative in packet['gates']['foundation']['environments'].items():
        shutil.copytree(evidence / relative, native / 'suites' / env)
    output = tmp_path / 'common'
    original_gate = stage_gate.run_stabilization_gate
    calls = []

    def adversarial_gate(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2 and mutation == 'raw-evidence':
            (output / 'evidence/merged/run.json').write_text('{}')
        fresh = original_gate(*args, **kwargs)
        if len(calls) == 2 and mutation == 'fresh-result':
            # Keep the real freshly evaluated verdict, but disagree on identity.
            fresh = reseal({**fresh, 'run_id': 'foreign-replay'})
        if len(calls) == 2 and mutation == 'saved-current':
            saved = read_json(output / 'current.json')
            (output / 'current.json').write_text(json.dumps(reseal({**saved, 'run_id': 'forged-saved'})))
        return fresh

    monkeypatch.setattr(stage_gate, 'run_stabilization_gate', adversarial_gate)
    match = None if mutation == 'raw-evidence' else 'saved current verdict differs from fresh raw-evidence replay'
    with pytest.raises(ValueError, match=match):
        ci.finalize_stabilization(plan, host, [native], output, packet['run_id'])
    assert len(calls) == 2
    assert (output / 'current.json').is_file()
    assert not read_json(output / 'current.json')['ok']
    for name in ('replayed-current.json', 'summary.json', 'remainder.json', 'invocations.json'):
        assert not (output / name).exists()


@pytest.mark.parametrize('mutation', ['stale-run', 'detached', 'missing-foundation'])
def test_ci_common_owner_rejects_foreign_or_missing_primaries(full_fixture, tmp_path, mutation):
    import shutil
    from openpine.verification import execution_ci as ci

    host, plan, evidence, packet = full_fixture
    native = tmp_path / 'native'
    shutil.copytree(evidence / packet['campaign'], native / 'merged')
    if mutation != 'missing-foundation':
        for env, relative in packet['gates']['foundation']['environments'].items():
            shutil.copytree(evidence / relative, native / 'suites' / env)
    if mutation == 'detached':
        (native / 'merged/run.json').unlink()
        (native / 'merged/run.json').symlink_to(evidence / packet['campaign'] / 'run.json')
    if mutation in {'stale-run', 'detached'}:
        with pytest.raises((ValueError, OSError)):
            ci.finalize_stabilization(plan, host, [native], tmp_path / 'common',
                                      'foreign' if mutation == 'stale-run' else packet['run_id'])
    else:
        result = ci.finalize_stabilization(plan, host, [native], tmp_path / 'common', packet['run_id'])
        assert not result['ok']
        assert result['stabilization']['gates']['foundation']['status'] == 'not_run'
        assert result['stabilization']['gates']['coverage']['status'] == 'not_run'


@pytest.fixture(scope="module")
def task_fragment_coverage(full_fixture, tmp_path_factory):
    """Real task producers and merger, with the unchanged full eight-owner plan."""
    import shutil
    from openpine.verification.execution_ci import run_ci_task
    from openpine.verification.execution_fragments import merge_fragments

    host, plan, evidence, packet = full_fixture
    output = tmp_path_factory.mktemp("task-fragment-coverage")
    roots = {name: output / 'execution-sources' / name for name in plan['roots']}
    for name, root in roots.items():
        shutil.copytree(plan['roots'][name], root, ignore=shutil.ignore_patterns('__pycache__'))
    fragments = []
    changed = copy.deepcopy(packet)
    changed['campaign'] = 'merged'
    changed['gates'] = {'coverage': {'tasks': {}}}
    for index, task in enumerate(plan['tasks']):
        fragment = output / f'task-{index}'
        result = run_ci_task(plan, evidence / 'plan.json', roots, task['id'],
                             fragment, packet['run_id'])
        assert result['pytest_scope_passed'] and result['coverage_scope_passed']
        fragments.append(fragment)
        relative = 'coverage/' + task['id']
        shutil.copytree(fragment / 'coverage-owner', output / relative)
        changed['gates']['coverage']['tasks'][task['id']] = relative
    merged = merge_fragments(plan, fragments, output / 'merged',
                            expected_plan_hash=plan['content_hash'],
                            expected_run_id=packet['run_id'])
    assert merged['pytest_scope_passed']
    (output / 'stabilization-inputs.json').write_text(json.dumps(changed))
    return host, plan, output, changed


def test_common_coverage_accepts_exact_task_fragment_with_whole_membership(task_fragment_coverage):
    host, plan, evidence, packet = task_fragment_coverage
    frozen = {p: p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    plan_before = copy.deepcopy(plan)
    whole = read_json(evidence / 'merged/aggregate.json')['content_hash']
    receipts = [read_json(evidence / relative / 'receipt.json')
                for relative in packet['gates']['coverage']['tasks'].values()]
    assert len({task['component'] for task in plan['tasks']}) == 8
    assert all(receipt['pytest_aggregate_hash'] != whole for receipt in receipts)
    current = replay(task_fragment_coverage)
    assert current['stabilization']['gates']['coverage']['status'] == 'passed', current
    assert not current['ok'] and not current['full_release_accepted']
    assert not current['full_stage2_accepted']
    assert plan == plan_before
    assert {p: p.read_bytes() for p in frozen} == frozen


@pytest.mark.parametrize('mutation', ['wrong-task', 'foreign-fragment', 'tampered-aggregate', 'missing-provenance'])
def test_common_task_fragment_coverage_fails_closed(task_fragment_coverage, tmp_path, mutation):
    import shutil
    host, plan, original, packet = task_fragment_coverage
    evidence = tmp_path / 'evidence'
    shutil.copytree(original, evidence)
    first, second = plan['tasks'][:2]
    receipt_path = evidence / packet['gates']['coverage']['tasks'][first['id']] / 'receipt.json'
    receipt = read_json(receipt_path)
    if mutation == 'wrong-task':
        receipt['task'] = second['id']
    elif mutation == 'tampered-aggregate':
        receipt['pytest_aggregate_hash'] = 'sha256:' + '0' * 64
    elif mutation == 'foreign-fragment':
        other = read_json(evidence / packet['gates']['coverage']['tasks'][second['id']] / 'receipt.json')
        receipt['pytest_aggregate_hash'] = other['pytest_aggregate_hash']
    else:
        run_path = evidence / 'merged/run.json'
        run = read_json(run_path)
        run.pop('merged_fragments')
        run_path.write_text(json.dumps(reseal(run)))
    if mutation != 'missing-provenance':
        receipt_path.write_text(json.dumps(reseal(receipt)))
    frozen = {p: p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    current = replay((host, plan, evidence, packet))
    assert current['stabilization']['gates']['coverage']['status'] == 'blocked', current
    assert not current['ok'] and not current['full_stage2_accepted']
    assert {p: p.read_bytes() for p in frozen} == frozen


def test_current_cli_exposes_checked_replay_binding():
    import argparse
    from openpine.verification.execution_cli import add_commands

    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command')
    add_commands(commands)
    for name in ('test-current', 'test-stabilization'):
        args = parser.parse_args([name, '--host-root', '/host', '--plan', '/plan',
                                 '--expected-plan-hash', 'hash', '--evidence', '/evidence',
                                 '--run-id', 'run', '--output', '/out', '--binding', '/binding'])
        assert args.binding == Path('/binding')


def test_source_replay_binding_preserves_original_execution_provenance(full_fixture, tmp_path):
    import shutil
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = full_fixture
    original = replay(full_fixture)
    roots = {}
    for name, location in plan['roots'].items():
        roots[name] = tmp_path / name
        shutil.copytree(location, roots[name], ignore=shutil.ignore_patterns('__pycache__'))
    binding = make_binding(plan, roots, {name: env['executable'] for name, env in plan['environments'].items()})
    run_bytes = (evidence / packet['campaign'] / 'run.json').read_bytes()
    result = run_stabilization_gate(roots['openpine'], plan, evidence,
                                   expected_plan_hash=plan['content_hash'], run_id=packet['run_id'],
                                   binding=binding)
    assert result == original
    assert (evidence / packet['campaign'] / 'run.json').read_bytes() == run_bytes


@pytest.fixture
def non_sibling_replay(full_fixture, tmp_path):
    import shutil
    from openpine.verification.execution_binding import make_binding

    _, plan, evidence, packet = full_fixture
    roots = {}
    for index, (name, location) in enumerate(plan['roots'].items()):
        roots[name] = tmp_path / ('checkout-' + str(index)) / 'source'
        shutil.copytree(location, roots[name], ignore=shutil.ignore_patterns('__pycache__'))
    binding = make_binding(plan, roots, {name: env['executable'] for name, env in plan['environments'].items()})
    return roots, binding, plan, evidence, packet


def test_foundation_non_sibling_mapping_preserves_all_replay_bytes(full_fixture, non_sibling_replay):
    from openpine.verification.stage_gate import run_stage_gate, run_stabilization_gate

    roots, binding, plan, evidence, packet = non_sibling_replay
    frozen_plan = copy.deepcopy(plan)
    frozen = {p: p.read_bytes() for p in evidence.rglob('*') if p.is_file()}
    result = run_stabilization_gate(roots['openpine'], plan, evidence,
                                   expected_plan_hash=plan['content_hash'], run_id=packet['run_id'],
                                   binding=binding)
    assert result == replay(full_fixture), result['stabilization']['gates']['foundation']
    for relative in packet['gates']['foundation']['environments'].values():
        folder = evidence / relative
        assert run_stage_gate(roots['openpine'], roots, folder, persist=False) == read_json(folder / 'stage1.json')
    assert plan == frozen_plan
    assert {p: p.read_bytes() for p in frozen} == frozen


@pytest.mark.parametrize('mutation', ['drift', 'missing', 'wrong-component', 'symlink', 'missing-mapping'])
def test_non_sibling_mapping_rejects_unverified_sources(non_sibling_replay, mutation):
    from openpine.verification.stage_gate import run_stabilization_gate

    roots, binding, plan, evidence, packet = non_sibling_replay
    if mutation == 'drift':
        (roots['pine2ast'] / 'unexpected.py').write_text('# drift\n')
    elif mutation == 'missing':
        binding['roots']['pine2ast'] = str(roots['pine2ast'].parent / 'absent')
    elif mutation == 'wrong-component':
        binding['roots']['pine2ast'], binding['roots']['pinelib'] = binding['roots']['pinelib'], binding['roots']['pine2ast']
    elif mutation == 'missing-mapping':
        binding['roots'].pop('pine2ast')
    else:
        link = roots['pine2ast'].parent / 'link'
        link.symlink_to(roots['pine2ast'], target_is_directory=True)
        binding['roots']['pine2ast'] = str(link)
    with pytest.raises((ValueError, OSError)):
        run_stabilization_gate(roots['openpine'], plan, evidence,
                               expected_plan_hash=plan['content_hash'], run_id=packet['run_id'],
                               binding=reseal(binding))


def test_seven_raw_owners_accept_real_minimal_execution_and_keep_language_debt(
    full_fixture,
):
    from openpine.verification.stage_gate import current_views

    current = replay(full_fixture)
    assert current["ok"], current
    assert all(
        row["status"] == "passed" for row in current["stabilization"]["gates"].values()
    )
    assert current["stage2"]["status"] == "in_progress"
    assert current["full_stage2_accepted"] is False
    views = current_views(current)
    assert {view["candidate_hash"] for view in views.values()} == {
        current["candidate_hash"]
    }
    assert {view["plan_hash"] for view in views.values()} == {current["plan_hash"]}
    assert views["remainder"]["items"]
    binding = views["remainder"]["remaining_spec_binding"]
    assert binding["requirement_count"] == 68
    assert len(binding["unclosed_requirements"]) == 68
    assert binding["full_stage2_accepted"] is binding["full_release_accepted"] is False


@pytest.mark.parametrize(
    "gate",
    [
        "branch-reconciliation",
        "foundation",
        "protected-workers",
        "coverage",
        "frontend",
        "packages",
        "test-performance",
    ],
)
def test_each_missing_owner_is_not_run_not_accepted(full_fixture, gate):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"].pop(gate)
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert (
            not current["ok"]
            and current["stabilization"]["gates"][gate]["status"] == "not_run"
        )
        assert current["stage2"]["status"] == "in_progress"
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("gate", [
    "branch-reconciliation", "foundation", "protected-workers", "coverage",
    "frontend", "packages", "test-performance",
])
def test_null_owner_locator_is_not_run_not_accepted(full_fixture, gate):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"][gate] = None
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert current["stabilization"]["gates"][gate]["status"] == "not_run"
        assert not current["ok"] and current["full_release_accepted"] is False
    finally:
        path.write_bytes(original)


def test_null_frozen_owner_specs_remain_not_run_despite_real_raw_inputs(tmp_path):
    from rc6_tests.stabilization_fixture import build_fixture

    fixture = build_fixture(tmp_path, stabilization_unconfigured=True)
    host, plan, _, packet = fixture
    policy = read_json(host / "verification/execution-policy.json")
    assert set(packet["gates"]) == set(policy["stabilization"]) - {"package_harness_inputs"}
    current = replay(fixture)
    assert all(row["status"] == "not_run" for row in current["stabilization"]["gates"].values())
    assert not current["ok"] and current["full_release_accepted"] is False
    assert current["plan_hash"] == plan["content_hash"]


@pytest.mark.parametrize("gate", [
    "branch-reconciliation", "foundation", "protected-workers", "coverage",
    "frontend", "packages", "test-performance",
])
@pytest.mark.parametrize("invalid", [[], False, "owner-receipt"])
def test_malformed_nonnull_owner_locator_is_blocked(full_fixture, gate, invalid):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"][gate] = invalid
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        row = current["stabilization"]["gates"][gate]
        assert row["status"] == "blocked" and row["errors"]
        assert not current["ok"] and current["full_release_accepted"] is False
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("invalid", [None, [], False, "owner-bindings"])
def test_malformed_frontend_binding_is_blocked(full_fixture, invalid):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"]["frontend"]["binding"] = invalid
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert current["stabilization"]["gates"]["frontend"]["status"] == "blocked"
        assert not current["ok"]
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("invalid", [None, [], False, "owner-locators"])
def test_malformed_owner_locator_mapping_is_rejected(full_fixture, invalid):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed["gates"] = invalid
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="locator mappings must be objects"):
            replay(full_fixture)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("omission", ["whole-environment", "single-component"])
def test_each_frozen_compatible_environment_requires_complete_component_tasks(
    full_fixture, omission
):
    from openpine.verification.execution_plan import validate_plan
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = full_fixture
    changed = copy.deepcopy(plan)
    changed["environments"]["second"] = copy.deepcopy(plan["environments"]["py"])
    if omission == "single-component":
        for task in plan["tasks"]:
            if task["component"] == "pine2ast":
                continue
            second = copy.deepcopy(task)
            second["environment"] = "second"
            second["id"] = second["component"] + "@second"
            changed["tasks"].append(second)
    changed = reseal(changed)
    validate_plan(changed, expected_hash=changed["content_hash"])
    with pytest.raises(ValueError, match="mandatory interpreter missing"):
        run_stabilization_gate(host, changed, evidence,
            expected_plan_hash=changed["content_hash"], run_id=packet["run_id"])


@pytest.mark.parametrize("field", ["plan_hash", "candidate_hash", "run_id"])
def test_foreign_historical_packet_rejected(full_fixture, field):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed[field] = "historical"
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="historical/stale"):
            replay(full_fixture)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize(
    "owner", ["foundation", "coverage", "frontend", "packages", "test-performance"]
)
def test_joint_resealing_does_not_replace_raw_owner_evidence(full_fixture, owner):
    from openpine.verification.execution_identity import hash_file

    _, _, evidence, packet = full_fixture
    replacements = {}

    def replace(path, value):
        replacements[path] = path.read_bytes()
        path.write_text(json.dumps(value))

    changed = copy.deepcopy(packet)
    if owner == "foundation":
        path = evidence / changed["gates"][owner]["environments"]["py"] / "stage1.json"
        receipt = read_json(path)
        receipt["source_pins"]["pine2ast"] = "0" * 40
        replace(path, reseal(receipt))
    elif owner == "coverage":
        folder = evidence / next(iter(changed["gates"][owner]["tasks"].values()))
        data = read_json(folder / "coverage.json")
        data["totals"]["percent_covered"] = 999
        replace(folder / "coverage.json", data)
        receipt = read_json(folder / "receipt.json")
        receipt["json"]["sha256"] = hash_file(folder / "coverage.json")
        replace(folder / "receipt.json", reseal(receipt))
    elif owner == "frontend":
        descriptor = changed["gates"][owner]["tests"]
        path = evidence / descriptor["path"]
        data = read_json(path)
        data["testResults"][0]["assertionResults"][0]["fullName"] = "different test"
        replace(path, data)
        descriptor["sha256"] = hash_file(path)
        command_descriptor = changed["gates"][owner]["commands"][-1]
        command_path = evidence / command_descriptor["path"]
        command = read_json(command_path)
        command["artifacts"]["tests"] = copy.deepcopy(descriptor)
        replace(command_path, reseal(command))
        command_descriptor["sha256"] = hash_file(command_path)
    elif owner == "packages":
        package = next(iter(changed["gates"][owner].values()))["normal"]
        path = evidence / package["probe"]["path"]
        data = read_json(path)
        data["components"]["openpine"]["origin"] = str(evidence / "plan.json")
        replace(path, data)
        package["probe"]["sha256"] = hash_file(path)
        command_path = path.parent / "command.json"
        command = read_json(command_path)
        command["files"]["stdout.log"] = hash_file(path)
        replace(command_path, reseal(command))
        for desc in package["commands"]:
            if desc["path"] == command_path.relative_to(evidence).as_posix():
                desc["sha256"] = hash_file(command_path)
    else:
        samples = changed["gates"][owner]["after"]
        samples[:] = [copy.deepcopy(samples[0]) for _ in range(5)]
    path = evidence / "stabilization-inputs.json"
    replace(path, changed)
    try:
        current = replay(full_fixture)
        assert not current["ok"], current
        assert current["stabilization"]["gates"][owner]["status"] == "blocked", current
    finally:
        for path, original in replacements.items():
            path.write_bytes(original)


def test_stale_source_and_resealed_plan_cannot_change_expected_identity(full_fixture):
    from openpine.verification.stage_gate import run_stabilization_gate

    host, plan, evidence, packet = full_fixture
    source = host / "test_minimal.py"
    original = source.read_bytes()
    try:
        source.write_bytes(original + b"\n# changed candidate\n")
        with pytest.raises(ValueError, match="candidate is stale"):
            replay(full_fixture)
        changed = copy.deepcopy(plan)
        from openpine.verification.execution_identity import source_snapshot

        changed["source"] = source_snapshot(
            {n: Path(p) for n, p in plan["roots"].items()}
        )
        with pytest.raises(ValueError, match="plan identity mismatch"):
            run_stabilization_gate(
                host,
                reseal(changed),
                evidence,
                expected_plan_hash=plan["content_hash"],
                run_id=packet["run_id"],
            )
    finally:
        source.write_bytes(original)


def test_false_full_stage2_flag_and_cli_reader_agreement(full_fixture, tmp_path):
    from openpine.verification.__main__ import main
    from openpine.verification.stage_gate import current_views

    host, plan, evidence, packet = full_fixture
    current = replay(full_fixture)
    forged = copy.deepcopy(current)
    forged["full_stage2_accepted"] = True
    with pytest.raises(ValueError, match="not full Stage 2"):
        current_views(reseal(forged))
    common = [
        "--host-root",
        str(host),
        "--plan",
        str(evidence / "plan.json"),
        "--expected-plan-hash",
        plan["content_hash"],
        "--evidence",
        str(evidence),
        "--run-id",
        packet["run_id"],
    ]
    for view in ("current", "progress", "remainder", "summary"):
        output = tmp_path / (view + ".json")
        assert (
            main(["test-current", *common, "--view", view, "--output", str(output)])
            == 0
        )
        data = read_json(output)
        assert data["candidate_hash"] == current["candidate_hash"]
        assert data["plan_hash"] == current["plan_hash"]
    saved = tmp_path / "forged.json"
    saved.write_text(json.dumps(reseal(forged)))
    with pytest.raises(ValueError, match="saved current verdict differs"):
        main(
            [
                "test-current",
                *common,
                "--saved-current",
                str(saved),
                "--output",
                str(tmp_path / "rejected.json"),
            ]
        )


@pytest.mark.parametrize("owner", ["foundation", "coverage", "packages"])
def test_missing_owner_interpreter_cannot_be_accepted(full_fixture, owner):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    if owner == "foundation":
        changed["gates"][owner]["environments"] = {}
    elif owner == "coverage":
        changed["gates"][owner]["tasks"].pop(
            next(iter(changed["gates"][owner]["tasks"]))
        )
    else:
        changed["gates"][owner] = {}
    path = evidence / "stabilization-inputs.json"
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        assert current["stabilization"]["gates"][owner]["status"] == "blocked"
        assert not current["ok"]
    finally:
        path.write_bytes(original)


def test_resealed_campaign_and_execution_command_interpreter_rejected(full_fixture):
    _, _, evidence, _ = full_fixture
    campaign = evidence / "run-0"
    run = read_json(campaign / "run.json")
    attempt = run["attempts"][0]
    execution = (
        campaign
        / attempt["task"]
        / attempt["shard"]
        / attempt["attempt_id"]
        / "execution.json"
    )
    original_run = (campaign / "run.json").read_bytes()
    original_execution = execution.read_bytes()
    try:
        attempt["argv"][0] = "/foreign/python"
        execution.write_text(json.dumps(attempt))
        (campaign / "run.json").write_text(json.dumps(reseal(run)))
        current = replay(full_fixture)
        assert not current["ok"]
        assert all(
            row["status"] == "blocked"
            for row in current["stabilization"]["gates"].values()
        )
        assert any(
            "interpreter/cwd" in error
            for row in current["stabilization"]["gates"].values()
            for error in row["errors"]
        )
    finally:
        execution.write_bytes(original_execution)
        (campaign / "run.json").write_bytes(original_run)


def test_tampered_installed_resource_is_rejected_against_frozen_wheel(full_fixture):
    _, _, evidence, packet = full_fixture
    package = next(iter(packet["gates"]["packages"].values()))["normal"]
    probe = read_json(evidence / package["probe"]["path"])
    component = probe["components"]["openpine"]
    relative = next(
        name for name in component["files"] if name.endswith("fixture-resource.json")
    )
    path = Path(probe["prefix"]) / relative
    original = path.read_bytes()
    try:
        path.write_text('{"value":999}')
        current = replay(full_fixture)
        assert not current["ok"]
        assert current["stabilization"]["gates"]["packages"]["status"] == "blocked"
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize('gate,slot', [('frontend', 'attempt'), ('packages', 'package_attempt')])
@pytest.mark.parametrize('relocated', [False, True])
def test_owner_evidence_subroot_is_bound_locator(tmp_path, gate, slot, relocated):
    from openpine.verification import stage_gate
    _owner_evidence_root = getattr(stage_gate, '_owner_evidence_root', None)
    assert callable(_owner_evidence_root), 'common reader lacks bound evidence-subroot support'

    attempt = tmp_path / 'owner'
    attempt.mkdir()
    historical = tmp_path / 'old' if relocated else attempt
    plan = {'owner_launch': {'paths': {slot: str(historical)}}}
    entry = {'evidence_subroot': 'owner', 'original': {'path': 'raw.json', 'sha256': 'unchanged'}}
    frozen = copy.deepcopy(entry)
    mappings = {str(historical): str(attempt)} if relocated else None
    root, supplied = _owner_evidence_root(plan, tmp_path, entry, gate, replay_paths=mappings)
    assert root == attempt and supplied == {'original': entry['original']}
    assert entry == frozen


@pytest.mark.parametrize('mutation', ['absolute', 'traversal', 'symlink', 'foreign', 'missing-launch', 'missing-mapping', 'empty', 'non-string'])
def test_owner_evidence_subroot_rejects_unbound_locations(tmp_path, mutation):
    from openpine.verification import stage_gate
    _owner_evidence_root = getattr(stage_gate, '_owner_evidence_root', None)
    assert callable(_owner_evidence_root), 'common reader lacks bound evidence-subroot support'

    attempt = tmp_path / 'owner'
    attempt.mkdir()
    plan = {'owner_launch': {'paths': {'attempt': str(attempt)}}}
    entry = {'evidence_subroot': 'owner'}
    mappings = None
    if mutation == 'absolute':
        entry['evidence_subroot'] = str(attempt)
    elif mutation == 'traversal':
        entry['evidence_subroot'] = 'owner/../owner'
    elif mutation == 'symlink':
        (tmp_path / 'link').symlink_to(attempt, target_is_directory=True)
        entry['evidence_subroot'] = 'link'
    elif mutation == 'foreign':
        (tmp_path / 'foreign').mkdir()
        entry['evidence_subroot'] = 'foreign'
    elif mutation == 'missing-launch':
        plan = {}
    elif mutation == 'missing-mapping':
        mappings = {}
    elif mutation == 'empty':
        entry['evidence_subroot'] = ''
    else:
        entry['evidence_subroot'] = 42
    with pytest.raises((ValueError, OSError, KeyError)):
        _owner_evidence_root(plan, tmp_path, entry, 'frontend', replay_paths=mappings)


def test_owner_evidence_subroot_legacy_flat_is_unchanged(tmp_path):
    from openpine.verification import stage_gate
    _owner_evidence_root = getattr(stage_gate, '_owner_evidence_root', None)
    assert callable(_owner_evidence_root), 'common reader lacks bound evidence-subroot support'

    entry = {'commands': [], 'tests': {'path': 'raw.json'}}
    assert _owner_evidence_root({}, tmp_path, entry, 'frontend') == (tmp_path, entry)


def test_owner_evidence_subroot_preserves_real_frontend_receipts(full_fixture):
    from openpine.verification import stage_gate
    _owner_evidence_root = getattr(stage_gate, '_owner_evidence_root', None)
    assert callable(_owner_evidence_root), 'common reader lacks bound evidence-subroot support'
    from openpine.verification.stabilization_evidence import verify_frontend

    host, plan, evidence, packet = full_fixture
    entry = packet['gates']['frontend']
    spec = read_json(host / 'verification/execution-policy.json')['stabilization']['frontend']
    frozen = {evidence / d['path']: (evidence / d['path']).read_bytes() for d in entry['commands']}
    expected = verify_frontend(plan, evidence, entry, spec)
    # Locator-only boundary test; do not change the executed plan or raw receipts.
    locator_plan = {'owner_launch': {'paths': {'attempt': str(evidence)}}}
    root, supplied = _owner_evidence_root(locator_plan, evidence.parent,
                                         {**entry, 'evidence_subroot': evidence.name}, 'frontend')
    assert supplied == entry
    assert verify_frontend(plan, root, supplied, spec) == expected
    assert {p: p.read_bytes() for p in frozen} == frozen


@pytest.mark.parametrize('gate', ['frontend', 'packages'])
def test_common_reader_rejects_subroot_without_frozen_launch(full_fixture, gate):
    _, _, evidence, packet = full_fixture
    changed = copy.deepcopy(packet)
    changed['gates'][gate]['evidence_subroot'] = evidence.name
    path = evidence / 'stabilization-inputs.json'
    original = path.read_bytes()
    try:
        path.write_text(json.dumps(changed))
        current = replay(full_fixture)
        row = current['stabilization']['gates'][gate]
        assert row['status'] == 'blocked' and 'frozen launch path' in row['errors'][0]
        assert not current['ok'] and not current['full_stage2_accepted']
    finally:
        path.write_bytes(original)


def test_common_reader_accepts_frozen_namespaces_and_checked_archive(tmp_path):
    import shutil
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.stage_gate import run_stabilization_gate
    from rc6_tests.stabilization_fixture import build_fixture

    original = tmp_path/'original'
    original.mkdir()
    host, plan, evidence, packet = build_fixture(original, portable=True, owner_namespaces=True)
    expected = replay((host, plan, evidence, packet))
    assert expected['ok'] and not expected['full_stage2_accepted']
    # Transport inventory contains installed regular files, not executable aliases.
    primaries = {p.relative_to(evidence).as_posix(): p.read_bytes() for p in evidence.rglob('*') if p.is_file() and not p.is_symlink()}
    composed = copy.deepcopy(packet)
    prefix = evidence.relative_to(original).as_posix() + '/'
    composed['campaign'] = prefix + packet['campaign']
    for gate, key in [('foundation', 'environments'), ('coverage', 'tasks')]:
        composed['gates'][gate][key] = {k: prefix + v for k, v in packet['gates'][gate][key].items()}
    for side in ['before', 'after']:
        for row in composed['gates']['test-performance'][side]:
            row['campaign'] = prefix + row['campaign']
            row['plan']['path'] = prefix + row['plan']['path']
    for gate in ['frontend', 'packages']:
        composed['gates'][gate]['evidence_subroot'] = prefix.rstrip('/')
    (original/'stabilization-inputs.json').write_text(json.dumps(composed))
    kwargs = {'expected_plan_hash': plan['content_hash'], 'run_id': packet['run_id']}
    assert run_stabilization_gate(host, plan, original, **kwargs) == expected
    assert {p.relative_to(evidence).as_posix(): p.read_bytes() for p in evidence.rglob('*') if p.is_file() and not p.is_symlink()} == primaries

    archive = tmp_path/'archive'
    shutil.copytree(original, archive, ignore=lambda directory, names: [n for n in names if (Path(directory)/n).is_symlink()])
    roots = {name: archive/Path(path).relative_to(original) for name, path in plan['roots'].items()}
    binding = make_binding(plan, roots, {name: env['executable'] for name, env in plan['environments'].items()})
    binding.pop('content_hash')
    binding['owner_paths'] = {str(original): str(archive)}
    binding = seal(binding)
    shutil.rmtree(original)
    assert run_stabilization_gate(roots['openpine'], plan, archive, binding=binding, **kwargs) == expected
    moved = archive/evidence.relative_to(original)
    assert {p.relative_to(moved).as_posix(): p.read_bytes() for p in moved.rglob('*') if p.is_file()} == primaries
    incomplete = {k: v for k, v in binding.items() if k != 'content_hash'}
    incomplete['owner_paths'] = {}
    blocked = run_stabilization_gate(roots['openpine'], plan, archive, binding=seal(incomplete), **kwargs)
    assert all(blocked['stabilization']['gates'][gate]['status'] == 'blocked' for gate in ['frontend', 'packages'])
    assert not blocked['ok'] and not blocked['full_stage2_accepted']
