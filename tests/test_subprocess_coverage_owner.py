"""Child coverage belongs to its real shard, never to a detached acceptance claim."""

from pathlib import Path

import pytest

from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
from openpine.verification.execution_coverage import combine_task_coverage
from openpine.verification.execution_identity import source_snapshot, write_once_json
from openpine.verification.identity import read_json
from rc6_tests.test_rc6_execution_platform import reseal, tiny_plan


@pytest.mark.parametrize("uncovered", [False, True])
def test_child_only_execution_is_retained_and_respects_owner_threshold(tmp_path, uncovered):
    bodies = {
        "test_child.py": "import subprocess, sys\ndef test_value():\n    subprocess.run([sys.executable, '-c', 'from tiny import left; assert left(1)==2'], check=True)\n"
    }
    plan, _ = tiny_plan(tmp_path, bodies=bodies)
    root = Path(plan["roots"]["tiny"])
    (root / "tiny").mkdir()
    code = "def left(x):\n    return x+1\n"
    if uncovered:
        code += "def never_called():\n    return 99\n"
    (root / "tiny/__init__.py").write_text(code)
    (root / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource=["tiny"]\npatch=["subprocess"]\n'
        "[tool.coverage.report]\nfail_under=100\n"
    )
    plan["source"] = source_snapshot({n: Path(p) for n, p in plan["roots"].items()})
    task = plan["tasks"][0]
    task["coverage"] = True
    task["coverage_package"] = "tiny"
    for shard in task["shards"]:
        shard["coverage"] = True
    plan = reseal(plan)
    path = tmp_path / "child-plan.json"
    write_once_json(path, plan)
    output = tmp_path / "run"
    run = run_campaign(plan, path, output, jobs=1, run_id="child-coverage")
    aggregate = aggregate_campaign(
        plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"]
    )
    assert aggregate["pytest_scope_passed"], aggregate
    artifacts = read_json(output / "run.json")["attempts"][0]["artifacts"]
    raw = [v for k, v in artifacts.items() if k.startswith("coverage-process:")]
    assert len(raw) >= 2  # Main pytest and the genuine child are distinct datasets.
    assert "coverage-combine" in artifacts
    result = combine_task_coverage(
        plan, output, "tiny@py", tmp_path / "owner", run_id=run["run_id"]
    )
    assert result["ok"] is (not uncovered), result
    totals = read_json(tmp_path / "owner/coverage.json")["totals"]
    assert (totals["percent_covered"] == 100) is (not uncovered)
    (output / raw[0]["path"]).write_bytes(b"tampered dataset")
    altered = aggregate_campaign(
        plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"]
    )
    assert not altered["pytest_scope_passed"]


@pytest.mark.parametrize("uncovered", [False, True])
def test_isolated_script_coverage_survives_foreign_working_directory(tmp_path, uncovered):
    foreign = tmp_path / "foreign-cwd"
    foreign.mkdir()
    # The child imports no package: an absolute source scope must trace __main__.
    body = "import subprocess, sys\nfrom pathlib import Path\ndef test_value():\n    subprocess.run([sys.executable, '-I', str(Path(__file__).parent/'tiny/runner.py')], cwd=" + repr(str(foreign)) + ", check=True)\n"
    plan, _ = tiny_plan(tmp_path, bodies={"test_script.py": body})
    root = Path(plan["roots"]["tiny"])
    (root / "tiny").mkdir()
    (root / "tiny/__init__.py").write_text("")
    script = "def left(x):\n    return x+1\nassert left(1)==2\n"
    if uncovered:
        script += "def never_called():\n    return 99\n"
    (root / "tiny/runner.py").write_text(script)
    (root / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource=["tiny"]\npatch=["subprocess"]\n'
        '[tool.coverage.report]\nfail_under=100\n')
    plan["source"] = source_snapshot({n: Path(p) for n, p in plan["roots"].items()})
    task = plan["tasks"][0]
    task["coverage"] = True
    task["coverage_package"] = "tiny"
    for shard in task["shards"]:
        shard["coverage"] = True
    plan = reseal(plan)
    path = tmp_path / "foreign-cwd-plan.json"
    write_once_json(path, plan)
    output = tmp_path / "run"
    run = run_campaign(plan, path, output, jobs=1, run_id="foreign-cwd-coverage")
    aggregate = aggregate_campaign(plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"])
    assert aggregate["pytest_scope_passed"], aggregate
    result = combine_task_coverage(plan, output, "tiny@py", tmp_path / "owner", run_id=run["run_id"])
    assert result["ok"] is (not uncovered), result
    totals = read_json(tmp_path / "owner/coverage.json")["totals"]
    assert (totals["percent_covered"] == 100) is (not uncovered)
