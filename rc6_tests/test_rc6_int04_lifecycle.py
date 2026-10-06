"""Actual process and filesystem boundary regressions; no product acceptance credit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import psutil
import pytest

from openpine.verification.execution_binding import checked_locations, make_binding
from openpine.verification.execution_identity import hash_file
from openpine.verification.execution_owner_launch import freeze_owner_launch, resolve_owner_policy
from openpine.verification.execution_process import run_logged
from rc6_tests.test_rc6_execution_platform import tiny_plan
from rc6_tests.test_rc6_owner_producer_launch import locator_policy


def copied_tool(tmp_path):
    tool = tmp_path / "python-copy"
    shutil.copyfile(Path(sys.executable).resolve(), tool)
    tool.chmod(0o700)
    return tool


def test_owner_freeze_rejects_regular_nonexecutable_bytes(tmp_path):
    tool = copied_tool(tmp_path)
    tool.chmod(0o600)
    with pytest.raises(ValueError, match="executable"):
        freeze_owner_launch(
            locator_policy(), {"attempt": str(tmp_path / "attempt"), "python": str(tool)}
        )


def test_owner_live_rejects_removed_executable_bit_but_historical_replay_stays_portable(tmp_path):
    tool = copied_tool(tmp_path)
    policy = locator_policy()
    launch = freeze_owner_launch(
        policy, {"attempt": str(tmp_path / "attempt"), "python": str(tool)}
    )
    before = hash_file(tool)
    tool.chmod(0o600)
    assert hash_file(tool) == before
    assert resolve_owner_policy(policy, launch)
    with pytest.raises(ValueError, match="executable"):
        resolve_owner_policy(policy, launch, check_live=True)


def test_bound_interpreter_rejects_removed_executable_bit(tmp_path):
    plan, _ = tiny_plan(tmp_path)
    tool = copied_tool(tmp_path)
    roots = {name: Path(value) for name, value in plan["roots"].items()}
    binding = make_binding(plan, roots, {"py": str(tool)})
    tool.chmod(0o600)
    with pytest.raises(ValueError, match="executable"):
        checked_locations(plan, binding)


CHILD = """import fcntl,json,os,socket,sys,time
from pathlib import Path
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
lock=open(sys.argv[1]+'.lock','w');fcntl.flock(lock,fcntl.LOCK_EX)
Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'port':s.getsockname()[1],'lock':lock.name}))
time.sleep(60)
"""
PARENT = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],start_new_session=True)
while not Path(sys.argv[2]).is_file():time.sleep(.01)
time.sleep(60) if sys.argv[3]=='timeout' else None
"""


def live(pid):
    try:
        return psutil.Process(pid).status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}
    except psutil.NoSuchProcess:
        return False


