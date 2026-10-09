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
        "over-budget-family",
        "duplicate-signal",
        "over-budget-signals",
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
        elif mutation == "over-budget-family":
            from openpine.verification.execution_process import _FAMILY_LEDGER_LIMIT

            family["observed_family"].extend(
                {"pid": 100000000 + index, "create_time": 1.0, "name": "forged history"}
                for index in range(_FAMILY_LEDGER_LIMIT)
            )
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
            if mutation == "duplicate-signal":
                family["cleanup_signals"] *= 2
            elif mutation == "over-budget-signals":
                from openpine.verification.execution_process import _FAMILY_LEDGER_LIMIT

                family["cleanup_signals"] *= 2 * _FAMILY_LEDGER_LIMIT + 1
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
    from openpine.verification.execution_campaign import aggregate_campaign

    body = """import time
def test_value(tmp_path):
    (tmp_path/'allocation-started').write_bytes(b'actual worker allocation')
    payload=bytearray(640*1024*1024)
    assert len(payload)==640*1024*1024
    time.sleep(30)
"""
    plan, path = tiny_plan(tmp_path, bodies={"test_a.py": body}, shards=1, timeout=20)
    output = tmp_path / "memory-run"
    # The memory budget includes its controller. Isolate that controller from
    # the parent pytest process, whose imports/coverage grow across the suite.
    controller = """import json,sys
from pathlib import Path
from openpine.verification.execution_campaign import run_campaign
plan_path=Path(sys.argv[1])
run_campaign(json.loads(plan_path.read_text()), plan_path, Path(sys.argv[2]),
             run_id="actual-memory-budget", memory_mib=512)
"""
    report = run_logged(
        [sys.executable, "-B", "-c", controller, str(path), str(output)],
        cwd=tmp_path,
        output=tmp_path / "memory-controller",
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])),
        timeout=40,
    )
    assert report["ok"] is True
    run = json.loads((output / "run.json").read_text())
    assert any("RSS exceeded campaign memory budget" in error for error in run["errors"])
    assert run["attempts"][0]["status"] == "cancelled"
    assert list(output.glob("tiny@py/s000/a001/private/pytest/test_value*/allocation-started"))
    assert (output / "tiny@py/s000/a001/execution.json").is_file()
    family = json.loads((output / "tiny@py/s000/a001/process-family.json").read_text())
    assert family["cleanup_verified"] is True and family["surviving_processes"] == []
    assert not aggregate_campaign(
        plan, output, expected_plan_hash=plan["content_hash"], expected_run_id=run["run_id"]
    )["ok"]


def test_recycled_enumeration_slot_never_admits_actual_unrelated_process(tmp_path):
    """A stale children-map slot resolves to a newer live non-descendant."""
    child = tmp_path / 'owned-child.json'
    neighbour_path = tmp_path / 'neighbour.json'
    family_path = tmp_path / 'process-family.json'
    parent = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],start_new_session=True)
while not Path(sys.argv[2]).exists() or not Path(sys.argv[3]).exists():time.sleep(.01)
time.sleep(.1)
"""
    code = """import os,sys,psutil,json
from pathlib import Path
from openpine.verification.execution_process import _supervise_family
original=psutil.Process.children
def recycled(self,*args,**kwargs):
    children=original(self,*args,**kwargs)
    path=Path(sys.argv[4])
    if path.exists():children.append(psutil.Process(json.loads(path.read_text())['pid']))
    return children
