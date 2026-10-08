"""Real protected worker faults; private primaries remain separate from projections.

Fixture instrumentation records the actual allocated unit and holds after the
production initializer validates HELLO and sends LOAD_ARTIFACT/INIT_RUN.
No worker or receipt is mocked.
This module never provisions users, AppArmor, sudo or cgroups.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import time
from typing import Any

from openpine.verification.qualification_public import FAMILY_ERRORS

UNIT = re.compile(r"openpine-worker-[0-9a-f]{32}\Z")
MODULE = "openpine.verification.protected_qualification"
MODES = ("interactive", "bulk_backtest")
FAULTS = ("timeout", "sigint", "controller-sigkill")
MODULES = ("openpine", "openpine_contracts", "pine2ast", "ast2python",
           "pinelib", "backtest_engine", "marketdata_provider", "optimizer")
PROPERTIES = ("ActiveState", "SubState", "MainPID", "ControlGroup",
              "TasksCurrent", "MemoryCurrent")
MAX_JSON = 4 * 1024 * 1024


def closed_environment() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "TZ": "UTC", "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            **{name: "1" for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                                      "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > MAX_JSON:
        raise ValueError("unbounded or symlinked qualification input")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("qualification input must be an object")
    return value


def write_primary(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > MAX_JSON:
        raise ValueError("qualification primary exceeds byte budget")
    temporary = path.with_name("." + path.name + "." + str(os.getpid()))
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # atomic publication, refuses an existing primary
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def unit_name(value: object) -> str:
    if not isinstance(value, str) or UNIT.fullmatch(value) is None:
        raise ValueError("only an exact test-owned UUID unit is allowed")
    return value


def installed_origins() -> dict[str, str]:
    prefix = Path(sys.prefix).resolve()
    origins = {}
    for name in MODULES:
        filename = importlib.import_module(name).__file__
        if not isinstance(filename, str):
            raise ValueError("candidate product module has no installed file origin")
        origins[name] = str(Path(filename).resolve())
    if not all(Path(value).is_relative_to(prefix) for value in origins.values()):
        raise ValueError("qualification requires all eight installed candidate origins")
    return origins


def parse_unit_properties(text: str) -> dict[str, str]:
    rows = {}
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in PROPERTIES or name in rows:
            raise ValueError("unexpected or duplicate systemd property")
        rows[name] = value
    if set(rows) != set(PROPERTIES):
        raise ValueError("incomplete systemd unit observation")
    return rows


class UnitObserver:
    """Read only the exact declared unit and its bounded cgroup subtree."""

    def snapshot(self, unit: str, known_group: str = "") -> dict[str, Any]:
        unit = unit_name(unit)
        command = ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "show",
                   "--property=" + ",".join(PROPERTIES), unit]
        result = subprocess.run(command, capture_output=True, text=True,  # noqa: S603 -- fixed absolute executables and validated exact UUID, no shell
                                timeout=3, check=False)
        if result.returncode:
            absent = subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/systemctl",  # noqa: S603 -- fixed manager query and validated UUID, no shell
                "list-units", "--all", "--no-legend", "--plain", unit + ".service"],
                capture_output=True, text=True, timeout=3, check=False)
            if absent.returncode or absent.stdout.strip():
                raise ValueError("cannot observe exact owned systemd unit")
            properties = {name: "" for name in PROPERTIES}
            properties.update(ActiveState="inactive", SubState="dead", MainPID="0")
        else:
            properties = parse_unit_properties(result.stdout)
        members: set[int] = set()
        group = properties["ControlGroup"] or known_group
        if group:
            root = Path("/sys/fs/cgroup").resolve()
            path = (root / group.lstrip("/")).resolve()
            if (not group.startswith("/") or path == root
                    or not path.is_relative_to(root)
                    or unit + ".service" not in path.parts):
                raise ValueError("unit cgroup is not its declared UUID subtree")
            if path.exists():
                files = [path / "cgroup.procs", *path.glob("**/cgroup.procs")]
                if len(files) > 4096:
                    raise ValueError("unit cgroup traversal exceeds budget")
                for filename in set(files):
                    if filename.is_symlink() or not filename.resolve().is_relative_to(path):
                        raise ValueError("unit cgroup membership escapes owned subtree")
                    values = filename.read_text().split()
                    if any(not v.isdecimal() or int(v) <= 0 for v in values):
                        raise ValueError("invalid live cgroup process identity")
                    members.update(map(int, values))
                    if len(members) > 4096:
                        raise ValueError("unit process membership exceeds budget")
        return {"unit": unit, "properties": properties, "members": sorted(members)}


class StableProcess:
    def __init__(self, pid: int):
        if type(pid) is not int or pid <= 0:
            raise ValueError("invalid owned process identity")
        self.pid = pid
        self.fd = os.pidfd_open(pid, 0)

    def alive(self) -> bool:
        poller = select.poll()
        poller.register(self.fd, select.POLLIN)
        events = poller.poll(0)
        if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
            raise OSError("owned pidfd observation failed")
        return not events

    def send(self, signum: int) -> None:
        signal.pidfd_send_signal(self.fd, signum, None, 0)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def live_unit(snapshot: dict[str, Any]) -> bool:
    props = snapshot["properties"]
    return (props["ActiveState"] == "active" and props["MainPID"].isdecimal()
            and int(props["MainPID"]) > 0 and bool(snapshot["members"]))


def cleaned_unit(snapshot: dict[str, Any], handles: list[StableProcess]) -> bool:
    return (snapshot["properties"]["ActiveState"] in ("inactive", "failed")
            and not snapshot["members"] and not any(h.alive() for h in handles))


def bind_fixture_candidate(libraries: Any, native: Any, marketdata: Any,
                           manifest: dict[str, Any], deployment: Any) -> None:
    """Bind the compiler, context and canonical producer to the same candidate."""
    libraries.ALL_COMMITS = {name: row["sha"] for name, row in manifest["components"].items()}
    libraries._deployment = lambda: deployment
    libraries._manifest = lambda: manifest
    native._manifest = lambda: manifest
    # bar() imports keep this producer's module globals. Set its inputs before
    # canonical creation; never rewrite or reseal an already produced envelope.
    marketdata.STACK = manifest["manifest_hash"]
    marketdata.COMMIT = libraries.ALL_COMMITS["marketdata-provider"]


def fixture(spec_path: Path, folder: Path, mode: str) -> None:
    """Run the original native fixture/assertions with actual candidate admission."""
    spec = read_json(spec_path)
    origins = installed_origins()
    # Only the copied test namespace is added. Product modules were already
    # admitted from the installed prefix, with no product PYTHONPATH overlay.
    tests = Path(spec["tests_root"]).resolve()
    if not (tests / "rc6_tests").is_dir() or (tests / "openpine").exists():
        raise ValueError("fault fixture namespace must contain tests only")
    sys.path.insert(0, str(tests))
    from openpine.admission import load_active_deployment_identity
    from openpine.runtime import isolated_worker as worker
    from rc6_tests import test_rc6_library_imports as libraries
    from rc6_tests import test_rc6_marketdata_boundary as marketdata
    from rc6_tests import test_rc6_numeric_search_integration as native

    manifest_path = Path(spec["candidate_manifest"])
    if sha256(manifest_path) != spec["candidate_manifest_sha256"]:
        raise ValueError("candidate admission bytes changed")
    manifest = read_json(manifest_path)
    deployment = load_active_deployment_identity(manifest_path, Path(spec["wheelhouse"]))
    commits = {name: row["sha"] for name, row in manifest["components"].items()}
    if commits != spec["source_commits"]:
        raise ValueError("candidate producer commits differ from qualification binding")
    bind_fixture_candidate(libraries, native, marketdata, manifest, deployment)
    original = worker.InteractiveWorkerSession.__init__
    original_unit = worker._worker_unit_name

    def allocated_unit():
        actual = unit_name(original_unit())
        write_primary(folder / "allocated-unit.json", {"unit": actual,
                                                       "coordinator_pid": os.getpid()})
        return actual

    def held(instance, *args, **kwargs):
        # The diagnostic hold must not itself expire the bulk worker's idle
        # clock before the independently supervised 60-second fault occurs.
        # Only this fixture's timing budget changes; production code is intact.
        kwargs["bulk_idle_timeout_s"] = 240.0
        original(instance, *args, **kwargs)
        write_primary(folder / "ready.json", {
                "coordinator_pid": os.getpid(), "unit": instance.unit_name,
                "systemd_client_pid": instance.proc.pid, "hello": instance.hello,
                "execution_context": instance.protocol.execution_context,
                "origins": origins, "candidate_manifest_hash": manifest["manifest_hash"],
        })
        deadline = time.monotonic() + 240
        while not (folder / "release").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("qualification timing hold expired")
            time.sleep(0.02)

    setattr(worker.InteractiveWorkerSession, "__init__", held)
    worker._worker_unit_name = allocated_unit
    try:
        native.test_real_protected_worker_searches_native_array(folder, mode, False)
        write_primary(folder / "native-result.json", {"ok": True, "bars": 6,
                                                      "intent": ["search", 2, 3],
                                                      "trade": [103, 3]})
    finally:
        setattr(worker.InteractiveWorkerSession, "__init__", original)
        worker._worker_unit_name = original_unit


def controller(spec: Path, folder: Path, mode: str, timeout: int, gate_fd: int) -> None:
    if os.read(gate_fd, 1) != b"1":
        raise ValueError("controller launch gate was refused")
    os.close(gate_fd)
    from openpine.verification.execution_process import run_logged
    command = [sys.executable, "-I", "-B", "-m", MODULE, "fixture",
               "--spec", str(spec), "--output", str(folder), "--mode", mode]
    # Explicit environment; no inherited token, credentials, product PYTHONPATH.
    env = closed_environment()
    result = run_logged(command, cwd=folder, output=folder / "command", env=env,
                        timeout=timeout, inputs={"binding": {"path": str(spec),
                        "sha256": sha256(spec)}})
    raise SystemExit(0 if result["ok"] else 1)


def wait_ready(path: Path, process: subprocess.Popen, deadline: float) -> dict[str, Any]:
    while not path.exists():
        if process.poll() is not None:
            raise ValueError("real fixture exited before validated worker HELLO")
        if time.monotonic() >= deadline:
            raise TimeoutError("real protected worker readiness timed out")
        time.sleep(0.02)
    value = read_json(path)
    unit_name(value["unit"])
    if value.get("hello", {}).get("kind") != "HELLO":
        raise ValueError("fixture did not retain actual worker HELLO")
    return value


class FaultPrimaryError(ValueError):
    """A fixed public code with private validation context kept separate."""

    def __init__(self, code: str, message: str):
        if not isinstance(code, str) or code not in FAMILY_ERRORS:
            raise ValueError("fault primary diagnostic is outside its closed vocabulary")
        self.code = code
        super().__init__(message)


def read_fault_primary(path: Path, kind: str) -> dict[str, Any]:
    if kind not in {"family", "receipt"}:
        raise ValueError("unknown fault primary kind")
    try:
        return read_json(path)
    except FileNotFoundError as error:
        raise FaultPrimaryError(kind + "-missing", "fault primary is missing") from error
    except (OSError, ValueError, TypeError) as error:
        raise FaultPrimaryError(kind + "-invalid", "fault primary is unreadable or invalid") from error


def validate_fault_family(family: dict[str, Any], fault: str, controller_pid: int,
                          coordinator_pid: int, returncode: int,
                          receipt: dict[str, Any] | None) -> None:
    """Validate actual failure primaries without applying success-only acceptance."""
    expected_reasons = {"timeout": {"cancelled"},
                        "sigint": {"command-exited", "orphan-descendants"},
                        # The real guardian receives PDEATHSIG=SIGTERM, so its
                        # cancelled reason also proves this owned crash path.
                        "controller-sigkill": {"controller-crashed", "cancelled"}}[fault]
    message = "fault process-family observation is incomplete or unclean"
    checks = (
        (family.get("controller_pid") == controller_pid, "family-controller-mismatch"),
        (family.get("command_pid") == coordinator_pid, "family-command-mismatch"),
        (isinstance(family.get("reason"), str) and family["reason"] in expected_reasons, "family-reason-mismatch"),
        # Report the specific mandatory observation before its derived cleanup
        # flag. Multiple failures still fail; this only chooses the public code.
        (family.get("observation_errors") == [], "family-observation-errors"),
        (family.get("surviving_processes") == [], "family-survivors"),
        (family.get("cleanup_verified") is True, "family-cleanup-unverified"),
    )
    for passed, code in checks:
        if not passed:
            raise FaultPrimaryError(code, message)
    identities = family.get("observed_family")
    if not isinstance(identities, list) or not 1 <= len(identities) <= 4096:
        raise FaultPrimaryError("family-ledger-invalid", message)
    keys = set()
    for row in identities:
        try:
            invalid = (not isinstance(row, dict) or set(row) != {"pid", "create_time", "name"}
                or type(row["pid"]) is not int or row["pid"] <= 0
                or type(row["create_time"]) not in (int, float)
                or not math.isfinite(row["create_time"]) or row["create_time"] <= 0
                or not isinstance(row["name"], str) or not row["name"])
        except (OverflowError, TypeError, ValueError):
            invalid = True
        if invalid:
            raise FaultPrimaryError("family-identity-invalid", "invalid retained fault process identity")
        if (row["pid"], row["create_time"]) in keys:
            raise FaultPrimaryError("family-identity-duplicate", "invalid retained fault process identity")
        keys.add((row["pid"], row["create_time"]))
    if coordinator_pid not in {pid for pid, _ in keys}:
        raise FaultPrimaryError("family-coordinator-missing", "fault family omitted its actual fixture coordinator")
    signals = family.get("cleanup_signals")
    if not isinstance(signals, list) or len(signals) > 2 * len(keys):
        raise FaultPrimaryError("family-signals-invalid", "unbounded fault cleanup signal ledger")
    seen = set()
    for row in signals:
        if (not isinstance(row, dict) or set(row) != {"pid", "create_time", "signal"}
                or type(row["signal"]) is not int
                or row["signal"] not in (signal.SIGTERM, signal.SIGKILL)):
            raise FaultPrimaryError("family-signal-invalid", "invalid fault cleanup signal identity")
        try:
            owned = (row["pid"], row["create_time"]) in keys
        except TypeError as error:
            raise FaultPrimaryError("family-signal-invalid", "invalid fault cleanup signal identity") from error
        if not owned:
            raise FaultPrimaryError("family-signal-foreign", "invalid fault cleanup signal identity")
        key = (row["pid"], row["create_time"], row["signal"])
        if key in seen:
            raise FaultPrimaryError("family-signal-duplicate", "duplicate fault cleanup signal")
        seen.add(key)
    message = "requested fault was not proved by actual exit/command primaries"
    if returncode != (-signal.SIGKILL if fault == "controller-sigkill" else 1):
        raise FaultPrimaryError("fault-exit-mismatch", message)
    if fault == "controller-sigkill":
        if receipt is not None:
            raise FaultPrimaryError("receipt-unexpected", message)
        return
    if receipt is None:
        raise FaultPrimaryError("receipt-missing", message)
    if not isinstance(receipt, dict):
        raise FaultPrimaryError("receipt-invalid", message)
    if receipt.get("status") != ("timeout" if fault == "timeout" else "failed"):
        raise FaultPrimaryError("receipt-status-mismatch", message)
    if fault == "sigint" and family.get("command_returncode") not in (-signal.SIGINT, 128 + signal.SIGINT):
        raise FaultPrimaryError("command-exit-mismatch", message)


def _complete_neighbour(process: subprocess.Popen, folder: Path, result: dict[str, Any]) -> None:
    """Keep completion evidence explicit; only closed diagnostics are projected."""
    result["case_stage"] = "neighbour-release"
    try:
        (folder / "release").touch(exist_ok=False)
    except OSError:
        result["case_error"] = "failed"
        return
    result["case_stage"] = "neighbour-wait"
    result["neighbour_completion_state"] = "unverified"
    try:
        returncode = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        result["case_error"] = "timeout"
        result["neighbour_completion_state"] = "observed-false"
        return
    if returncode != 0:
        result["case_error"] = "nonzero-exit"
        result["neighbour_completion_state"] = "observed-false"
        return
    result["case_stage"] = "native-result"
    try:
        native = read_json(folder / "native-result.json")
    except FileNotFoundError:
        result["case_error"] = "missing-result"
        result["neighbour_completion_state"] = "observed-false"
        return
    except (OSError, ValueError, TypeError):
        result["case_error"] = "invalid-result"
        result["neighbour_completion_state"] = "observed-false"
        return
    if native != {"ok": True, "bars": 6, "intent": ["search", 2, 3], "trade": [103, 3]}:
        result["case_error"] = "invalid-result"
        result["neighbour_completion_state"] = "observed-false"
        return
    result["neighbour_completed"] = True
    result["neighbour_completion_state"] = "observed-true"
    result["case_stage"] = "complete"


def run_fault(spec: Path, output: Path, mode: str, fault: str) -> dict[str, Any]:
    from openpine.runtime.isolated_worker import _stop_worker_unit
    if mode not in MODES or fault not in FAULTS:
        raise ValueError("unknown qualification mode or fault")
    output.mkdir(parents=True, exist_ok=False)
    observer = UnitObserver()
    processes: list[subprocess.Popen] = []
    controls: list[StableProcess | None] = []
    worker_handles: list[StableProcess] = []
    neighbour_handles: list[StableProcess] = []
    coordinator: StableProcess | None = None
    ready: dict[str, dict[str, Any]] = {}
    before: dict[str, Any] = {}
    result: dict[str, Any] = {"mode": mode, "fault": fault, "ok": False,
                              "automatic_cleanup": False, "neighbour_survived": False,
                              "neighbour_completed": False, "case_stage": "setup",
                              "case_error": "none", "neighbour_completion_state": "not-reached"}
    try:
        for role in ("affected", "neighbour"):
            folder = output / role
            folder.mkdir()
            gate_read, gate_write = os.pipe()
            argv = [sys.executable, "-I", "-B", "-m", MODULE, "controller",
                    "--spec", str(spec), "--output", str(folder), "--mode", mode,
                    "--gate-fd", str(gate_read),
                    "--timeout", "60" if role == "affected" and fault == "timeout" else "180"]
            process = None
            try:
                with (folder / "controller.stdout").open("xb") as out, (folder / "controller.stderr").open("xb") as err:
                    process = subprocess.Popen(argv, cwd=folder, stdout=out, stderr=err,  # noqa: S603 -- this interpreter, closed environment, declared module argv, no shell
                                               pass_fds=(gate_read,), env=closed_environment())
                processes.append(process)
                controls.append(None)
                os.close(gate_read)
                gate_read = -1
                control = StableProcess(process.pid)
                controls[-1] = control
                os.write(gate_write, b"1")
            except BaseException:
                os.close(gate_write)
                gate_write = -1
                if process is not None:
                    try:
                        process.wait(timeout=3)  # EOF refuses gate before any worker launch
                    except subprocess.TimeoutExpired:
                        pass  # Keep the launch error; owned disposal still runs below.
                raise
            finally:
                if gate_read >= 0:
                    os.close(gate_read)
                if gate_write >= 0:
                    os.close(gate_write)
            ready[role] = wait_ready(folder / "ready.json", process, time.monotonic() + 30)
        if ready["affected"]["unit"] == ready["neighbour"]["unit"]:
            raise ValueError("protected coordinators share a unit")
        before = {role: observer.snapshot(row["unit"]) for role, row in ready.items()}
        if not all(live_unit(row) for row in before.values()) or not all(h is not None and h.alive() for h in controls):
            raise ValueError("both actual units and coordinators must be live before fault")
        for pid in before["affected"]["members"]:
            worker_handles.append(StableProcess(pid))
        for pid in before["neighbour"]["members"]:
            neighbour_handles.append(StableProcess(pid))
        if set(before["affected"]["members"]) & set(before["neighbour"]["members"]):
            raise ValueError("independent units share process identities")
        psutil = importlib.import_module("psutil")
        coordinator = StableProcess(ready["affected"]["coordinator_pid"])
        if not any(p.pid == processes[0].pid for p in psutil.Process(coordinator.pid).parents()):
            raise ValueError("fixture coordinator is outside the owned controller family")
        write_primary(output / "before.json", before)
        result["case_stage"] = "fault"
        if fault != "timeout":
            target = coordinator if fault == "sigint" else controls[0]
            assert target is not None
            target.send(signal.SIGINT if fault == "sigint" else signal.SIGKILL)
        returncode = processes[0].wait(timeout=75)
        result["case_stage"] = "automatic-observation"
        deadline = time.monotonic() + 5
        while True:
            affected = observer.snapshot(ready["affected"]["unit"], before["affected"]["properties"]["ControlGroup"])
            if cleaned_unit(affected, worker_handles) or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        neighbour = observer.snapshot(ready["neighbour"]["unit"])
        write_primary(output / "after.json", {"affected": affected, "neighbour": neighbour})
        result["automatic_cleanup"] = cleaned_unit(affected, worker_handles)
        result["neighbour_survived"] = (live_unit(neighbour) and controls[1] is not None and controls[1].alive()
            and all(h.alive() for h in neighbour_handles)
            and neighbour["properties"]["MainPID"] == before["neighbour"]["properties"]["MainPID"]
            and neighbour["properties"]["ControlGroup"] == before["neighbour"]["properties"]["ControlGroup"])
        result["case_stage"] = "family-receipt"
        family = output / "affected/command/process-family.json"
        deadline = time.monotonic() + 5
        while not family.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        receipt_path = output / "affected/command/command.json"
        receipt = read_fault_primary(receipt_path, "receipt") if receipt_path.exists() else None
        validate_fault_family(read_fault_primary(family, "family"), fault, processes[0].pid,
                              coordinator.pid, returncode, receipt)
        result["family_sha256"] = sha256(family)
        result["controller_returncode"] = returncode
        result["command_receipt_present"] = receipt is not None
        _complete_neighbour(processes[1], output / "neighbour", result)
        result["ok"] = (result["automatic_cleanup"] and result["neighbour_survived"]
                        and result["neighbour_completed"])
        if not result["ok"] and result["case_error"] == "none":
            result["case_error"] = "failed"
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 -- fault boundary retains failure and always disposes only owned resources
        result["case_error"] = (error.code if isinstance(error, FaultPrimaryError) else
                                "interrupted" if isinstance(error, KeyboardInterrupt) else
                                "timeout" if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) else "failed")
        result["error_type"] = type(error).__name__
        result["error"] = str(error)[:2048]
    finally:
        # Record automatic result before any forced disposal. This file is never
        # rewritten by cleanup; a surviving unit permanently fails this attempt.
        disposal: dict[str, Any] = {"units": {}, "controllers": {}, "errors": []}
        try:
            write_primary(output / "automatic-result.json", result)
        except (OSError, ValueError) as error:
            result["ok"] = False
            disposal["errors"].append("automatic primary persistence: " + str(error)[:2048])
        # Record the production UUID before startup, so even failed HELLO/setup
        # can be disposed without discovering or guessing unrelated units.
        owned = dict(ready)
        for role in ("affected", "neighbour"):
            allocated = output / role / "allocated-unit.json"
            if role not in owned and allocated.exists():
                try:
                    owned[role] = read_json(allocated)
                    unit_name(owned[role]["unit"])
                except (OSError, ValueError, KeyError) as error:
                    owned.pop(role, None)
                    disposal["errors"].append("unit allocation observation: " + str(error)[:2048])
        for role, row in owned.items():
            try:
                _stop_worker_unit(unit_name(row["unit"]))
                group = before.get(role, {}).get("properties", {}).get("ControlGroup", "")
                snapshot = observer.snapshot(row["unit"], group)
                disposal["units"][role] = snapshot
                handles = worker_handles if role == "affected" else neighbour_handles
                if not cleaned_unit(snapshot, handles):
                    disposal["errors"].append("owned unit disposal incomplete: " + role)
            except Exception as error:  # noqa: BLE001 -- disposal boundary records errors and continues remaining exact owned units
                disposal["units"][role] = {"error": str(error)[:2048]}
                disposal["errors"].append("owned unit disposal failed: " + role)
        for index, process in enumerate(processes):
            try:
                disposal["controllers"][str(index)] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle = controls[index]
                try:
                    if handle is None:
                        raise ValueError("launch gate refused without retained controller identity")
                    if handle.alive():
                        handle.send(signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        if handle.alive():
                            handle.send(signal.SIGKILL)
                        process.wait(timeout=3)
                except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                    disposal["errors"].append("controller disposal: " + str(error)[:2048])
        for handle in [*worker_handles, *neighbour_handles, *controls, *([coordinator] if coordinator else [])]:
            if handle is not None:
                try:
                    handle.close()
                except OSError as error:
                    disposal["errors"].append("retained handle close: " + str(error)[:2048])
        result["forced_disposal_ok"] = not disposal["errors"]
        if disposal["errors"]:
            result["ok"] = False
            if result["case_error"] == "none":
                result["case_error"] = "disposal-failed"
        write_primary(output / "forced-disposal.json", disposal)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fixture", "controller", "fault"))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--fault", choices=FAULTS)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--gate-fd", type=int)
    args = parser.parse_args()
    if args.action == "fixture":
        fixture(args.spec, args.output, args.mode)
    elif args.action == "controller":
        if args.gate_fd is None:
            parser.error("controller requires an inherited launch gate")
        controller(args.spec, args.output, args.mode, args.timeout, args.gate_fd)
    else:
        if args.fault is None:
            parser.error("fault action requires --fault")
        return 0 if run_fault(args.spec, args.output, args.mode, args.fault)["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
