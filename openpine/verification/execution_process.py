"""Bounded subprocess cleanup shared by verification and CI preparation."""

from __future__ import annotations
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast


class _OwnedProcess(Protocol):
    pid: int

    def create_time(self) -> float: ...
    def name(self) -> str: ...
    def status(self) -> str: ...
    def send_signal(self, sig: int) -> None: ...


class _FamilyProcess(_OwnedProcess, Protocol):
    def children(self, recursive: bool = False) -> list[_FamilyProcess]: ...


class _FamilyObserver(Protocol):
    Process: Callable[..., _FamilyProcess]
    NoSuchProcess: type[Exception]
    AccessDenied: type[Exception]
    STATUS_ZOMBIE: str
    STATUS_DEAD: str


def require_executable(path: Path) -> None:
    if not path.is_file() or not path.stat().st_mode & 0o111 or not os.access(path, os.X_OK):
        raise ValueError("declared executable is not an executable regular file")


def start_declared_process(
    argv: list[str], *, family_evidence: Path | None = None, **kwargs
) -> subprocess.Popen:
    """Shared shell-free launch with an explicit executable and argv."""
    if (
        not argv
        or any(not isinstance(v, str) or "\x00" in v for v in argv)
        or not Path(argv[0]).is_absolute()
    ):
        raise ValueError("need an absolute executable and declared argv")
    if kwargs.get("shell"):
        raise ValueError("declared commands never use a shell")
    require_executable(Path(argv[0]).resolve(strict=True))
    if family_evidence is not None:
        if not sys.platform.startswith("linux"):
            raise ValueError("complete process-family supervision requires Linux subreaper support")
        argv = [
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--supervise-family",
            str(os.getpid()),
            str(family_evidence),
            *argv,
        ]
    return subprocess.Popen(argv, **kwargs)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked


def _supervise_family(argv: list[str], parent_pid: int, evidence: Path) -> int:
    """Own adopted descendants, including new sessions, after controller death.

    This process is a per-command Linux subreaper, never a host-wide reaper.
    Signal targets must retain their observed PID/create-time identity. No
    preexec_fn is used in the threaded coordinator, and neighbours are never signalled.
    """
    import ctypes
    import json
    import time
    from importlib import import_module

    # Structural interface for the already locked runtime dependency; no
    # optional third-party stub install or type-check suppression is required.
    psutil = cast(_FamilyObserver, import_module('psutil'))

    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER and PR_SET_PDEATHSIG are unprivileged Linux APIs.
    if libc.prctl(36, 1, 0, 0, 0) != 0 or libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot establish owned process-family supervisor")
    stopped = 0

    def stop(signum, frame):
        nonlocal stopped
        stopped = signum

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    owner = psutil.Process()
    observed: dict[tuple[int, float], dict[str, Any]] = {}
    sent: list[dict[str, Any]] = []
    errors: list[str] = []
    child = None

    def record_error(error):
        if len(errors) < 8:
            errors.append(f"{type(error).__name__}: {error}")

    boot_time = next(
        int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
        if line.startswith('btime ')
    )
    clock_ticks = os.sysconf('SC_CLK_TCK')

    class ProcProcess:
        """Kernel identity fallback for this supervisor's owned descendants."""

        def __init__(self, pid: int) -> None:
            self.pid = pid

        def stat(self) -> tuple[str, list[str]]:
            text = (Path('/proc') / str(self.pid) / 'stat').read_bytes()
            end = text.rfind(b')')
            fields = [field.decode('ascii') for field in text[end + 2:].split()]
            return os.fsdecode(text[text.find(b'(') + 1:end]), fields

        def create_time(self) -> float:
            return boot_time + int(self.stat()[1][19]) / clock_ticks

        def name(self) -> str:
            return self.stat()[0]

        def status(self) -> str:
            state = self.stat()[1][0]
            return psutil.STATUS_ZOMBIE if state == 'Z' else psutil.STATUS_DEAD if state == 'X' else state

        def send_signal(self, signum: int) -> None:
            os.kill(self.pid, signum)

    absent = (psutil.NoSuchProcess, FileNotFoundError, ProcessLookupError)

    def kernel_fallback():
        # Retain known identities as well as newly adopted direct children.
        pids = {pid for pid, _ in observed}
        try:
            for name in os.listdir('/proc'):
                if not name.isdecimal():
                    continue
                try:
                    process = ProcProcess(int(name))
                    if int(process.stat()[1][1]) == os.getpid():
                        pids.add(process.pid)
                except absent:
                    continue
                except OSError as error:
                    record_error(error)
        except OSError as error:
            record_error(error)
        descendants: list[_OwnedProcess] = []
        for pid in pids:
            process = ProcProcess(pid)
            try:
                key = (pid, process.create_time())
                if key not in observed and int(process.stat()[1][1]) != os.getpid():
                    continue
                observed.setdefault(key, {'pid': pid, 'create_time': key[1], 'name': process.name()})
                descendants.append(process)
            except absent:
                continue
            except OSError as error:
                record_error(error)
                # Keep the unresolved observed identity for the survivor receipt.
                if any(known_pid == pid for known_pid, _ in observed):
                    descendants.append(process)
        return descendants

    def observe():
        try:
            descendants = owner.children(recursive=True)
            if len(descendants) > 4096:
                raise RuntimeError("owned process-family observation budget exceeded")
            for process in descendants:
                try:
                    key = (process.pid, process.create_time())
                    observed.setdefault(
                        key, {"pid": process.pid, "create_time": key[1], "name": process.name()}
                    )
                except absent:
                    continue
            return descendants
        except (psutil.AccessDenied, OSError, RuntimeError) as error:
            record_error(error)
            return kernel_fallback()

    def active():
        result = []
        for process in observe():
            try:
                if process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                    result.append(process)
            except absent:
                continue
            except (psutil.AccessDenied, OSError) as error:
                record_error(error)
                fallback = ProcProcess(process.pid)
                try:
                    if (fallback.pid, fallback.create_time()) in observed and fallback.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                        result.append(fallback)
                except absent:
                    continue
                except OSError as caught:
                    record_error(caught)
                    result.append(fallback)
        return result

    def send(processes, signum):
        for process in processes:
            try:
                key = (process.pid, process.create_time())
                if key in observed and process.pid != os.getpid():
                    process.send_signal(signum)
                    sent.append({"pid": process.pid, "create_time": key[1], "signal": signum})
            except absent:
                continue
            except (psutil.AccessDenied, OSError) as error:
                record_error(error)
                fallback = ProcProcess(process.pid)
                try:
                    key = (fallback.pid, fallback.create_time())
                    if key in observed and fallback.pid not in {os.getpid(), parent_pid}:
                        fallback.send_signal(signum)
                        sent.append({'pid': fallback.pid, 'create_time': key[1], 'signal': signum})
                except absent:
                    continue
                except OSError as caught:
                    record_error(caught)

    def reap():
        if child is not None:
            child.poll()
        for pid, _ in observed:
            if child is not None and pid == child.pid:
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass  # Still parented by a live owned descendant, or already reaped.

    reason = "controller-disappeared-before-launch"
    returncode = None
    if os.getppid() == parent_pid and not stopped:
        try:
            child = subprocess.Popen(argv)  # noqa: S603, S607 -- original declared argv, inherited streams, no shell
        except OSError as error:
            reason = 'launch-error'
            record_error(error)
    if child is not None:
        observe()
        while child.poll() is None and not stopped and os.getppid() == parent_pid and not errors:
            observe()
            time.sleep(0.02)
        returncode = child.poll()
        reason = (
            "cancelled"
            if stopped
            else "controller-crashed"
            if os.getppid() != parent_pid
            else "observation-error"
            if errors
            else "command-exited"
        )
        if reason == "command-exited":
            deadline = time.monotonic() + 2.0
            while active() and time.monotonic() < deadline and not stopped:
                reap()
                time.sleep(0.02)
            if active():
                reason = "orphan-descendants"
    send(active(), signal.SIGTERM)
    deadline = time.monotonic() + 0.5
    while active() and time.monotonic() < deadline:
        reap()
        time.sleep(0.02)
    deadline = time.monotonic() + 0.5
    while active() and time.monotonic() < deadline:
        send(active(), signal.SIGKILL)
        reap()
        time.sleep(0.02)
    reap()
    survivors = []
    for process in active():
        try:
            survivors.append(
                {
                    "pid": process.pid,
                    "create_time": process.create_time(),
                    "status": process.status(),
                }
            )
        except absent:
            continue
        except (psutil.AccessDenied, OSError) as error:
            record_error(error)
            survivors.append({'pid': process.pid, 'status': 'observation-unavailable'})
    body = {
        "argv": argv,
        "cwd": os.getcwd(),
        "supervisor_pid": os.getpid(),
        "controller_pid": parent_pid,
        "command_pid": child.pid if child is not None else None,
        "reason": reason,
        "command_returncode": returncode,
        "observed_family": list(observed.values()),
        "cleanup_signals": sent,
        "surviving_processes": survivors,
        "observation_errors": errors,
        "cleanup_verified": not survivors and not errors,
    }
    partial = evidence.with_suffix(evidence.suffix + ".partial")
    with partial.open("x") as stream:
        json.dump(body, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.link(partial, evidence)
    partial.unlink()
    if reason == "command-exited" and not survivors and not errors and returncode is not None:
        return returncode if returncode >= 0 else 128 - returncode
    return 70


def validate_process_family(
    path: Path, *, argv: list[str] | None = None, cwd: str | None = None
) -> dict:
    """Require completed, nonempty raw family observations for accepted commands."""
    from openpine.verification.identity import read_json

    family = read_json(path)
    expected = {
        "argv",
        "cwd",
        "supervisor_pid",
        "controller_pid",
        "command_pid",
        "reason",
        "command_returncode",
        "observed_family",
        "cleanup_signals",
        "surviving_processes",
        "observation_errors",
        "cleanup_verified",
    }
    if (
        not isinstance(family, dict)
        or set(family) != expected
        or family["cleanup_verified"] is not True
        or family["reason"] != "command-exited"
        or type(family["command_returncode"]) is not int
        or family["command_returncode"] != 0
        or family["surviving_processes"] != []
        or family["observation_errors"] != []
        or not isinstance(family["observed_family"], list)
        or not family["observed_family"]
        or not isinstance(family["cleanup_signals"], list)
        or any(
            type(family[n]) is not int or family[n] <= 0
            for n in ("supervisor_pid", "controller_pid", "command_pid")
        )
        or not isinstance(family["argv"], list)
        or not family["argv"]
        or any(not isinstance(v, str) for v in family["argv"])
        or not isinstance(family["cwd"], str)
        or not Path(family["cwd"]).is_absolute()
        or (argv is not None and family["argv"] != argv)
        or (cwd is not None and family["cwd"] != cwd)
        or len({family[n] for n in ("supervisor_pid", "controller_pid", "command_pid")}) != 3
    ):
        raise ValueError("incomplete or unsuccessful process-family evidence")
    import math

    identities = []
    for row in family["observed_family"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"pid", "create_time", "name"}
            or type(row["pid"]) is not int
            or row["pid"] <= 0
            or row["pid"] in {family["supervisor_pid"], family["controller_pid"]}
            or type(row["create_time"]) not in {int, float}
            or not math.isfinite(row["create_time"])
            or row["create_time"] <= 0
            or not isinstance(row["name"], str)
            or not row["name"]
        ):
            raise ValueError("invalid process-family observation identity")
        identities.append((row["pid"], row["create_time"]))
    if len(set(identities)) != len(identities) or family["command_pid"] not in {
        p for p, _ in identities
    }:
        raise ValueError("missing or duplicate command process-family observation")
    for row in family['cleanup_signals']:
        if (
            not isinstance(row, dict)
            or set(row) != {'pid', 'create_time', 'signal'}
            or type(row['pid']) is not int
            or type(row['create_time']) not in {int, float}
            or (row['pid'], row['create_time']) not in identities
            or type(row['signal']) is not int
            or row['signal'] not in {signal.SIGTERM, signal.SIGKILL}
        ):
            raise ValueError('invalid process-family cleanup signal identity')
    return family