psutil.Process.children=recycled
raise SystemExit(_supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]],os.getppid(),Path(sys.argv[5])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    controller = None
    neighbour = None
    witness = None
    try:
        with (tmp_path / 'probe.log').open('wb') as log:
            controller = subprocess.Popen(  # noqa: S603 -- deterministic stale-map probe, owned test paths
                [sys.executable, '-B', '-c', code, parent, CHILD, str(child), str(neighbour_path), str(family_path)],
                env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 5
            while not child.exists() and time.monotonic() < deadline and controller.poll() is None:
                time.sleep(.01)
            assert child.exists()
            witness = json.loads(child.read_text())
            # Created after the supervisor: models psutil's newer-than-owner filter.
            neighbour = subprocess.Popen(  # noqa: S603 -- exclusively test-owned newer unrelated process
                [sys.executable, '-B', '-c', 'import time;time.sleep(30)'], start_new_session=True,
            )
            neighbour_path.write_text(json.dumps({'pid': neighbour.pid}))
            controller.wait(timeout=10)
        report = json.loads(family_path.read_text())
        assert neighbour.poll() is None, 'recycled enumeration slot signalled an unrelated process'
        assert neighbour.pid not in {p['pid'] for p in report['observed_family']}
        assert neighbour.pid not in {p['pid'] for p in report['cleanup_signals']}
        assert not live(witness['pid'])
        resources_closed(witness)
    finally:
        if controller is not None:
            if controller.poll() is None:
                controller.kill()
            controller.wait(timeout=3)
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        if witness is not None:
            dispose_test_child(witness['pid'])
        if neighbour is not None:
            if neighbour.poll() is None:
                neighbour.kill()
            neighbour.wait(timeout=3)


def test_successful_final_traversal_omission_reconciles_retained_live_family(tmp_path):
    child = tmp_path / 'child.json'
    observed = tmp_path / 'observed'
    exited = tmp_path / 'ancestor-exiting'
    family_path = tmp_path / 'process-family.json'
    parent = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],start_new_session=True)
while not Path(sys.argv[3]).exists():time.sleep(.01)
Path(sys.argv[4]).write_bytes(b'ancestor exits during traversal')
"""
    code = """import os,sys,psutil,json
from pathlib import Path
from openpine.verification.execution_process import _supervise_family
original=psutil.Process.children
def omitted(self,*args,**kwargs):
    rows=original(self,*args,**kwargs)
    witness=Path(sys.argv[3])
    if witness.exists() and any(p.pid==json.loads(witness.read_text())['pid'] for p in rows):
        Path(sys.argv[4]).write_bytes(b'actually enumerated before ancestor exit')
    return [] if Path(sys.argv[5]).exists() else rows
psutil.Process.children=omitted
raise SystemExit(_supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],sys.argv[5]],os.getppid(),Path(sys.argv[6])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    with (tmp_path / 'probe.log').open('wb') as log:
        result = subprocess.run(  # noqa: S603 -- actual ancestor exit and deterministic successful omission
            [sys.executable, '-B', '-c', code, parent, CHILD, str(child), str(observed), str(exited), str(family_path)],
            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=10,
        )
    witness = json.loads(child.read_text())
    try:
        assert observed.is_file() and exited.is_file()
        assert not live(witness['pid']), 'successful traversal omitted a retained live detached child'
        resources_closed(witness)
        report = json.loads(family_path.read_text())
        assert witness['pid'] in {p['pid'] for p in report['observed_family']}
        assert report['surviving_processes'] == [] and report['cleanup_verified'] is True
        assert result.returncode == 70 and report['reason'] == 'orphan-descendants'
    finally:
        dispose_test_child(witness['pid'])


def test_stable_owned_handle_signal_ignores_recycled_integer_routing(tmp_path, monkeypatch):
    """Real pidfd signal target stays fixed across the modelled check/signal window."""
    from openpine.verification.execution_process import _OwnedHandle

    processes = []
    handle = None
    try:
        for _ in range(2):
            processes.append(subprocess.Popen(  # noqa: S603 -- two exclusively owned real kernel targets
                [sys.executable, '-B', '-c', 'import time;time.sleep(30)'],
            ))
        first, neighbour = processes
        fd = os.pidfd_open(first.pid, 0)
        handle = _OwnedHandle(first.pid, psutil.Process(first.pid).create_time(), 'python', fd)
        before = (handle.pid, handle.create_time)
        routed = []
        original_kill = os.kill

        def recycled_kill(pid, signum):
            routed.append(pid)
            original_kill(neighbour.pid, signum)

        with monkeypatch.context() as patch:
            patch.setattr(os, 'kill', recycled_kill)
            assert before == (first.pid, handle.create_time)
            handle.send_signal(signal.SIGTERM)
        first.wait(timeout=3)
        assert first.returncode == -signal.SIGTERM
        assert neighbour.poll() is None and routed == []
        with pytest.raises(ProcessLookupError):
            handle.send_signal(signal.SIGTERM)
        assert neighbour.poll() is None
    finally:
        if handle is not None:
            handle.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def test_unavailable_pidfd_backend_fails_before_any_child_launch(tmp_path, monkeypatch):
    import errno

    marker = tmp_path / 'child-launched'

    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOSYS, 'pidfd backend unavailable')

    monkeypatch.setattr(os, 'pidfd_open', unavailable)
    output = tmp_path / 'command'
    report = run_logged(
        [sys.executable, '-B', '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]).touch()', str(marker)],
        cwd=tmp_path, output=output, env=dict(os.environ),
    )
    assert report['status'] == 'infrastructure_error' and report['ok'] is False
    assert 'pidfd' in report['error']
    assert not marker.exists() and not (output / 'process-family.json').exists()
    assert (output / 'command.json').is_file()


