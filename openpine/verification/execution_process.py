"""Bounded subprocess cleanup shared by verification and CI preparation."""

from __future__ import annotations
import os
import select
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast


_FAMILY_LIVE_LIMIT = 4096
_FAMILY_LEDGER_LIMIT = 16384
_FAMILY_OVERFLOW_LIMIT = 4096
_FAMILY_UNVERIFIED_LIMIT = 64


class _OwnedHandle:
    """A retained kernel process identity; integer PIDs are only receipt labels."""

    def __init__(self, pid: int, create_time: float, name: str, fd: int) -> None:
        self.pid, self.create_time, self.name, self.fd = pid, create_time, name, fd

    def alive(self) -> bool:
        return self.fd >= 0 and not select.select([self.fd], [], [], 0)[0]

    def send_signal(self, signum: int) -> None:
        if self.fd < 0:
            raise ProcessLookupError('closed owned process handle')
        signal.pidfd_send_signal(self.fd, signum, None, 0)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _require_pidfd() -> None:
    """Fail before launching work when stable kernel signalling is unavailable."""
    if not sys.platform.startswith('linux') or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise ValueError('complete process-family supervision requires Linux pidfd support')
    try:
        fd = os.pidfd_open(os.getpid(), 0)
        try:
            signal.pidfd_send_signal(fd, 0, None, 0)
        finally:
            os.close(fd)
    except OSError as error:
        raise OSError(error.errno, f'pidfd backend unavailable: {error}') from error


class _FamilyPopen(subprocess.Popen):
    """Retain the guardian's direct-child identity until its poll/wait completes."""

    def __init__(self, argv: list[str], **kwargs) -> None:
        self._family_fd = -1
        self._family_fd_lock = threading.Lock()
        gate_read, gate_write = os.pipe()
        admitted = False
        try:
            argv = [*argv[:7], str(gate_read), *argv[7:]]
            passed = tuple(kwargs.pop('pass_fds', ())) + (gate_read,)
            super().__init__(argv, pass_fds=passed, **kwargs)  # noqa: S603, S607 -- checked guardian argv, no shell
            os.close(gate_read)
            gate_read = -1
            try:
                # This direct child has not been reaped or exposed to a caller.
                self._family_fd = os.pidfd_open(self.pid, 0)
                # The guardian cannot launch the declared command until stable
                # parent-side ownership has succeeded.
                os.write(gate_write, b'1')
                # Include the final I/O in the same handoff cancellation scope.
                # Clear the descriptor before close: Linux has already released
                # it if a signal interrupts the Python return from that syscall.
                closing_gate, gate_write = gate_write, -1
                os.close(closing_gate)
                admitted = True
            except (OSError, KeyboardInterrupt) as error:
                error_number = error.errno if isinstance(error, OSError) else None
                if gate_write >= 0:
                    failed_gate, gate_write = gate_write, -1
                    os.close(failed_gate)
                try:
                    for signum in (signal.SIGTERM, signal.SIGKILL):
                        if self._family_fd >= 0:
                            self.signal_family_guardian(signum)
                        else:
                            # Only this private, direct unreaped child: it cannot
                            # recycle its PID before this constructor reaps it.
                            super().send_signal(signum)
                        try:
                            self.wait(timeout=3)
                            break
                        except subprocess.TimeoutExpired:
                            if signum == signal.SIGKILL:
                                raise
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    raise OSError(error_number, f'guardian handoff failed; guardian {self.pid} cleanup unverified: {cleanup_error}') from error
                if isinstance(error, KeyboardInterrupt):
                    raise
                raise OSError(error_number, f'guardian pidfd acquisition failed; launch gate refused; guardian {self.pid} reaped with returncode {self.returncode}: {error}') from error
        finally:
            if gate_read >= 0:
                os.close(gate_read)
            if gate_write >= 0:
                os.close(gate_write)
            if not admitted:
                self._close_family_fd()

    def _close_family_fd(self) -> None:
        with self._family_fd_lock:
            if self._family_fd >= 0:
                os.close(self._family_fd)
                self._family_fd = -1

    def poll(self) -> int | None:
        result = super().poll()
        if result is not None:
            self._close_family_fd()
        return result

    def wait(self, timeout: float | None = None) -> int:
        result = super().wait(timeout=timeout)
        self._close_family_fd()
        return result

    def signal_family_guardian(self, signum: int) -> None:
        with self._family_fd_lock:
            if self._family_fd >= 0:
                try:
                    signal.pidfd_send_signal(self._family_fd, signum, None, 0)
                except ProcessLookupError:
                    pass  # The retained guardian already exited.


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
        _require_pidfd()
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
        return _FamilyPopen(argv, **kwargs)
    return subprocess.Popen(argv, **kwargs)  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked


def _supervise_family(argv: list[str], parent_pid: int, evidence: Path, *, launch_gate: int | None = None) -> int:
    """Own descendants through live lineage and stable kernel handles.

    This process is a per-command Linux subreaper, never a host-wide reaper.
    Enumeration and receipt identities confer no signalling authority. Every
    newly discovered task needs a pidfd and a live ancestry chain to this owner.
    """
    import ctypes
    import json
    import time
    from importlib import import_module

    _require_pidfd()
    psutil = cast(_FamilyObserver, import_module('psutil'))
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_CHILD_SUBREAPER and PR_SET_PDEATHSIG are unprivileged Linux APIs.
    if libc.prctl(36, 1, 0, 0, 0) != 0 or libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'cannot establish owned process-family supervisor')
    stopped = 0

    def stop(signum, frame):
        nonlocal stopped
        stopped = signum

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    owner = psutil.Process()
    owner_pid = os.getpid()
    observed: dict[tuple[int, float], dict[str, Any]] = {}
    handles: dict[int, _OwnedHandle] = {}
    overflow: dict[int, _OwnedHandle] = {}
    unverified: dict[tuple[int, float], dict[str, Any]] = {}
    omitted_overflow_observations = 0
    pressure_started = False
    sent: list[dict[str, Any]] = []
    sent_keys: set[tuple[int, float, int]] = set()
    errors: list[str] = []
    child = None
    absent = (psutil.NoSuchProcess, FileNotFoundError, ProcessLookupError)

    def record_error(error):
        message = f'{type(error).__name__}: {error}'[:512]
        if len(errors) < 8 and message not in errors:
            errors.append(message)

    boot_time = next(
        int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
        if line.startswith('btime ')
    )
    clock_ticks = os.sysconf('SC_CLK_TCK')

    def stat(pid: int) -> tuple[str, str, int, int]:
        # comm may contain parentheses and arbitrary non-UTF8 bytes.
        raw = (Path('/proc') / str(pid) / 'stat').read_bytes()
        end = raw.rfind(b')')
        fields = raw[end + 2:].split()
        return os.fsdecode(raw[raw.find(b'(') + 1:end]), fields[0].decode('ascii'), int(fields[1]), int(fields[19])

    def fd_pid(fd: int) -> int:
        # A dead pidfd reports Pid:-1, even after its integer label is recycled.
        return next(int(line.split()[1]) for line in (Path('/proc/self/fdinfo') / str(fd)).read_text().splitlines() if line.startswith('Pid:'))

    def capture_owned(pid: int, *, command: bool = False) -> _OwnedHandle | None:
        if pid in {owner_pid, parent_pid} or pid <= 1:
            record_error(RuntimeError('enumeration included a non-owned controller or supervisor'))
            return None
        # Hold every ancestor pidfd until all edges have been rechecked. A live
        # parent cannot be replaced under its child's stable real-parent link.
        chain: list[tuple[int, int, tuple[str, str, int, int]]] = []
        retained = False
        try:
            cursor = pid
            while cursor != owner_pid:
                if cursor <= 1 or cursor == parent_pid or any(p == cursor for p, _, _ in chain):
                    record_error(RuntimeError('enumerated process lacks live owned lineage'))
                    return None
                if len(chain) >= _FAMILY_LIVE_LIMIT:
                    record_error(RuntimeError('owned process-family lineage budget exceeded'))
                    return None
                fd = os.pidfd_open(cursor, 0)
                try:
                    row = stat(cursor)
                except (OSError, ValueError):
                    os.close(fd)
                    raise
                chain.append((cursor, fd, row))
                # A just-launched direct child may already be a zombie. Popen
                # has not reaped it, so its direct-child identity is still owned.
                if fd_pid(fd) != cursor or (select.select([fd], [], [], 0)[0] and not (command and cursor == pid and row[2] == owner_pid)):
                    return None
                cursor = row[2]
            for cursor, fd, row in chain:
                current = stat(cursor)
                if current[2:] != row[2:] or fd_pid(fd) != cursor:
                    return None
                if select.select([fd], [], [], 0)[0] and not (command and cursor == pid and current[2] == owner_pid):
                    return None
            _, fd, row = chain[0]
            handle = _OwnedHandle(pid, boot_time + row[3] / clock_ticks, row[0], fd)
            retained = True
            return handle
        except absent:
            return None
        except (psutil.AccessDenied, OSError, ValueError) as error:
            record_error(error)
            return None
        finally:
            for index, (_, fd, _) in enumerate(chain):
                if not retained or index != 0:
                    os.close(fd)

    def diagnose(process: _OwnedHandle) -> None:
        # These are diagnostics, never authority to signal an integer PID.
        try:
            metadata = psutil.Process(process.pid)
            metadata.create_time()
            metadata.name()
            metadata.status()
        except absent:
            pass
        except (psutil.AccessDenied, OSError) as error:
            record_error(error)

    def pressure_cleanup(handle: _OwnedHandle) -> None:
        # Once the receipt budget is exhausted, new verified-owned tasks still
        # receive stable-handle cleanup. Failure is explicit; no accepted receipt
        # can pretend the bounded ledger is a complete successful observation.
        nonlocal pressure_started, omitted_overflow_observations
        if not pressure_started:
            pressure_started = True
            send(list(handles.values()), signal.SIGTERM)
        try:
            handle.send_signal(signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            record_error(error)
        finally:
            # One global cleanup deadline covers the whole family. Retain live
            # overflow pidfds, including denied signals, rather than waiting a
            # separate half-second per discovered task or hiding survivors.
            if not handle.alive():
                handle.close()
            elif len(overflow) < _FAMILY_OVERFLOW_LIMIT:
                overflow[handle.pid] = handle
            else:
                key = (handle.pid, handle.create_time)
                if key in unverified or len(unverified) < _FAMILY_UNVERIFIED_LIMIT:
                    unverified[key] = {'pid': handle.pid, 'create_time': handle.create_time,
                                       'status': 'pidfd-exit-unverified'}
                else:
                    omitted_overflow_observations += 1
                handle.close()

    def admit(pid: int, *, command: bool = False) -> None:
        existing = handles.get(pid) or overflow.get(pid)
        if existing is not None:
            if existing.alive():
                return
            existing.close()
            handles.pop(pid, None)
            overflow.pop(pid, None)
        handle = capture_owned(pid, command=command)
        if handle is None:
            return
        key = (handle.pid, handle.create_time)
        if key in observed:
            record_error(RuntimeError('reused process receipt identity cannot be admitted'))
            pressure_cleanup(handle)
            return
        if len(observed) >= _FAMILY_LEDGER_LIMIT or len(handles) >= _FAMILY_LIVE_LIMIT:
            record_error(RuntimeError('owned process-family cumulative ledger budget exceeded' if len(observed) >= _FAMILY_LEDGER_LIMIT else 'owned process-family live handle budget exceeded'))
            pressure_cleanup(handle)
            return
        observed[key] = {'pid': pid, 'create_time': handle.create_time, 'name': handle.name}
        handles[pid] = handle
        diagnose(handle)

    def observe() -> list[_OwnedHandle]:
        # Reconcile retained stable identities on EVERY path, even a successful
        # empty psutil traversal after an intermediate ancestor exits.
        for pool in (handles, overflow):
            for pid, handle in list(pool.items()):
                if not handle.alive():
                    if child is None or pid != child.pid:
                        try:
                            os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            pass  # Descendant's live parent has already reaped it.
                    handle.close()
                    del pool[pid]
        try:
            descendants = owner.children(recursive=True)
            if len(descendants) > _FAMILY_LIVE_LIMIT:
                record_error(RuntimeError('owned process-family enumeration budget exceeded'))
            for process in descendants:
                admit(process.pid)
        except (psutil.AccessDenied, OSError, RuntimeError) as error:
            record_error(error)
        # Streaming kernel enumeration catches newly adopted direct children
        # without an unbounded fallback PID set or a reliance on old membership.
        try:
            with os.scandir('/proc') as entries:
                for entry in entries:
                    if not entry.name.isdecimal():
                        continue
                    pid = int(entry.name)
                    try:
                        if stat(pid)[2] == owner_pid:
                            admit(pid)
                    except absent:
                        continue
                    except (OSError, ValueError) as error:
                        record_error(error)
        except OSError as error:
            record_error(error)
        active = []
        for handle in (*handles.values(), *overflow.values()):
            if handle.alive():
                diagnose(handle)
                active.append(handle)
        return active

    def send(processes: list[_OwnedHandle], signum: int) -> None:
        for handle in processes:
            key = (handle.pid, handle.create_time, int(signum))
            if key in sent_keys:
                continue
            try:
                handle.send_signal(signum)
                if key[:2] in observed:
                    sent_keys.add(key)
                    sent.append({'pid': handle.pid, 'create_time': handle.create_time, 'signal': signum})
            except ProcessLookupError:
                continue
            except OSError as error:
                record_error(error)

    def reap():
        if child is not None:
            child.poll()
        # waitpid only targets direct owned children. PID reuse cannot cause it
        # to reap or signal an unrelated process.
        for pid in (*handles, *overflow):
            if child is not None and pid == child.pid:
                continue
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    reason = 'controller-disappeared-before-launch'
    returncode = None
    launch_allowed = launch_gate is None
    if launch_gate is not None:
        try:
            launch_allowed = os.read(launch_gate, 1) == b'1'
        finally:
            os.close(launch_gate)
        if not launch_allowed:
            reason = 'launch-gate-refused'
            record_error(RuntimeError('guardian launch gate was not admitted'))
    if launch_allowed and os.getppid() == parent_pid and not stopped:
        try:
            child = subprocess.Popen(argv)  # noqa: S603, S607 -- declared argv, inherited streams, no shell
        except OSError as error:
            reason = 'launch-error'
            record_error(error)
    if child is not None:
        admit(child.pid, command=True)
        observe()
        while child.poll() is None and not stopped and os.getppid() == parent_pid and not errors:
            observe()
            time.sleep(.02)
        returncode = child.poll()
        reason = ('cancelled' if stopped else 'controller-crashed' if os.getppid() != parent_pid
                  else 'observation-error' if errors else 'command-exited')
        if reason == 'command-exited':
            deadline = time.monotonic() + 2.0
            while observe() and time.monotonic() < deadline and not stopped and not errors:
                reap()
                time.sleep(.02)
            if observe():
                reason = 'orphan-descendants'
    send(observe(), signal.SIGTERM)
    deadline = time.monotonic() + .5
    while observe() and time.monotonic() < deadline:
        reap()
        time.sleep(.02)
    deadline = time.monotonic() + .5
    while observe() and time.monotonic() < deadline:
        send(observe(), signal.SIGKILL)
        reap()
        time.sleep(.02)
    reap()
    survivors = [{'pid': handle.pid, 'create_time': handle.create_time, 'status': 'pidfd-live'} for handle in observe()]
    survivors.extend(unverified.values())
    if omitted_overflow_observations:
        message = f'overflow survivor inventory incomplete: {omitted_overflow_observations} additional unverified observations'
        if len(errors) == 8:
            errors[-1] = message
        else:
            errors.append(message)
    body = {
        'argv': argv, 'cwd': os.getcwd(), 'supervisor_pid': owner_pid, 'controller_pid': parent_pid,
        'command_pid': child.pid if child is not None else None, 'reason': reason,
        'command_returncode': returncode, 'observed_family': list(observed.values()),
        'cleanup_signals': sent, 'surviving_processes': survivors, 'observation_errors': errors,
        'cleanup_verified': not survivors and not errors,
    }
    for handle in (*handles.values(), *overflow.values()):
        handle.close()
    partial = evidence.with_suffix(evidence.suffix + '.partial')
    with partial.open('x') as stream:
        json.dump(body, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.link(partial, evidence)
    partial.unlink()
    if reason == 'command-exited' and not survivors and not errors and returncode is not None:
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
        or len(family["observed_family"]) > _FAMILY_LEDGER_LIMIT
        or not isinstance(family["cleanup_signals"], list)
        or len(family["cleanup_signals"]) > 2 * len(family["observed_family"])
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
    signal_keys = set()
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
        key = (row['pid'], row['create_time'], row['signal'])
        if key in signal_keys:
            raise ValueError('duplicate process-family cleanup signal identity')
        signal_keys.add(key)
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
    if isinstance(process, _FamilyPopen):
        process.signal_family_guardian(signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            process.signal_family_guardian(signal.SIGKILL)
            process.wait(timeout=grace)
        return
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
    except KeyboardInterrupt:
        status = 'cancelled'
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
    if len(sys.argv) < 6 or sys.argv[1] != "--supervise-family":
        raise SystemExit("internal declared process-family launch expected")
    raise SystemExit(_supervise_family(sys.argv[5:], int(sys.argv[2]), Path(sys.argv[3]), launch_gate=int(sys.argv[4])))
