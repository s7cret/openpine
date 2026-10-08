"""Real kernel families; only a privileged client's signal denial is modelled."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from openpine.verification import execution_process as process_module
from openpine.verification import protected_qualification as harness


CLIENT = """import fcntl,json,os,signal,sys,time
from pathlib import Path
root=Path(sys.argv[1]); pipe=int(sys.argv[2])
signal.alarm(12)
lock=(root/'client.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX)
(root/'client.json.partial').write_text(json.dumps({'pid':os.getpid()}))
os.replace(root/'client.json.partial',root/'client.json')
while os.read(pipe,1):pass
(root/'client-channel-eof').touch()
if sys.argv[3]=='persistent':
    while not (root/'client-dispose').exists():time.sleep(.01)
else:time.sleep(.35 if sys.argv[3]=='late-cooperative' else .04)
"""

COORDINATOR = """import json,os,signal,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]);r,w=os.pipe()
child=subprocess.Popen([sys.executable,'-I','-B',str(root/'client.py'),str(root),str(r),sys.argv[2]],pass_fds=(r,),start_new_session=True)
os.close(r)
while not (root/'client.json').exists():time.sleep(.01)
(root/'coordinator.json.partial').write_text(json.dumps({'pid':os.getpid()}))
os.replace(root/'coordinator.json.partial',root/'coordinator.json')
while not (root/'release').exists():
    if (root/'coordinator-sigint').exists():signal.raise_signal(signal.SIGINT)
    time.sleep(.01)