def test_actual_high_pidfd_liveness_and_signalling_preserve_neighbour(tmp_path):
    """A real retained pidfd above FD_SETSIZE must track its original process."""
    import fcntl

    from openpine.verification.execution_process import _OwnedHandle

    processes = []
    handle = None
    try:
        for _ in range(2):
            processes.append(subprocess.Popen(  # noqa: S603 -- two exclusively owned actual targets
                [sys.executable, '-B', '-c', 'import time;time.sleep(30)'],
            ))
        first, neighbour = processes
        low = os.pidfd_open(first.pid, 0)
        try:
            high = fcntl.fcntl(low, fcntl.F_DUPFD_CLOEXEC, 2048)
        finally:
            os.close(low)
        handle = _OwnedHandle(first.pid, psutil.Process(first.pid).create_time(), 'python', high)
        (tmp_path / 'high-fd-witness.json').write_text(json.dumps({'pid': first.pid, 'fd': high}))
        assert high >= 2048 and handle.alive()
        handle.send_signal(signal.SIGTERM)
        first.wait(timeout=3)
        assert first.returncode == -signal.SIGTERM and not handle.alive()
        assert neighbour.poll() is None
        handle.close()
        assert not handle.alive()
    finally:
        if handle is not None:
            handle.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def test_actual_high_pidfd_lineage_capture_closes_detached_resources(tmp_path):
    """Duplication forces actual admission/cleanup through pidfds above 1024."""
    child = tmp_path / 'child.json'
    family = tmp_path / 'process-family.json'
    fds = tmp_path / 'high-fd-witness.json'
    code = """import fcntl,json,os,sys
from pathlib import Path
from openpine.verification.execution_process import _supervise_family
original=os.pidfd_open
issued=[]
def high_open(pid,flags=0):
    low=original(pid,flags)
    try:high=fcntl.fcntl(low,fcntl.F_DUPFD_CLOEXEC,2048)
    finally:os.close(low)
    issued.append({'pid':pid,'fd':high})
    Path(sys.argv[5]).write_text(json.dumps(issued))
    return high
os.pidfd_open=high_open
raise SystemExit(_supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],'exit'],os.getppid(),Path(sys.argv[4])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    witness = None
    try:
        with (tmp_path / 'probe.log').open('wb') as log:
            result = subprocess.run(  # noqa: S603 -- actual high-fd family in a private probe process
                [sys.executable, '-B', '-c', code, PARENT, CHILD, str(child), str(family), str(fds)],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=10,
            )
        witness = json.loads(child.read_text())
        report = json.loads(family.read_text())
        issued = json.loads(fds.read_text())
        assert all(row['fd'] >= 2048 for row in issued)
        assert witness['pid'] in {row['pid'] for row in issued}
        assert not live(witness['pid']), 'high pidfd prevented detached child cleanup'
        resources_closed(witness)
        assert report['cleanup_verified'] is True and not report['surviving_processes']
        assert not report['observation_errors']
        assert witness['pid'] in {row['pid'] for row in report['observed_family']}
        assert result.returncode == 70 and report['reason'] == 'orphan-descendants'
    finally:
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        if witness is not None:
            dispose_test_child(witness['pid'])


def test_actual_pass_fds_pressure_still_closes_detached_family(tmp_path):
    """Caller-owned descriptors occupy low guardian slots without many tasks."""
    child = tmp_path / 'child.json'
    family = tmp_path / 'process-family.json'
    fds = tmp_path / 'pass-fds-witness.json'
    code = """import json,os,sys,time