def process_family_launch_error(path: Path) -> str | None:
    """Preserve infrastructure classification when the inner exec syscall fails."""
    from openpine.verification.identity import read_json

    if not path.is_file():
        return None
    family = read_json(path)
    if family.get('reason') == 'launch-error' and family.get('command_pid') is None:
        return 'declared command launch failed: ' + '; '.join(family['observation_errors'])
    return None


def _stop_group(process: subprocess.Popen, grace: float=2.0) -> None:
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            process.kill()
        process.wait(timeout=grace)
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:  # noqa: S110 -- absent process/resource is already cleaned up
            pass

def run_logged(argv: list[str], *, cwd, output, env: dict[str, str], timeout: int=900, stdin: bytes | None=None, inputs: dict | None=None, binding: dict | None=None, artifacts: dict | Callable[[], dict] | None=None) -> dict:
    """Run a declared command with no shell; retain failed and cancelled evidence."""
    import time
    from datetime import datetime, timezone
    from pathlib import Path
    from openpine.verification.execution_identity import hash_file, write_once_json
    from openpine.verification.identity import seal
    if not argv or any((not isinstance(v, str) or '\x00' in v for v in argv)) or (not Path(argv[0]).is_absolute()) or (type(timeout) is not int) or (timeout < 1):
        raise ValueError('need absolute executable, argv and a positive timeout')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    provenance: dict[str, dict[str, Any]] = {}
    for index, (name, spec) in enumerate((inputs or {}).items()):
        source = Path(spec['path'])
        if source.is_symlink() or hash_file(source) != spec['sha256']:
            raise ValueError('command input provenance differs before execution')
        captured = output / f'input-{index}.bin'
        captured.write_bytes(source.read_bytes())
        if hash_file(captured) != spec['sha256']:
            raise ValueError('command input provenance capture drift')
        provenance[name] = {'source': dict(spec), 'captured': {'path': captured.name, 'sha256': spec['sha256']}}
    def check_tool_aliases():
        import shutil
        for name, row in provenance.items():
            if not name.startswith('owner-tool:'):
                continue
            spec = row['source']
            # Old single-tool callers bind argv directly. Auxiliary tools need
            # the frozen launch alias; a resolved target alone is insufficient.
            alias = spec.get('declared_path')
            if alias is None:
                if len([n for n in provenance if n.startswith('owner-tool:')]) != 1:
                    raise ValueError('command tool provenance lacks declared alias')
                alias = argv[0]
            candidates = [alias]
            if spec.get('is_argv_executable') is True:
                candidates.append(argv[0])
            if name == 'owner-tool:node':
                search = os.get_exec_path(env)
                search = [str(Path(cwd) / p) if not Path(p).is_absolute() else p for p in search]
                candidates.append(shutil.which('node', path=os.pathsep.join(search)))
            elif name == 'owner-tool:chromium':
                candidates.append(env.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'))
            for candidate in candidates:
                if not isinstance(candidate, str) or not Path(candidate).is_absolute():
                    raise ValueError('command tool provenance lacks absolute executable alias')
                target = Path(candidate).resolve(strict=True)
                require_executable(target)
                if target != Path(spec['path']) or hash_file(target) != spec['sha256']:
                    raise ValueError('command tool alias provenance differs from frozen target')

    process, error, status, returncode = (None, None, 'not_run', None)
    started, tick = (datetime.now(timezone.utc).isoformat(), time.perf_counter())
    input_file = output / 'stdin.bin'
    if stdin is not None:
        input_file.write_bytes(stdin)
    try:
        with (output / 'stdout.log').open('xb') as out, (output / 'stderr.log').open('xb') as err:
            with input_file.open('rb') if stdin is not None else open(os.devnull, 'rb') as inp:
                check_tool_aliases()
                process = start_declared_process(argv, family_evidence=output / 'process-family.json', cwd=cwd, env=env, stdin=inp, stdout=out, stderr=err, start_new_session=os.name == 'posix')
                try:
                    returncode = process.wait(timeout=timeout)
                    status = 'completed' if returncode == 0 else 'failed'
                    launch_error = process_family_launch_error(output / 'process-family.json')
                    if launch_error is not None:
                        status, error = ('infrastructure_error', launch_error)
                except subprocess.TimeoutExpired:
                    status = 'timeout'
                    _stop_group(process)
                    returncode = process.poll()
                except KeyboardInterrupt:
                    status = 'cancelled'
                    _stop_group(process)
                    returncode = process.poll()
    except (OSError, ValueError, RuntimeError) as caught:
        status, error = ('infrastructure_error', str(caught))
    finally:
        if process is not None:
            if process.poll() is None:
                _stop_group(process)
            elif os.name == 'posix':
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:  # noqa: S110 -- absent process/resource is already cleaned up
                    pass
                else:
                    _stop_group(process)
                    if status == 'completed':
                        status, error = ('failed', 'orphan process after command exit')
    try:
        check_tool_aliases()
    except (OSError, ValueError, RuntimeError) as caught:
        status, error = ('failed', 'command tool provenance changed or invalid: ' + str(caught))
    for name, row in provenance.items():
        try:
            row['after_sha256'] = hash_file(Path(row['source']['path']))
        except (OSError, ValueError):
            row['after_sha256'] = None
        if row['after_sha256'] != row['source']['sha256']:
            status, error = ('failed', 'command input provenance changed during execution')
    files = {p.name: hash_file(p) for p in output.iterdir() if p.is_file()}
    if callable(artifacts):
        artifacts = artifacts() if status == 'completed' else None
    report = seal({'schema_id': 'openpine.execution_command.v1', 'argv': argv, 'cwd': str(cwd), 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(), 'wall_seconds': time.perf_counter() - tick, 'status': status, 'returncode': returncode, 'error': error, 'files': files, 'ok': status == 'completed' and returncode == 0 and (error is None), 'environment_keys': sorted(env), 'input_provenance': provenance, 'binding': binding, 'artifacts': artifacts, 'full_stage_accepted': False})
    write_once_json(output / 'command.json', report)
    return report


if __name__ == "__main__":
    if len(sys.argv) < 5 or sys.argv[1] != "--supervise-family":
        raise SystemExit("internal declared process-family launch expected")
    raise SystemExit(_supervise_family(sys.argv[4:], int(sys.argv[2]), Path(sys.argv[3])))