def dispose_test_child(pid):
    if live(pid):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while live(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not live(pid), "test-owned child could not be removed"


def resources_closed(witness):
    import fcntl

    with socket.socket() as connection:
        connection.settimeout(0.2)
        assert connection.connect_ex(("127.0.0.1", witness["port"])) != 0
    with open(witness["lock"], "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("outcome", ["timeout", "exit"])
def test_actual_detached_family_and_socket_removed_after_command(tmp_path, outcome):
    child = tmp_path / "child.json"
    output = tmp_path / "command"
    report = run_logged(
        [sys.executable, "-B", "-c", PARENT, CHILD, str(child), outcome],
        cwd=tmp_path,
        output=output,
        env=dict(os.environ),
        timeout=2 if outcome == "timeout" else 5,
    )
    witness = json.loads(child.read_text())
    try:
        assert not live(witness["pid"]), "detached grandchild survived command boundary"
        resources_closed(witness)
        assert report["status"] == ("timeout" if outcome == "timeout" else "failed")
        assert report["ok"] is False
        assert (output / "stdout.log").is_file() and (output / "stderr.log").is_file()
        assert (output / "command.json").is_file()
        family = json.loads((output / "process-family.json").read_text())
        assert family["cleanup_verified"] is True and family["surviving_processes"] == []
        assert witness["pid"] in {p["pid"] for p in family["observed_family"]}
    finally:
        dispose_test_child(witness["pid"])


def test_actual_auxiliary_tool_mode_drift_is_retained_failure(tmp_path):
    tool = tmp_path / "auxiliary"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o700)
    before = hash_file(tool)
    output = tmp_path / "command"
    report = run_logged(
        [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path;import sys;Path(sys.argv[1]).chmod(0o600)",
            str(tool),
        ],
        cwd=tmp_path,
        output=output,
        env=dict(os.environ),
        inputs={
            "owner-tool:auxiliary": {
                "path": str(tool),
                "sha256": before,
                "declared_path": str(tool),
            }
        },
    )
    assert hash_file(tool) == before
    assert report["ok"] is False and report["status"] == "failed"
    assert "executable" in report["error"]
    assert (output / "command.json").is_file() and (output / "input-0.bin").is_file()


def test_actual_controller_crash_cleans_own_family_preserves_concurrent_neighbour(tmp_path):
    """Two independent commands; genuine campaign/protected qualification remains separate."""
    controller = """import os,sys
from pathlib import Path
from openpine.verification.execution_process import run_logged
run_logged([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],'timeout'],cwd=Path(sys.argv[3]).parent,output=Path(sys.argv[4]),env=dict(os.environ),timeout=45)
"""
    controls = []
    witnesses = []
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    try:
        for index in range(2):
            root = tmp_path / f"owner-{index}"
            root.mkdir()
            path = root / "child.json"
            process = subprocess.Popen(  # noqa: S603 -- fixed interpreter, test code and isolated owned paths
                [
                    sys.executable,
                    "-B",
                    "-c",
                    controller,
                    PARENT,
                    CHILD,
                    str(path),
                    str(root / "command"),
                ],
                env=env,
                start_new_session=True,
            )
            controls.append(process)
            deadline = time.monotonic() + 8
            while not path.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.02)
            assert path.exists(), "real controller did not create its child witness"
            witnesses.append(json.loads(path.read_text()))
        controls[0].kill()
        controls[0].wait(timeout=3)
        family_path = tmp_path / "owner-0/command/process-family.json"
        deadline = time.monotonic() + 5
        while not family_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert family_path.exists()
        family = json.loads(family_path.read_text())
        assert family["reason"] in {"cancelled", "controller-crashed"}
        assert family["cleanup_verified"] is True and family["surviving_processes"] == []
        assert not live(witnesses[0]["pid"])
        resources_closed(witnesses[0])
        assert live(witnesses[1]["pid"]) and controls[1].poll() is None
        with socket.socket() as connection:
            connection.settimeout(0.2)
            assert connection.connect_ex(("127.0.0.1", witnesses[1]["port"])) == 0
        assert (tmp_path / "owner-0/command/stdout.log").is_file()
        assert not (tmp_path / "owner-0/command/command.json").exists(), (
            "crashed controller published incomplete acceptance"
        )
    finally:
        for process in controls:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        deadline = time.monotonic() + 3
        while any(live(w["pid"]) for w in witnesses) and time.monotonic() < deadline:
            time.sleep(0.02)
        for witness in witnesses:
            dispose_test_child(witness["pid"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-descriptor",
        "missing-file",
        "cleanup-false",
        "survivor",
        "wrong-argv",
        "wrong-cwd",
        "empty-family",
        "signal-neighbour",
        "signal-wrong-create-time",
        "signal-invalid-number",
        "signal-wrong-type",
        "signal-malformed-row",
        "signal-list-wrong-type",
        "overlapping-role-pids",
        "observed-controller",
    ],
)
def test_real_command_replay_rejects_resealed_family_evidence_mutations(tmp_path, mutation):
    from openpine.verification.identity import seal
    from openpine.verification.stabilization_evidence import verify_command

    output = tmp_path / "command"
    argv = [sys.executable, "-B", "-c", "print('actual baseline')"]
    receipt = run_logged(argv, cwd=tmp_path, output=output, env=dict(os.environ))
    expected = {"argv": argv, "cwd": str(tmp_path)}
    assert verify_command(output, expected) == receipt
    family_path = output / "process-family.json"
    family = json.loads(family_path.read_text())
    if mutation == "missing-descriptor":
        receipt["files"].pop("process-family.json")
    elif mutation == "missing-file":
        family_path.unlink()
    else:
        if mutation == "cleanup-false":
            family["cleanup_verified"] = False
        elif mutation == "survivor":
            family["surviving_processes"] = [{"pid": os.getpid(), "status": "running"}]
        elif mutation == "wrong-argv":
            family["argv"] = [sys.executable, "-c", "pass"]
        elif mutation == "wrong-cwd":
            family["cwd"] = str(tmp_path.parent)
        elif mutation == "empty-family":
            family["observed_family"] = []
        elif mutation == "overlapping-role-pids":
            family["supervisor_pid"] = family["command_pid"]
        elif mutation == "observed-controller":
            family["observed_family"].append(
                {"pid": family["controller_pid"], "create_time": 1.0, "name": "neighbour"}
            )
        elif mutation == "signal-list-wrong-type":
            family["cleanup_signals"] = {}
        else:
            root = next(p for p in family["observed_family"] if p["pid"] == family["command_pid"])
            row = {"pid": root["pid"], "create_time": root["create_time"], "signal": signal.SIGTERM}
            if mutation == "signal-neighbour":
                row["pid"] = os.getpid()
            elif mutation == "signal-wrong-create-time":
                row["create_time"] += 1
            elif mutation == "signal-invalid-number":
                row["signal"] = signal.SIGUSR1
            elif mutation == "signal-wrong-type":
                row["signal"] = True
            elif mutation == "signal-malformed-row":
                row["extra"] = "unbound"
            family["cleanup_signals"] = [row]
        family_path.write_text(json.dumps(family))
        receipt["files"]["process-family.json"] = hash_file(family_path)
    receipt.pop("content_hash")
    (output / "command.json").write_text(json.dumps(seal(receipt)))
    with pytest.raises((ValueError, OSError)):
        verify_command(output, expected)