from pathlib import Path
from openpine.verification.execution_process import start_declared_process
reserved=[]
try:
    while not reserved or reserved[-1]<1056:reserved.append(os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC))
    process=start_declared_process([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3],'exit'],family_evidence=Path(sys.argv[4]),pass_fds=tuple(reserved))
    visible={int(entry.name) for entry in (Path('/proc')/str(process.pid)/'fd').iterdir()}
    Path(sys.argv[5]).write_text(json.dumps({'first_reserved':reserved[0],'last_reserved':reserved[-1],'count':len(reserved),'guardian_has_all_reserved':set(reserved)<=visible}))
    result=process.wait(timeout=10)
    deadline=time.monotonic()+3
    while not Path(sys.argv[3]).exists() and time.monotonic()<deadline:time.sleep(.01)
    raise SystemExit(result)
finally:
    for fd in reserved:os.close(fd)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    witness = None
    try:
        with (tmp_path / 'probe.log').open('wb') as log:
            result = subprocess.run(  # noqa: S603 -- private coordinator and actual inherited test descriptors
                [sys.executable, '-B', '-c', code, PARENT, CHILD, str(child), str(family), str(fds)],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=15,
            )
        witness = json.loads(child.read_text())
        reservation = json.loads(fds.read_text())
        assert reservation['first_reserved'] == 3 and reservation['last_reserved'] >= 1056
        assert reservation['count'] == reservation['last_reserved'] - 2
        assert reservation['guardian_has_all_reserved'] is True
        report = json.loads(family.read_text())
        assert not live(witness['pid']), 'inherited descriptors prevented actual family cleanup'
        resources_closed(witness)
        assert report['cleanup_verified'] is True and not report['surviving_processes']
        assert not report['observation_errors']
        assert witness['pid'] in {row['pid'] for row in report['observed_family']}
        assert result.returncode == 70 and report['reason'] == 'orphan-descendants'
    finally:
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        if witness is not None:
            dispose_test_child(witness['pid'])


def test_cumulative_family_budget_fails_and_closes_resources_under_sequential_churn(tmp_path):
    """Actual sequential children exceed a small injected ledger, not the live limit."""
    child = tmp_path / 'child.json'
    family = tmp_path / 'process-family.json'
    neighbour = tmp_path / 'neighbour-checkpoint'
    neighbour.write_bytes(b'preserve unrelated evidence')
    parent = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]],start_new_session=True)
while not Path(sys.argv[2]).exists():time.sleep(.01)
for index in range(20):
    subprocess.run([sys.executable,'-B','-c','import time;time.sleep(.08)'],check=True)
"""
    code = """import os,sys
from pathlib import Path
from openpine.verification import execution_process as module
module._FAMILY_LEDGER_LIMIT=8
raise SystemExit(module._supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3]],os.getppid(),Path(sys.argv[4])))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    witness = None
    try:
        with (tmp_path / 'probe.log').open('wb') as log:
            result = subprocess.run(  # noqa: S603 -- exclusively owned sequential budget-pressure probe
                [sys.executable, '-B', '-c', code, parent, CHILD, str(child), str(family)],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=15,
            )
        witness = json.loads(child.read_text())
        report = json.loads(family.read_text())
        assert result.returncode == 70 and report['cleanup_verified'] is False
        assert any('cumulative' in error for error in report['observation_errors'])
        assert len(report['observed_family']) <= 8
        assert len(report['cleanup_signals']) <= 16 and family.stat().st_size < 32 * 1024
        assert report['surviving_processes'] == [] and not live(witness['pid'])
        resources_closed(witness)
        assert neighbour.read_bytes() == b'preserve unrelated evidence'
    finally:
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        if witness is not None:
            dispose_test_child(witness['pid'])


