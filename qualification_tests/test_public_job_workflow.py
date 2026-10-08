"""Exercise the real driver shell while its command output remains private."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("driver_status", [0, 7, 130])
def test_real_workflow_shell_preserves_driver_status_without_leaking_output(tmp_path, driver_status):
    workflow = yaml.safe_load((ROOT / ".github/workflows/rc6-preparation.yml").read_text())
    steps = workflow["jobs"]["protected-qualification"]["steps"]
    driver = next(step for step in steps if step.get("id") == "qualification")
    public = next(step for step in steps if step.get("id") == "public_outcome")
    assert "always()" in public["if"] and "continue-on-error" not in driver
    executable = tmp_path / "bin/python"
    executable.parent.mkdir()
    executable.write_text('#!/bin/bash\necho private-driver-canary\necho private-stderr-canary >&2\nexit "$TEST_DRIVER_STATUS"\n')
    executable.chmod(0o700)
    env = {"PATH": str(executable.parent) + ":/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path),
           "GITHUB_WORKSPACE": str(ROOT), "APPROVED_SHA": "a" * 40,
           "QUALIFICATION_PUBLIC": "true", "TEST_DRIVER_STATUS": str(driver_status)}
    completed = subprocess.run(["/bin/bash", "-c", driver["run"]], env=env, cwd=tmp_path,  # noqa: S603 -- committed driver shell; python is a local no-op double
                               capture_output=True, text=True, check=False)
    assert completed.returncode == driver_status
    assert "canary" not in completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert ("checks completed" if driver_status == 0 else "checks failed") in completed.stdout
    private = tmp_path / "run04-qualified-driver.log"
    assert private.read_text() == "private-driver-canary\nprivate-stderr-canary\n"
    assert private.stat().st_mode & 0o777 == 0o600
    assert not os.path.exists(tmp_path / "public")