def test_actual_observation_denial_still_removes_direct_and_adopted_children(tmp_path):
    child = tmp_path / "child.json"
    family = tmp_path / "process-family.json"
    code = """import os,sys,psutil
from pathlib import Path
from openpine.verification.execution_process import _supervise_family
original=psutil.Process.children
def denied(self,*args,**kwargs):
    if Path(sys.argv[3]).exists():raise psutil.AccessDenied(self.pid)
    return original(self,*args,**kwargs)
psutil.Process.children=denied
raise SystemExit(_supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],'timeout'],os.getppid(),Path(sys.argv[4])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(  # noqa: S603 -- fixed observation-fault probe, no shell
        [sys.executable, "-B", "-c", code, PARENT, CHILD, str(child), str(family)],
        env=env,
        timeout=10,
    )
    witness = json.loads(child.read_text())
    try:
        assert result.returncode == 70
        report = json.loads(family.read_text())
        assert report["observation_errors"] and report["cleanup_verified"] is False
        assert report["surviving_processes"] == []
        assert not live(witness["pid"])
        resources_closed(witness)
    finally:
        dispose_test_child(witness["pid"])


@pytest.mark.parametrize(
    "operation,error_kind",
    [("status", "AccessDenied"), ("status", "OSError"),
     ("create_time", "AccessDenied"), ("name", "OSError")],
)
def test_actual_status_and_fallback_metadata_denial_retains_cleanup(tmp_path, operation, error_kind):
    child = tmp_path / "child.json"
    family = tmp_path / "process-family.json"
    code = """import os,sys,psutil
from pathlib import Path
from openpine.verification.execution_process import _supervise_family
operation,error_kind=sys.argv[5:7]
original=getattr(psutil.Process,operation)
original_children=psutil.Process.children
def denied(self,*args,**kwargs):
    if Path(sys.argv[3]).exists():
        if error_kind=='AccessDenied':raise psutil.AccessDenied(self.pid)
        raise OSError('actual owned process observation denied')
    return original(self,*args,**kwargs)
def denied_children(self,*args,**kwargs):
    if Path(sys.argv[3]).exists() and operation!='status':raise psutil.AccessDenied(self.pid)
    return original_children(self,*args,**kwargs)