def test_overflow_signal_denial_retains_actual_survivors_without_per_child_wait(tmp_path):
    """Real held resources survive a denied syscall and must remain in the receipt."""
    family = tmp_path / 'process-family.json'
    held = CHILD.replace('import fcntl,json,os,socket,sys,time', 'import fcntl,json,os,socket,sys,time,signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)')
    parent = """import subprocess,sys,time
from pathlib import Path
for index in range(6):
    subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]+'/'+str(index)+'.json'],start_new_session=True)
while len(list(Path(sys.argv[2]).glob('*.json')))<6:time.sleep(.01)
time.sleep(60)
"""
    code = """import os,sys,signal
from pathlib import Path
from openpine.verification import execution_process as module
module._FAMILY_LEDGER_LIMIT=2
module._FAMILY_OVERFLOW_LIMIT=2
original=module._OwnedHandle.send_signal
def denied(self,signum):
    if signum==signal.SIGKILL:raise PermissionError('actual overflow pidfd signal denied')
    return original(self,signum)
module._OwnedHandle.send_signal=denied
# Allow all six real resource holders to start before injecting budget pressure.
original_children=__import__('psutil').Process.children
def ready(self,*args,**kwargs):
    rows=original_children(self,*args,**kwargs)
    import time
    while len(list(Path(sys.argv[3]).glob('*.json')))<6:time.sleep(.01)
    return rows
__import__('psutil').Process.children=ready
raise SystemExit(module._supervise_family([sys.executable,'-B','-c',sys.argv[1],sys.argv[2],sys.argv[3]],os.getppid(),Path(sys.argv[4])))
"""
    resources = tmp_path / 'resources'
    resources.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    started = time.monotonic()
    witnesses = []
    try:
        with (tmp_path / 'probe.log').open('wb') as log:
            result = subprocess.run(  # noqa: S603 -- owned signal-denial probe with real six-process fanout
                [sys.executable, '-B', '-c', code, parent, held, str(resources), str(family)],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=10,
            )
        witnesses = [json.loads(p.read_text()) for p in resources.glob('*.json')]
        assert len(witnesses) == 6 and all(live(w['pid']) for w in witnesses)
        report = json.loads(family.read_text())
        assert result.returncode == 70 and report['cleanup_verified'] is False
        assert {w['pid'] for w in witnesses}.issubset({p['pid'] for p in report['surviving_processes']})
        assert report['observation_errors'] and len(report['observed_family']) <= 2
        assert time.monotonic() - started < 4, 'overflow fanout added independent per-child waits'
    finally:
        if not witnesses:
            witnesses = [json.loads(p.read_text()) for p in resources.glob('*.json')]
        for witness in witnesses:
            dispose_test_child(witness['pid'])
            resources_closed(witness)


def test_guardian_cancellation_never_routes_recycled_process_group_to_neighbour(tmp_path, monkeypatch):
    from openpine.verification.execution_process import _stop_group, start_declared_process

    family = tmp_path / 'process-family.json'
    ready = tmp_path / 'declared-child-ready'
    neighbour = subprocess.Popen(  # noqa: S603 -- exclusively owned unrelated group routing witness
        [sys.executable, '-B', '-c', 'import time;time.sleep(30)'], start_new_session=True,
    )
    guardian = None
    routed = []
    try:
        guardian = start_declared_process(
            [sys.executable, '-B', '-c',
             'import sys,time;from pathlib import Path;Path(sys.argv[1]).touch();time.sleep(30)', str(ready)],
            family_evidence=family, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Let the real guardian establish ownership and start the declared child.
        deadline = time.monotonic() + 5
        while not ready.is_file() and guardian.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.is_file()
        original_killpg = os.killpg

        def recycled_group(pgid, signum):
            routed.append((pgid, signum))
            original_killpg(neighbour.pid, signum)

        with monkeypatch.context() as patch:
            patch.setattr(os, 'killpg', recycled_group)
            _stop_group(guardian)
            # Repeat after wait() reaps the guardian: its old PGID has no authority.
            _stop_group(guardian)
        assert guardian.poll() is not None
        assert neighbour.poll() is None and routed == []
        report = json.loads(family.read_text())
        assert report['cleanup_verified'] is True and report['surviving_processes'] == []
    finally:
        (tmp_path / 'routing-probe.json').write_text(json.dumps(
            {'modelled_group_routing': routed, 'neighbour_returncode_before_disposal': neighbour.poll(),
             'guardian_returncode_before_disposal': guardian.poll() if guardian is not None else None,
             'forced_kernel_pgid_reuse': False}
        ))
        if guardian is not None:
            if guardian.poll() is None:
                _stop_group(guardian)
            guardian.wait(timeout=3)
        if neighbour.poll() is None:
            neighbour.kill()
        neighbour.wait(timeout=3)


def test_late_guardian_pidfd_failure_reaps_stopped_guardian_without_command_launch(tmp_path, monkeypatch):
    import errno

    marker = tmp_path / 'command-launched'
    original_open = os.pidfd_open
    captured = []

    def late_failure(pid, flags=0):
        if pid == os.getpid():
            return original_open(pid, flags)
        # Retain an independent stable test witness; inject the production
        # acquisition failure only after the real guardian exists and is stopped.
        fd = original_open(pid, flags)
        captured.append((pid, fd))
        signal.pidfd_send_signal(fd, signal.SIGSTOP, None, 0)
        deadline = time.monotonic() + 3
        while psutil.Process(pid).status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
            time.sleep(.01)
        raise OSError(errno.EMFILE, 'injected late guardian pidfd acquisition failure')

    output = tmp_path / 'command'
    started = time.monotonic()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, 'pidfd_open', late_failure)
            report = run_logged(
                [sys.executable, '-B', '-c',
                 'import sys;from pathlib import Path;Path(sys.argv[1]).touch()', str(marker)],
                cwd=tmp_path, output=output, env=dict(os.environ),
            )
        assert report['status'] == 'infrastructure_error' and report['ok'] is False
        assert 'pidfd' in report['error'] and captured
        assert not marker.exists()
        assert all(not live(pid) for pid, _ in captured)
        assert (output / 'command.json').is_file()
        assert time.monotonic() - started < 7, 'late acquisition failure was not bounded'
    finally:
        (tmp_path / 'late-failure-probe.json').write_text(json.dumps(
            {'guardian_states_before_disposal': [{'pid': pid, 'live': live(pid)} for pid, _ in captured],
             'declared_command_launched': marker.exists(), 'injected_post_spawn_error': 'EMFILE'}
        ))
        for pid, fd in captured:
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
            except ProcessLookupError:
                pass  # The tested constructor already reaped this owned guardian.
            os.close(fd)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass  # Reaped by the production constructor.