os.close(w);child.wait(timeout=3)
"""

GUARDIAN = """import errno,importlib.util,json,os,signal,sys
from pathlib import Path
source=Path(SOURCE);root=Path(ROOT)
spec=importlib.util.spec_from_file_location('family_probe_product',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
import psutil
original_children=psutil.Process.children
def children(self,*args,**kwargs):
    rows=original_children(self,*args,**kwargs)
    witness=root/'client.json'
    if witness.exists() and json.loads(witness.read_text())['pid'] in {p.pid for p in rows}:
        (root/'family-observed').touch()
    return rows
psutil.Process.children=children
original_signal=module._OwnedHandle.send_signal
def send(self,signum):
    witness=root/'client.json'
    if witness.exists() and self.pid==json.loads(witness.read_text())['pid']:
        with (root/'denied-signals.jsonl').open('a') as out:
            out.write(json.dumps({'pid':self.pid,'signal':int(signum)})+'\\n')
        raise PermissionError(errno.EPERM,'modelled privileged client signal permission')
    return original_signal(self,signum)
module._OwnedHandle.send_signal=send
raise SystemExit(module._supervise_family(sys.argv[5:],int(sys.argv[2]),Path(sys.argv[3]),launch_gate=int(sys.argv[4])))
"""

CONTROLLER = """import importlib.util,os,sys
from pathlib import Path
root=Path(sys.argv[1]);source=Path(sys.argv[2])
spec=importlib.util.spec_from_file_location('family_probe_controller_product',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.__file__=str(root/'guardian.py')
result=module.run_logged([sys.executable,'-I','-B',str(root/'coordinator.py'),str(root),sys.argv[3]],cwd=root,output=root/'command',env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'PYTHONDONTWRITEBYTECODE':'1'},timeout=2 if sys.argv[4]=='timeout' else 15)
raise SystemExit(0 if result['ok'] else 1)
"""


def wait_file(path: Path, deadline: float) -> None:
    while not path.is_file() and time.monotonic() < deadline:
        time.sleep(.01)
    assert path.is_file(), f"owned test process did not produce {path.name}"


def alive(pidfd: int) -> bool:
    return process_module._pidfd_alive(pidfd)


def resources_closed(root: Path) -> None:
    assert (root / "client-channel-eof").exists()
    with (root / "client.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def exercise(root: Path, fault: str, persistent: bool, *, late_exit: bool = False) -> tuple[dict, dict | None, int, dict]:
    source = Path(process_module.__file__).resolve()
    for name, code in {
        "client.py": CLIENT, "coordinator.py": COORDINATOR, "controller.py": CONTROLLER,
        "guardian.py": GUARDIAN.replace("SOURCE", repr(str(source))).replace("ROOT", repr(str(root))),
    }.items():
        (root / name).write_text(code)
    client_fd = control_fd = neighbour_fd = -1
    witness = None
    neighbour = subprocess.Popen(  # noqa: S603 -- fixed interpreter and test-owned neighbour
        [sys.executable, "-I", "-B", "-c", "import time;time.sleep(30)"]
    )
    control = None
    try:
        neighbour_fd = os.pidfd_open(neighbour.pid)
        with (root / "controller.log").open("wb") as log:
            control = subprocess.Popen(  # noqa: S603 -- fixed interpreter, owned scripts, shell=False
                [sys.executable, "-I", "-B", str(root / "controller.py"), str(root),
                 str(source), "persistent" if persistent else
                 "late-cooperative" if late_exit else "cooperative", fault],
                stdout=log, stderr=subprocess.STDOUT,
            )
            control_fd = os.pidfd_open(control.pid)
            wait_file(root / "family-observed", time.monotonic() + 6)
            wait_file(root / "coordinator.json", time.monotonic() + 2)
            witness = json.loads((root / "client.json").read_text())
            coordinator_pid = json.loads((root / "coordinator.json").read_text())["pid"]
            client_fd = os.pidfd_open(witness["pid"])
            assert alive(client_fd) and alive(neighbour_fd)
            if fault == "controller-sigkill":
                signal.pidfd_send_signal(control_fd, signal.SIGKILL)
            elif fault == "sigint":
                # The owned coordinator raises a real signal on itself. A PID
                # from a readiness file never grants late signalling authority.
                (root / "coordinator-sigint").touch()
            elif fault == "release":
                (root / "release").touch()
            rc = control.wait(timeout=7)
        family_path = root / "command/process-family.json"
        wait_file(family_path, time.monotonic() + 3)
        family = json.loads(family_path.read_text())
        receipt_path = root / "command/command.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        assert alive(neighbour_fd), "unrelated neighbour was signalled"
        assert witness["pid"] in {row["pid"] for row in family["observed_family"]}
        if persistent:
            assert alive(client_fd)
            with (root / "client.lock").open("a") as lock:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert witness["pid"] in {row["pid"] for row in family["surviving_processes"]}
            assert family["cleanup_verified"] is False
            assert any("PermissionError" in error for error in family["observation_errors"])
        else:
            assert not alive(client_fd)
            resources_closed(root)
        return family, receipt, rc, {"controller_pid": control.pid, "coordinator_pid": coordinator_pid}
    finally:
        # Only the original unreaped direct children confer signal authority.
        # The client has a private disposal gate and a bounded self-lifetime;
        # its late observation-only pidfd must never be used for signalling.
        (root / "client-dispose").touch()
        for fd in (control_fd, neighbour_fd):
            if fd >= 0:
                if alive(fd):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                os.close(fd)
        if control is not None:
            if control.poll() is None:
                control.kill()  # Original direct unreaped child if pidfd acquisition failed.
            control.wait(timeout=3)
        if neighbour.poll() is None:
            neighbour.kill()  # Original direct unreaped child if pidfd acquisition failed.
        neighbour.wait(timeout=3)
        if client_fd >= 0:
            try:
                deadline = time.monotonic() + 3
                while alive(client_fd) and time.monotonic() < deadline:
                    time.sleep(.01)
                assert not alive(client_fd), "private client disposal did not complete"
            finally:
                os.close(client_fd)


@pytest.mark.parametrize("fault", ["timeout", "controller-sigkill", "sigint"])
def test_actual_cooperative_client_exits_without_unnecessary_denied_signal(tmp_path, fault):
    family, receipt, rc, identities = exercise(tmp_path, fault, False)
    assert family["observation_errors"] == [], family
    assert family["cleanup_verified"] is True and family["surviving_processes"] == []
    assert not (tmp_path / "denied-signals.jsonl").exists()
    harness.validate_fault_family(family, fault, identities["controller_pid"],
                                  identities["coordinator_pid"], rc, receipt)


@pytest.mark.parametrize("fault", ["timeout", "controller-sigkill"])
def test_actual_live_denied_client_still_rejects_fault_receipt(tmp_path, fault):
    family, receipt, rc, identities = exercise(tmp_path, fault, True)
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.validate_fault_family(family, fault, identities["controller_pid"],
                                      identities["coordinator_pid"], rc, receipt)
    assert caught.value.code == "family-observation-errors"
    attempts = [json.loads(line) for line in (tmp_path / "denied-signals.jsonl").read_text().splitlines()]
    assert {row["signal"] for row in attempts} == {signal.SIGTERM, signal.SIGKILL}


@pytest.mark.parametrize("fault", ["timeout", "controller-sigkill"])
def test_actual_late_exit_does_not_forgive_a_denied_cleanup_signal(tmp_path, fault):
    family, receipt, rc, identities = exercise(tmp_path, fault, False, late_exit=True)
    assert family["surviving_processes"] == [] and family["cleanup_verified"] is False
    assert any("PermissionError" in error for error in family["observation_errors"])
    with pytest.raises(harness.FaultPrimaryError) as caught:
        harness.validate_fault_family(family, fault, identities["controller_pid"],
                                      identities["coordinator_pid"], rc, receipt)
    assert caught.value.code == "family-observation-errors"


def test_actual_normal_release_still_accepts_complete_family(tmp_path):
    family, receipt, rc, _ = exercise(tmp_path, "release", False)
    assert rc == 0 and receipt is not None and receipt["ok"] is True
    assert not (tmp_path / "denied-signals.jsonl").exists()
    process_module.validate_process_family(tmp_path / "command/process-family.json")