setattr(psutil.Process,operation,denied)
psutil.Process.children=denied_children
raise SystemExit(_supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],'exit'],os.getppid(),Path(sys.argv[4])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    diagnostic = tmp_path / "probe.log"
    ready = tmp_path / 'neighbour-ready'
    neighbour = subprocess.Popen(  # noqa: S603 -- isolated test-owned neighbour with kernel comm bytes
        [sys.executable, '-B', '-c',
         "import ctypes,sys,time;from pathlib import Path;"
         "assert ctypes.CDLL(None).prctl(15,ctypes.c_char_p(bytes([255])+b'int04-other'),0,0,0)==0;"
         "Path(sys.argv[1]).write_bytes(b'ready');time.sleep(30)", str(ready)],
        start_new_session=True,
    )
    witness = None
    try:
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline and neighbour.poll() is None:
            time.sleep(.01)
        assert ready.exists()
        with diagnostic.open("wb") as log:
            result = subprocess.run(  # noqa: S603 -- fixed owned observation-fault probe and isolated paths
                [sys.executable, "-B", "-c", code, PARENT, CHILD, str(child), str(family), operation, error_kind],
                env=env, timeout=10, stdout=log, stderr=subprocess.STDOUT,
            )
        witness = json.loads(child.read_text())
        assert result.returncode == 70, diagnostic.read_text()
        report = json.loads(family.read_text())
        assert report["observation_errors"] and report["cleanup_verified"] is False
        assert report["surviving_processes"] == []
        assert not live(witness["pid"])
        resources_closed(witness)
        assert neighbour.poll() is None, 'unrelated process was affected by owned cleanup'
    finally:
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        if witness is not None:
            dispose_test_child(witness["pid"])
        if neighbour.poll() is None:
            neighbour.kill()
        neighbour.wait(timeout=3)


@pytest.mark.parametrize("payload", [b"invalid executable format\n", b"#!/missing/int04-interpreter\n"])
def test_actual_inner_exec_failure_retains_infrastructure_classification(tmp_path, payload, monkeypatch):
    tool = tmp_path / "invalid-executable"
    tool.write_bytes(payload)
    tool.chmod(0o700)
    output = tmp_path / "command"
    report = run_logged([str(tool)], cwd=tmp_path, output=output, env=dict(os.environ))
    assert report["status"] == "infrastructure_error" and report["ok"] is False
    assert report["error"]
    family = json.loads((output / "process-family.json").read_text())
    assert family["reason"] == "launch-error" and family["command_pid"] is None
    assert family["observation_errors"] and family["surviving_processes"] == []
    assert (output / "command.json").is_file() and (output / "stderr.log").is_file()
    from openpine.verification import execution_campaign
    from openpine.verification.execution_process import start_declared_process

    plan, path = tiny_plan(tmp_path)
    # Fault after normal binding validation, then perform the real failing exec.
    # No fixture environment or source identity is changed to bless invalid bytes.
    def failing_exec(argv, **kwargs):
        return start_declared_process([str(tool), *argv[1:]], **kwargs)

    monkeypatch.setattr(execution_campaign, 'start_declared_process', failing_exec)
    campaign = tmp_path / 'campaign'
    run = execution_campaign.run_campaign(plan, path, campaign, run_id='actual-inner-exec-failure')
    assert run['attempts'][0]['status'] == 'infrastructure_error'
    assert run['attempts'][0]['error']
    family = json.loads((campaign / 'tiny@py/s000/a001/process-family.json').read_text())
    assert family['reason'] == 'launch-error' and family['command_pid'] is None
    assert (campaign / 'tiny@py/s000/a001/execution.json').is_file()


def test_actual_disk_floor_refuses_dispatch_and_preserves_neighbour(tmp_path):
    from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
    from openpine.verification.execution_identity import write_once_json
    from openpine.verification.identity import seal

    plan, _ = tiny_plan(tmp_path)
    stat = os.statvfs(tmp_path)
    plan["disk_free_guard"] = {"minimum_free_bytes": stat.f_blocks * stat.f_frsize + 1}
    plan.pop("content_hash")
    plan = seal(plan)
    path = tmp_path / "disk-plan.json"
    write_once_json(path, plan)
    neighbour = tmp_path / "neighbour-checkpoint"
    neighbour.write_bytes(b"user-owned checkpoint")
    output = tmp_path / "disk-run"
    run = run_campaign(plan, path, output, run_id="actual-disk-floor")
    assert run["attempts"] == [] and run["disk_free_guard"]["tripped"] is True
    assert run["disk_free_guard"]["samples"] > 0
    assert run["errors"] and (output / "run.json").is_file()
    assert not aggregate_campaign(
        plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"]
    )["ok"]
    assert neighbour.read_bytes() == b"user-owned checkpoint"