def test_actual_sigint_during_guardian_handoff_retains_cancelled_evidence_and_closes_family(tmp_path, monkeypatch):
    _guardian_handoff_sigint_probe(tmp_path, monkeypatch, 'write')


def test_actual_sigint_during_gate_close_retains_cancelled_evidence_and_closes_family(tmp_path, monkeypatch):
    _guardian_handoff_sigint_probe(tmp_path, monkeypatch, 'close')


def test_actual_sigint_after_gate_read_close_reaps_guardian_without_child_or_pipe_leak(tmp_path, monkeypatch):
    from openpine.verification.execution_process import _FamilyPopen

    output = tmp_path / 'command'
    marker = tmp_path / 'declared-child-launched'
    original_init, original_close = subprocess.Popen.__init__, os.close
    captured = {}
    interrupted = False

    def witness_init(process, argv, **kwargs):
        original_init(process, argv, **kwargs)
        if isinstance(process, _FamilyPopen):
            read_fd = int(argv[7])
            captured.update(process=process, read_fd=read_fd,
                            pipe=f'pipe:[{os.fstat(read_fd).st_ino}]',
                            pidfd=os.pidfd_open(process.pid, 0))
            # Admit no command: wait for its real cleanup signal handler, so a
            # refused-gate receipt is available even before the first gate read.
            status = Path('/proc') / str(process.pid) / 'status'
            def handler_ready():
                mask = next(int(line.split()[1], 16) for line in status.read_text().splitlines() if line.startswith('SigCgt:'))
                return bool(mask & (1 << (signal.SIGTERM - 1)))
            deadline = time.monotonic() + 5
            while not handler_ready() and time.monotonic() < deadline:
                time.sleep(.01)
            assert handler_ready()

    def interrupt_close(fd):
        nonlocal interrupted
        result = original_close(fd)
        if fd == captured.get('read_fd') and not interrupted:
            interrupted = True
            signal.raise_signal(signal.SIGINT)
        return result

    def gate_descriptors():
        found = []
        for entry in Path('/proc/self/fd').iterdir():
            try:
                if os.readlink(entry) == captured.get('pipe'):
                    found.append(int(entry.name))
            except FileNotFoundError:
                pass  # Directory enumeration's own descriptor has closed.
        return found

    report = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess.Popen, '__init__', witness_init)
            patch.setattr(os, 'close', interrupt_close)
            report = run_logged(
                [sys.executable, '-B', '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]).touch()', str(marker)],
                cwd=tmp_path, output=output, env=dict(os.environ), timeout=5,
            )
        assert interrupted and report['status'] == 'cancelled'
        assert (output / 'command.json').is_file() and not marker.exists()
        assert captured['process'].poll() is not None, 'guardian survived read-end handoff interruption'
        assert gate_descriptors() == [], 'test-owned launch pipe leaked in the controller'
        family = json.loads((output / 'process-family.json').read_text())
        assert family['command_pid'] is None and family['cleanup_verified'] is False
        assert family['reason'] == 'launch-gate-refused'
        assert any('gate was not admitted' in error for error in family['observation_errors'])
        assert not family['observed_family'] and not family['surviving_processes']
    finally:
        process = captured.get('process')
        leaked = gate_descriptors() if captured else []
        (tmp_path / 'read-end-close-probe.json').write_text(json.dumps({
            'actual_SIGINT': interrupted,
            'status_before_disposal': report['status'] if report else None,
            'guardian_live_before_disposal': process.poll() is None if process else None,
            'declared_child_launched': marker.exists(),
            'test_owned_pipe_descriptors_before_disposal': leaked,
        }))
        for fd in leaked:
            os.close(fd)  # Only this test's exact launch pipe, after failed evidence.
        if process is not None:
            if process.poll() is None:
                signal.pidfd_send_signal(captured['pidfd'], signal.SIGKILL, None, 0)
            process.wait(timeout=3)
            os.close(captured['pidfd'])