def test_two_actual_campaigns_crash_and_cancel_preserve_neighbour_and_raw_resources(tmp_path):
    """Independent coordinators and relocated identical inputs; actual tiny pytest shards."""
    body = f"""import subprocess,sys,time
from pathlib import Path
def test_value(tmp_path):
    child=tmp_path/'child.json'
    subprocess.Popen([sys.executable,'-B','-c',{CHILD!r},str(child)],start_new_session=True)
    while not child.exists():time.sleep(.01)
    time.sleep(60)
"""
    controller = """import sys
from pathlib import Path
from openpine.verification.identity import read_json
from openpine.verification.execution_campaign import run_campaign
path=Path(sys.argv[1]);run_campaign(read_json(path),path,Path(sys.argv[2]),run_id=sys.argv[3],jobs=1)
"""
    controls = []
    witnesses = []
    outputs = []
    source_hashes = []
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    try:
        for index in range(2):
            location = tmp_path / f"placement-{index}"
            location.mkdir()
            plan, path = tiny_plan(location, bodies={"test_a.py": body}, shards=1, timeout=30)
            source_hashes.append(plan["source"]["content_hash"])
            output = tmp_path / f"campaign-{index}"
            outputs.append(output)
            controls.append(
                subprocess.Popen(  # noqa: S603 -- real owned campaign coordinator and fixed test argv
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        controller,
                        str(path),
                        str(output),
                        f"actual-concurrent-{index}",
                    ],
                    env=env,
                    start_new_session=True,
                )
            )
            deadline = time.monotonic() + 12
            files = []
            while time.monotonic() < deadline and controls[-1].poll() is None:
                files = list(output.glob("tiny@py/s000/a001/private/pytest/test_value*/child.json"))
                if files:
                    break
                time.sleep(0.02)
            assert files, "actual campaign did not launch its resource witness"
            witnesses.append(json.loads(files[0].read_text()))
        assert source_hashes[0] == source_hashes[1]
        controls[0].kill()
        controls[0].wait(timeout=3)
        family_path = outputs[0] / "tiny@py/s000/a001/process-family.json"
        deadline = time.monotonic() + 5
        while not family_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        family = json.loads(family_path.read_text())
        assert family["cleanup_verified"] is True and not family["surviving_processes"]
        assert not live(witnesses[0]["pid"])
        resources_closed(witnesses[0])
        assert live(witnesses[1]["pid"]) and controls[1].poll() is None
        assert not (outputs[0] / "run.json").exists()
        assert not (outputs[0] / "tiny@py/s000/a001/execution.json").exists()
        assert (outputs[0] / "tiny@py/s000/a001/stdout.log").is_file()
        controls[1].send_signal(signal.SIGINT)
        controls[1].wait(timeout=8)
        run = json.loads((outputs[1] / "run.json").read_text())
        assert run["errors"] and run["attempts"][0]["status"] == "cancelled"
        assert not live(witnesses[1]["pid"])
        resources_closed(witnesses[1])
        assert (outputs[1] / "tiny@py/s000/a001/execution.json").is_file()
        assert (outputs[1] / "tiny@py/s000/a001/private").is_dir()
    finally:
        for process in controls:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        deadline = time.monotonic() + 3
        while any(live(w["pid"]) for w in witnesses) and time.monotonic() < deadline:
            time.sleep(0.02)
        for witness in witnesses:
            dispose_test_child(witness["pid"])


def test_actual_memory_budget_cancels_after_real_allocation_preserving_failed_private(tmp_path):
    from openpine.verification.execution_campaign import aggregate_campaign, run_campaign

    body = """import time
def test_value(tmp_path):
    (tmp_path/'allocation-started').write_bytes(b'actual worker allocation')
    payload=bytearray(640*1024*1024)
    assert len(payload)==640*1024*1024
    time.sleep(30)
"""
    plan, path = tiny_plan(tmp_path, bodies={"test_a.py": body}, shards=1, timeout=20)
    output = tmp_path / "memory-run"
    run = run_campaign(plan, path, output, run_id="actual-memory-budget", memory_mib=512)
    assert any("RSS exceeded campaign memory budget" in error for error in run["errors"])
    assert run["attempts"][0]["status"] == "cancelled"
    assert list(output.glob("tiny@py/s000/a001/private/pytest/test_value*/allocation-started"))
    assert (output / "tiny@py/s000/a001/execution.json").is_file()
    family = json.loads((output / "tiny@py/s000/a001/process-family.json").read_text())
    assert family["cleanup_verified"] is True and family["surviving_processes"] == []
    assert not aggregate_campaign(
        plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"]
    )["ok"]