def _guardian_handoff_sigint_probe(tmp_path, monkeypatch, boundary):
    child = tmp_path / 'child.json'
    output = tmp_path / 'command'
    original_open, original_write, original_close = os.pidfd_open, os.write, os.close
    captured = []
    interrupted = False
    gate_write_fd = None

    def witness_open(pid, flags=0):
        fd = original_open(pid, flags)
        if pid != os.getpid():
            captured.append((pid, os.dup(fd)))
        return fd

    def interrupt_when_ready():
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            deadline = time.monotonic() + 5
            while not child.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert child.exists(), 'actual declared child did not hold its resources'
            signal.raise_signal(signal.SIGINT)

    def interrupt_handoff(fd, payload):
        nonlocal gate_write_fd
        result = original_write(fd, payload)
        if payload == b'1':
            gate_write_fd = fd
            if boundary == 'write':
                interrupt_when_ready()
        return result

    def interrupt_close(fd):
        result = original_close(fd)
        if boundary == 'close' and fd == gate_write_fd:
            interrupt_when_ready()
        return result

    witness = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, 'pidfd_open', witness_open)
            patch.setattr(os, 'write', interrupt_handoff)
            patch.setattr(os, 'close', interrupt_close)
            try:
                report = run_logged(
                    [sys.executable, '-B', '-c', PARENT, CHILD, str(child), 'timeout'],
                    cwd=tmp_path, output=output, env=dict(os.environ), timeout=10,
                )
            except KeyboardInterrupt:
                pytest.fail('guardian handoff SIGINT escaped without cancellation evidence')
        witness = json.loads(child.read_text())
        assert interrupted and report['status'] == 'cancelled' and report['ok'] is False
        assert (output / 'command.json').is_file()
        family = json.loads((output / 'process-family.json').read_text())
        assert family['cleanup_verified'] is True and family['surviving_processes'] == []
        assert not live(witness['pid'])
        resources_closed(witness)
    finally:
        if witness is None and child.is_file():
            witness = json.loads(child.read_text())
        (tmp_path / 'handoff-probe.json').write_text(json.dumps(
            {'actual_SIGINT': interrupted, 'boundary': boundary, 'cancelled_receipt_exists_before_disposal': (output / 'command.json').is_file(),
             'owned_child_live_before_disposal': live(witness['pid']) if witness else None}
        ))
        for pid, fd in captured:
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
            except ProcessLookupError:
                pass  # Production already reaped the cancelled guardian.
            deadline = time.monotonic() + 3
            while live(pid) and time.monotonic() < deadline:
                time.sleep(.01)
            if live(pid):
                signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
            os.close(fd)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass  # Reaped by production.
        if witness is not None:
            dispose_test_child(witness['pid'])
