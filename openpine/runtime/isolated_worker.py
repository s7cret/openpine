"""OS-isolated execution of generated artifact bytes.

Threat model: generated code is untrusted. The parent/gateway process never
imports it. The child receives already-captured bytes on stdin (no path reread).
Isolation is bubblewrap: new net/pid namespaces, read-only /usr, empty tmpfs
scratch, cleared environment. There is no in-process fallback.
"""

from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import os
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ast2python.artifacts import verify_generated_artifact_v3
from ast2python.errors import BundleInvariantError
from openpine_contracts import validate_payload, verify_content_hash
from openpine_contracts.errors import SchemaValidationError

from openpine.runtime.cgroup import CgroupError, attach_worker_tree, prepare_worker_cgroup
from openpine.runtime.worker_protocol import WorkerProtocolError, WorkerProtocolTranscript

ExecutionContext = dict[str, Any]
AdmittedManifest = Mapping[str, Any]


def htf_bars_for_bootstrap(*, bulk_backtest: bool, htf_bars: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Bulk bootstrap cannot carry HTF series; INTERACTIVE still gets the stamped rows."""

    if bulk_backtest:
        return []
    return list(htf_bars or [])

BWRAP = "/usr/bin/bwrap"
SANDBOX_PYTHON = "/usr/bin/python3"
WORKER_USER = "openpine-worker"
TMPFS_BYTES = 16 * 1024 * 1024
TRUSTED_DEST = "/tmp/openpine-trusted"
WORKER_LINE_LIMIT_BYTES = 10_000_000

# Child bootstrap is stdlib-only. Host env and host home are not visible.
_BOOTSTRAP = (
    f"import sys\nsys.path.insert(0, {TRUSTED_DEST!r})\n"
    + r"""
import json

from openpine_rc6_worker_runtime import RC6WorkerProtocol, run_bulk, run_interactive


def main():
    request = json.loads(sys.stdin.readline())
    if request.get("interactive") is not True:
        raise RuntimeError("RC6 isolated worker requires interactive protocol execution")
    context = request.get("execution_context")
    if not isinstance(context, dict):
        raise RuntimeError("RC6 execution context is required")
    if request.get("stack_id") != context.get("stack_manifest_hash"):
        raise RuntimeError("RC6 request stack identity mismatch")
    protocol = RC6WorkerProtocol(context)
    if request.get("bulk_backtest") is True:
        return run_bulk(request, protocol)
    return run_interactive(request, protocol)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        json.dump(
            {"error_type": type(exc).__name__, "detail": str(exc)},
            sys.stdout,
            separators=(",", ":"),
            sort_keys=True,
        )
        sys.stdout.write("\n")
        sys.stdout.flush()
        raise SystemExit(1)
"""
)


class IsolatedWorkerError(RuntimeError):
    """Typed failure from the isolated generated-code worker."""


_TRUSTED_STAGE: Path | None = None
_TRUSTED_NAMES = (
    "ast2python",
    "attr",
    "attrs",
    "backtest_engine",
    "jsonschema",
    "jsonschema_specifications",
    "marketdata_provider",
    "msgpack",
    "openpine_rc6_worker_runtime",
    "openpine_contracts",
    "pinelib",
    "referencing",
    "rpds",
    "typing_extensions",
)
_RUNTIME_ROOTS = ("/usr", "/lib", "/lib64")


def _chmod_tree(root: Path) -> None:
    root.chmod(0o755)
    for path in root.rglob("*"):
        mode = 0o755 if path.is_dir() else 0o644
        path.chmod(mode | stat.S_IROTH | (stat.S_IXOTH if path.is_dir() else 0))


def _cleanup_trusted_stage() -> None:
    global _TRUSTED_STAGE
    stage = _TRUSTED_STAGE
    _TRUSTED_STAGE = None
    if stage is not None:
        shutil.rmtree(stage, ignore_errors=True)


atexit.register(_cleanup_trusted_stage)


def _stage_trusted_packages() -> list[tuple[str, str]]:
    global _TRUSTED_STAGE
    dest_root = Path(TRUSTED_DEST)
    if _TRUSTED_STAGE is None:
        stage = Path(tempfile.mkdtemp(prefix="openpine-trusted-"))
        try:
            stage.chmod(0o755)
            for name in _TRUSTED_NAMES:
                if name == "openpine_rc6_worker_runtime":
                    source = Path(__file__).with_name("rc6_worker_runtime.py")
                    target = stage / f"{name}.py"
                    shutil.copy2(source, target)
                    target.chmod(0o644 | stat.S_IROTH)
                    continue
                spec = importlib.util.find_spec(name)
                if spec is None or not spec.origin:
                    continue
                origin = Path(spec.origin).resolve()
                if spec.submodule_search_locations is None:
                    target = stage / origin.name
                    shutil.copy2(origin, target)
                    target.chmod(0o644 | stat.S_IROTH)
                else:
                    src = origin.parent
                    target = stage / name
                    shutil.copytree(
                        src,
                        target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                    _chmod_tree(target)
            _TRUSTED_STAGE = stage
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return [(str(_TRUSTED_STAGE), str(dest_root))]


def worker_user_uid() -> int | None:
    try:
        probe = subprocess.run(  # noqa: S603
            ["/usr/bin/sudo", "-n", "-u", WORKER_USER, "--", "/usr/bin/id", "-u"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0 or not probe.stdout.strip().isdigit():
        return None
    uid = int(probe.stdout.strip())
    return uid if uid > 0 else None


def worker_user_available() -> bool:
    return worker_user_uid() is not None


def _runtime_ro_bind_args() -> list[str]:
    argv: list[str] = []
    roots = [Path(root) for root in _RUNTIME_ROOTS if Path(root).exists()]
    python_prefix = Path(sys.base_prefix).resolve()
    if not any(python_prefix.is_relative_to(root.resolve()) for root in roots):
        roots.append(python_prefix)
    for root in roots:
        argv.extend(["--ro-bind", str(root), str(root)])
    return argv


def _resolved_worker_policy(admitted_manifest: AdmittedManifest) -> dict[str, Any]:
    if not isinstance(admitted_manifest, Mapping):
        raise IsolatedWorkerError("sealed admitted manifest is required")
    manifest_hash = admitted_manifest.get("manifest_hash")
    if not isinstance(manifest_hash, str) or not manifest_hash.startswith("sha256:"):
        raise IsolatedWorkerError("sealed admitted manifest hash is required")
    policy = admitted_manifest.get("worker_policy")
    if not isinstance(policy, Mapping):
        raise IsolatedWorkerError("admitted worker policy is required")
    required = {
        "bubblewrap_path",
        "python_path",
        "worker_user",
        "tmpfs_bytes",
        "memory_max_bytes",
        "tasks_max",
        "trusted_packages",
    }
    if set(policy) != required:
        raise IsolatedWorkerError("admitted worker policy fields are invalid")
    if policy.get("python_path") != "candidate-python":
        raise IsolatedWorkerError("admitted sandbox Python policy is invalid")
    trusted = policy.get("trusted_packages")
    if trusted != list(_TRUSTED_NAMES):
        raise IsolatedWorkerError("admitted trusted package policy is invalid")
    resolved = dict(policy)
    base_executable = getattr(sys, "_base_executable", sys.executable)
    resolved["python_path"] = str(Path(base_executable).resolve())
    resolved["trusted_package_binds"] = _stage_trusted_packages()
    return resolved


def _bwrap_argv(
    admitted_manifest: AdmittedManifest, unit_name: str | None = None
) -> list[str]:
    policy = _resolved_worker_policy(admitted_manifest)
    if not Path(str(policy["bubblewrap_path"])).is_file():
        raise IsolatedWorkerError("bubblewrap is required for isolated execution")
    if not Path(str(policy["python_path"])).is_file():
        raise IsolatedWorkerError("sandbox python is missing")
    if worker_user_uid() is None:
        raise IsolatedWorkerError("dedicated openpine-worker user is required")
    unit = unit_name or "openpine-worker-sandbox-test"
    prefix = [
        "/usr/bin/sudo",
        "-n",
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--pipe",
        "--service-type=exec",
        f"--unit={unit}",
        f"--uid={policy['worker_user']}",
        f"--property=MemoryMax={policy['memory_max_bytes']}",
        "--property=MemorySwapMax=0",
        f"--property=TasksMax={policy['tasks_max']}",
        "--property=CPUQuota=100%",
        "--property=KillMode=control-group",
        "--property=OOMPolicy=kill",
        "--property=SystemCallFilter=@system-service @mount",
        "--",
    ]
    argv = prefix + [
        str(policy["bubblewrap_path"]),
        *_runtime_ro_bind_args(),
        "--size",
        str(policy["tmpfs_bytes"]),
        "--tmpfs",
        "/tmp",
    ]
    for src, dest in policy["trusted_package_binds"]:
        argv.extend(["--ro-bind", src, dest])
    return argv + [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--remount-ro",
        "/",
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TZ",
        "UTC",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "PYTHONHASHSEED",
        "0",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--chdir",
        "/tmp",
        str(policy["python_path"]),
        "-I",
        "-c",
        _BOOTSTRAP,
    ]


def _worker_unit_name() -> str:
    return f"openpine-worker-{uuid.uuid4().hex}"


_PENDING_WORKER_UNITS: set[str] = set()
_PENDING_WORKER_UNITS_LOCK = threading.Lock()


def _retain_pending_worker_unit(unit_name: str) -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        _PENDING_WORKER_UNITS.add(unit_name)


def _discard_pending_worker_unit(unit_name: str) -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        _PENDING_WORKER_UNITS.discard(unit_name)


def _stop_worker_unit(unit_name: str) -> None:
    def run_systemctl(
        *args: str, capture_stdout: bool = False
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(  # noqa: S603
                ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", *args, unit_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolatedWorkerError("worker unit cleanup failed") from exc

    def active_state() -> str:
        completed = run_systemctl(
            "show", "--property=ActiveState", "--value", capture_stdout=True
        )
        if completed.returncode != 0:
            raise IsolatedWorkerError("worker unit state verification failed")
        state = (completed.stdout or "").strip()
        if not state:
            raise IsolatedWorkerError("worker unit state verification was empty")
        return state

    stop_error: IsolatedWorkerError | None = None
    try:
        run_systemctl("stop")
    except IsolatedWorkerError as exc:
        stop_error = exc

    try:
        if active_state() == "inactive":
            _discard_pending_worker_unit(unit_name)
            return
    except IsolatedWorkerError:
        pass

    kill_error: IsolatedWorkerError | None = None
    try:
        run_systemctl("kill", "--kill-who=all", "--signal=KILL")
    except IsolatedWorkerError as exc:
        kill_error = exc

    try:
        final_state = active_state()
    except IsolatedWorkerError as exc:
        _retain_pending_worker_unit(unit_name)
        raise IsolatedWorkerError("worker unit cleanup could not be verified") from (
            kill_error or stop_error or exc
        )
    if final_state != "inactive":
        _retain_pending_worker_unit(unit_name)
        raise IsolatedWorkerError("worker unit remained active after cleanup") from (
            kill_error or stop_error
        )
    _discard_pending_worker_unit(unit_name)


def _retry_pending_worker_unit_cleanup() -> None:
    with _PENDING_WORKER_UNITS_LOCK:
        pending = tuple(_PENDING_WORKER_UNITS)
    for unit_name in pending:
        try:
            _stop_worker_unit(unit_name)
        except IsolatedWorkerError:
            pass


atexit.register(_retry_pending_worker_unit_cleanup)


def _close_process_pipes(proc: subprocess.Popen[Any]) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is None or getattr(pipe, "closed", False):
            continue
        try:
            pipe.close()
        except (BrokenPipeError, OSError):
            continue


def _reap_worker_process_bounded(
    proc: subprocess.Popen[Any], timeout: float = 2.0
) -> None:
    cleanup_error: BaseException | None = None
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup_error = exc
    finally:
        _close_process_pipes(proc)
    if cleanup_error is not None:
        raise IsolatedWorkerError("worker process cleanup did not complete") from cleanup_error


def _cleanup_worker_process(proc: subprocess.Popen[Any], unit_name: str) -> None:
    process_kill_error: OSError | None = None
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError as exc:
            process_kill_error = exc
    unit_error: IsolatedWorkerError | None = None
    try:
        _stop_worker_unit(unit_name)
    except IsolatedWorkerError as exc:
        unit_error = exc
    reap_error: IsolatedWorkerError | None = None
    try:
        _reap_worker_process_bounded(proc)
    except IsolatedWorkerError as exc:
        reap_error = exc
    if unit_error is not None:
        raise unit_error from (reap_error or process_kill_error)
    if reap_error is not None:
        raise reap_error from process_kill_error


def _read_available_stderr(proc: subprocess.Popen[Any], limit: int = 1_000_000) -> str:
    pipe = proc.stderr
    if pipe is None or pipe.closed:
        return ""
    chunks = bytearray()
    while len(chunks) < limit:
        ready, _, _ = select.select([pipe], [], [], 0)
        if not ready:
            break
        try:
            chunk = os.read(pipe.fileno(), min(65_536, limit - len(chunks)))
        except OSError:
            break
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks).decode("utf-8", errors="replace")


def _validate_interactive_generated_artifact(
    source: bytes,
    generated_artifact: Mapping[str, Any],
    execution_context: Mapping[str, Any],
) -> dict[str, str]:
    """Validate the exact V3 module admitted for an interactive worker."""

    try:
        validate_payload("openpine.generated_artifact.v3", generated_artifact)
        verify_generated_artifact_v3(generated_artifact)
    except (SchemaValidationError, BundleInvariantError) as exc:
        raise IsolatedWorkerError("generated artifact is invalid") from exc
    emitted_hash = "sha256:" + hashlib.sha256(source).hexdigest()
    producer = generated_artifact.get("producer")
    entrypoint = generated_artifact.get("entrypoint")
    execution_commits = execution_context.get("producer_commits")
    if (
        execution_context.get("generated_artifact_hash")
        != generated_artifact.get("content_hash")
        or execution_context.get("source_hash")
        != generated_artifact.get("source_hash")
        or execution_context.get("emitted_module_hash") != emitted_hash
        or generated_artifact.get("emitted_module_hash") != emitted_hash
        or not isinstance(producer, Mapping)
        or not isinstance(execution_commits, Mapping)
        or producer.get("commit") != execution_commits.get("ast2python")
        or not isinstance(entrypoint, Mapping)
    ):
        raise IsolatedWorkerError("generated artifact admission identity mismatch")
    from openpine.runtime.strategy_host import StrategyHostError, validate_strategy_host
    try:
        validate_strategy_host(source, generated_artifact["version_context"]["pine_version"])
    except StrategyHostError as exc:
        raise IsolatedWorkerError(f"{exc.code}: {exc}") from exc
    module_name = entrypoint.get("module")
    class_name = entrypoint.get("class")
    if not isinstance(module_name, str) or class_name != "GeneratedScript":
        raise IsolatedWorkerError("generated artifact entrypoint identity mismatch")
    return {
        "artifact_hash": str(generated_artifact["content_hash"]),
        "module_hash": emitted_hash,
        "entrypoint_module": module_name,
        "entrypoint_class": class_name,
    }


class InteractiveWorkerSession:
    """Persistent protocol-v2 worker: one broker projection and bar per message."""

    def __init__(
        self,
        source: bytes,
        execution_context: ExecutionContext,
        instrument_id: str,
        admitted_manifest: AdmittedManifest,
        generated_artifact: dict[str, Any],
        run_hash: str,
        protocol_artifact_dir: str | Path,
        *,
        semantic_profile: str,
        chart_timeframe: str,
        params: dict[str, Any] | None = None,
        htf_bars: list[dict[str, Any]] | None = None,
        timeout_s: float = 5.0,
        cgroup_dir: str | Path | None = None,
        bulk_backtest: bool = False,
        engine_config: Mapping[str, Any] | None = None,
        bulk_idle_timeout_s: float = 60.0,
    ) -> None:
        if len(source) > 500_000:
            raise IsolatedWorkerError("artifact source exceeds size limit")
        try:
            validate_payload("openpine.execution_context.v1", execution_context)
        except ValueError as exc:
            raise IsolatedWorkerError("execution_context is invalid") from exc
        if not verify_content_hash(
            execution_context, schema_id="openpine.execution_context.v1"
        ):
            raise IsolatedWorkerError("execution_context content hash is invalid")
        load_identity = _validate_interactive_generated_artifact(
            source,
            generated_artifact,
            execution_context,
        )
        if (
            not isinstance(run_hash, str)
            or not run_hash.startswith("sha256:")
            or run_hash == "sha256:" + "0" * 64
        ):
            raise IsolatedWorkerError("sealed run hash is required")
        stack_manifest_hash = execution_context.get("stack_manifest_hash")
        if stack_manifest_hash != execution_context.get("stack_id"):
            raise IsolatedWorkerError("execution_context stack manifest identity mismatch")
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise IsolatedWorkerError("instrument identity is required")
        if semantic_profile not in {"legacy_4x", "strict_5x"}:
            raise IsolatedWorkerError("semantic_profile required")
        if not isinstance(chart_timeframe, str) or not chart_timeframe.strip():
            raise IsolatedWorkerError("chart_timeframe required")
        if params is not None and not isinstance(params, dict):
            raise IsolatedWorkerError("params must be an object")
        from openpine.runtime.inputs import InputBindingError, resolve_inputs
        try:
            self.input_registry = resolve_inputs(source, params, envelope=generated_artifact)
        except InputBindingError as exc:
            raise IsolatedWorkerError(f"{exc.code}: {exc}") from exc
        if not str(protocol_artifact_dir):
            raise IsolatedWorkerError("protocol artifact directory is required")
        if bulk_backtest and not isinstance(engine_config, Mapping):
            raise IsolatedWorkerError("bulk backtest engine config is required")
        self.timeout_s = timeout_s
        self.bulk_idle_timeout_s = float(bulk_idle_timeout_s)
        self.bulk_backtest = bool(bulk_backtest)
        self.engine_config = dict(engine_config) if engine_config is not None else {}
        self.max_line_bytes = 1_000_000
        self._closed = False
        self._stdout_buffer = bytearray()
        self.unit_name = _worker_unit_name()
        self.bytes_sent = 0
        self.bytes_received = 0
        self.generated_artifact = dict(generated_artifact)
        self.load_identity = load_identity
        self.run_hash = run_hash
        self.protocol_artifact_dir = Path(protocol_artifact_dir)
        self.protocol_artifact_dir.mkdir(parents=True, exist_ok=True)
        self.protocol = WorkerProtocolTranscript(execution_context)
        self._last_commit: dict[str, Any] | None = None
        if cgroup_dir is not None:
            try:
                prepare_worker_cgroup(cgroup_dir)
            except CgroupError as exc:
                raise IsolatedWorkerError(str(exc)) from exc
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                _bwrap_argv(admitted_manifest, self.unit_name),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise IsolatedWorkerError("worker spawn failed") from exc
        if cgroup_dir is not None:
            try:
                attach_worker_tree(cgroup_dir, self.proc.pid)
            except CgroupError as exc:
                self._kill()
                raise IsolatedWorkerError(str(exc)) from exc
        try:
            bootstrap = {
                "interactive": True,
                "bulk_backtest": self.bulk_backtest,
                "source": source.decode("utf-8"),
                "stack_id": stack_manifest_hash,
                "execution_context": execution_context,
                "generated_artifact": generated_artifact,
                "instrument_id": instrument_id,
                "semantic_profile": semantic_profile,
                "chart_timeframe": chart_timeframe,
                "htf_bars": htf_bars_for_bootstrap(
                    bulk_backtest=self.bulk_backtest, htf_bars=htf_bars
                ),
                "params": dict(self.input_registry.values),
                "input_values_hash": self.input_registry.values_hash,
            }
            if self.engine_config:
                bootstrap["engine_config"] = self.engine_config
            self._write_bootstrap(bootstrap)
            hello = self._read_message()
            if hello.get("kind") != "HELLO":
                self._raise_response(hello)
            self.hello = hello
            load = self.protocol.append(
                "LOAD_ARTIFACT",
                dict(self.load_identity),
                created_at_utc_ms=0,
            )
            self._write_message(load)
            init = self.protocol.append(
                "INIT_RUN",
                {
                    "run_id": execution_context["run_id"],
                    "run_hash": run_hash,
                    "execution_context_hash": execution_context["content_hash"],
                    "execution_context": execution_context,
                    "semantic_profile": semantic_profile,
                    "capabilities": ["closed_bar", "checkpoint_v1"],
                },
                created_at_utc_ms=0,
            )
            self._write_message(init)
        except Exception:
            self._kill()
            raise

    def _kill(self) -> None:
        if getattr(self, "proc", None) is None:
            return
        try:
            _cleanup_worker_process(self.proc, self.unit_name)
        finally:
            self._closed = True

    def _close_pipes(self) -> None:
        _close_process_pipes(self.proc)

    def _write_json_line(self, payload: dict[str, Any]) -> None:
        self._write_serialized_json_line(json.dumps(payload, separators=(",", ":")))

    def _write_serialized_json_line(self, payload: str) -> None:
        if self._closed or self.proc.stdin is None:
            raise IsolatedWorkerError("interactive worker is closed")
        if not isinstance(payload, str) or "\n" in payload or "\r" in payload:
            raise IsolatedWorkerError("serialized worker message must be one JSON line")
        encoded = payload + "\n"
        encoded_size = len(encoded.encode("utf-8"))
        if encoded_size > WORKER_LINE_LIMIT_BYTES:
            raise IsolatedWorkerError("interactive message exceeds size limit")
        try:
            self.proc.stdin.write(encoded)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            detail = _read_available_stderr(self.proc).strip()
            suffix = f": {detail}" if detail else ""
            raise IsolatedWorkerError(
                f"interactive worker pipe closed{suffix}"
            ) from exc
        self.bytes_sent += encoded_size

    def _write_bootstrap(self, payload: dict[str, Any]) -> None:
        self._write_json_line(payload)

    def _write_message(self, payload: dict[str, Any]) -> None:
        try:
            validate_payload("openpine.worker.protocol.v2", payload)
        except ValueError as exc:
            raise IsolatedWorkerError("invalid worker protocol message") from exc
        if not verify_content_hash(payload, schema_id="openpine.worker.protocol.v2"):
            raise IsolatedWorkerError("worker protocol message content hash is invalid")
        self._write_json_line(payload)

    def _read_message(self, *, require_protocol: bool = True) -> dict[str, Any]:
        if self.proc.stdout is None:
            raise IsolatedWorkerError("interactive worker stdout unavailable")
        deadline = time.monotonic() + self.timeout_s
        newline = self._stdout_buffer.find(b"\n")
        while newline < 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise IsolatedWorkerError("timeout")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                self._kill()
                raise IsolatedWorkerError("timeout")
            try:
                chunk = os.read(self.proc.stdout.fileno(), 65_536)
            except OSError as exc:
                raise IsolatedWorkerError("interactive worker stdout read failed") from exc
            if not chunk:
                if self._stdout_buffer:
                    # Preserve a final bootstrap diagnostic even without a newline.
                    newline = len(self._stdout_buffer) - 1
                    break
                stderr = _read_available_stderr(self.proc)
                raise IsolatedWorkerError(stderr.strip() or "interactive worker exited")
            self._stdout_buffer.extend(chunk)
            newline = self._stdout_buffer.find(b"\n")
            limit = getattr(self, "max_line_bytes", 1_000_000)
            if newline < 0 and len(self._stdout_buffer) > limit:
                self._kill()
                raise IsolatedWorkerError("excessive worker output")
        line_bytes = bytes(self._stdout_buffer[: newline + 1])
        del self._stdout_buffer[: newline + 1]
        line_size = len(line_bytes)
        if line_size > getattr(self, "max_line_bytes", 1_000_000):
            self._kill()
            raise IsolatedWorkerError("excessive worker output")
        self.bytes_received += line_size
        try:
            response = json.loads(line_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IsolatedWorkerError("malformed worker output") from exc
        if not isinstance(response, dict):
            raise IsolatedWorkerError("worker response must be an object")
        if not require_protocol:
            return response
        if response.get("schema_id") != "openpine.worker.protocol.v2":
            self._raise_response(response)
        try:
            validate_payload("openpine.worker.protocol.v2", response)
            if not verify_content_hash(
                response, schema_id="openpine.worker.protocol.v2"
            ):
                raise WorkerProtocolError("worker response content hash is invalid")
            self.protocol.accept(response)
        except (ValueError, WorkerProtocolError) as exc:
            raise IsolatedWorkerError("invalid worker protocol response") from exc
        return response

    @staticmethod
    def _raise_response(response: dict[str, Any]) -> None:
        code = str(response.get("error_code") or "WORKER_REJECTED")
        detail = str(response.get("error") or "worker rejected message")
        raise IsolatedWorkerError(f"{code}: {detail}")

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._write_message(payload)
        return self._read_message()

    def evaluate_bar(self, event: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "run_id",
            "bar_index",
            "bar_open_time_utc_ms",
            "recalc_iteration",
            "bar_hash",
            "bar",
            "broker_projection",
            "execution_event",
        }
        if not required.issubset(event):
            raise IsolatedWorkerError("engine BAR_BEGIN artifact is incomplete")
        message = self.protocol.append(
            "BAR_BEGIN",
            {name: event[name] for name in required},
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        response = self._request(message)
        if response.get("kind") != "INTENT_BATCH":
            raise IsolatedWorkerError("worker did not return INTENT_BATCH")
        body = response.get("body")
        if not isinstance(body, dict):
            raise IsolatedWorkerError("worker INTENT_BATCH body is invalid")
        if any(body.get(key) != event[key] for key in ("run_id", "bar_index", "recalc_iteration")):
            raise IsolatedWorkerError("worker intent callback identity mismatch")
        return body

    def _persist_artifact(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        encoded = artifact.get("bytes")
        if not isinstance(encoded, bytes):
            raise IsolatedWorkerError("protocol artifact bytes are required")
        artifact_hash = artifact.get("artifact_hash")
        if not isinstance(artifact_hash, str) or not artifact_hash.startswith("sha256:"):
            raise IsolatedWorkerError("protocol artifact hash is invalid")
        path = self.protocol_artifact_dir / f"{artifact_hash[7:]}.json"
        if path.exists():
            if path.read_bytes() != encoded:
                raise IsolatedWorkerError("protocol artifact hash collision")
        else:
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(encoded)
            temporary.replace(path)
        return {
            "artifact_hash": artifact_hash,
            "schema_id": artifact["schema_id"],
            "codec": artifact["codec"],
            "size_bytes": artifact["size_bytes"],
            "uri": path.resolve().as_uri(),
        }

    def evaluate_recalc(self, event: Mapping[str, Any]) -> dict[str, Any]:
        recalc_iteration = int(event["recalc_iteration"])
        broker_batch = self.protocol.append(
            "BROKER_EVENT_BATCH",
            {
                "run_id": event["run_id"],
                "bar_index": event["bar_index"],
                "recalc_iteration": recalc_iteration - 1,
                "broker_event_batch_hash": event["broker_event_batch_hash"],
                "broker_events": event["broker_events"],
            },
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(broker_batch)
        request = self.protocol.append(
            "RECALC_REQUEST",
            {
                "run_id": event["run_id"],
                "bar_index": event["bar_index"],
                "recalc_iteration": recalc_iteration,
                "cause_sequence": broker_batch["sequence"],
                "execution_event": event["execution_event"],
                "broker_projection_hash": event["broker_projection_hash"],
                "broker_projection": event["broker_projection"],
            },
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(request)
        result = self._read_message()
        if result.get("kind") != "RECALC_RESULT":
            raise IsolatedWorkerError("worker did not return RECALC_RESULT")
        response = self._read_message()
        if response.get("kind") != "INTENT_BATCH":
            raise IsolatedWorkerError("worker did not return recalculated INTENT_BATCH")
        body = response.get("body")
        if not isinstance(body, dict):
            raise IsolatedWorkerError("worker recalculated INTENT_BATCH body is invalid")
        recalc = result.get("body", {})
        if (any(body.get(key) != event[key] or recalc.get(key) != event[key]
                for key in ("run_id", "bar_index", "recalc_iteration"))
                or recalc.get("intent_batch_message_id") != response.get("message_id")
                or recalc.get("intent_batch_hash") != body.get("intent_batch_hash")):
            raise IsolatedWorkerError("recalculation response identity mismatch")
        return body

    def commit_bar(self, event: Mapping[str, Any]) -> dict[str, Any]:
        state_ref = self._persist_artifact(event["state_artifact"])
        projection_ref = self._persist_artifact(
            event["broker_projection_artifact"]
        )
        body = {
            "run_id": event["run_id"],
            "bar_index": event["bar_index"],
            "recalc_iteration": event["recalc_iteration"],
            "state_hash": event["state_hash"],
            "broker_projection_hash": event["broker_projection_hash"],
            "state_ref": state_ref,
            "broker_projection_ref": projection_ref,
        }
        message = self.protocol.append(
            "BAR_COMMIT",
            body,
            created_at_utc_ms=int(event["bar_open_time_utc_ms"]),
        )
        self._write_message(message)
        self._last_commit = message
        return message

    def heartbeat(self) -> None:
        if self._closed or self.proc.poll() is not None:
            raise IsolatedWorkerError("interactive worker heartbeat failed")

    def finalize(self) -> dict[str, Any]:
        if self._closed:
            return {"kind": "FINALIZE"}
        if self._last_commit is None:
            raise IsolatedWorkerError("cannot finalize without a committed bar")
        commit_body = self._last_commit["body"]
        message: dict[str, Any] | None = None
        try:
            message = self.protocol.append(
                "FINALIZE",
                {
                    "run_id": commit_body["run_id"],
                    "final_sequence": self._last_commit["sequence"],
                    "final_state_hash": commit_body["state_hash"],
                    "broker_projection_hash": commit_body["broker_projection_hash"],
                    "last_commit_message_id": self._last_commit["message_id"],
                    "last_committed_sequence": self._last_commit["sequence"],
                },
                created_at_utc_ms=int(self._last_commit["created_at_utc_ms"]),
            )
            self._write_message(message)
        finally:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            try:
                return_code = self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                self._kill()
                raise IsolatedWorkerError("interactive worker did not exit after FINALIZE") from error
            except OSError as error:
                self._kill()
                raise IsolatedWorkerError("interactive worker exit could not be verified") from error
            else:
                if return_code != 0:
                    raise IsolatedWorkerError(f"interactive worker exited with status {return_code}")
            finally:
                self._close_pipes()
                self._closed = True
        assert message is not None
        return message

    def __enter__(self) -> "InteractiveWorkerSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.finalize()
        else:
            try:
                abort = self.protocol.append(
                    "ABORT",
                    {
                        "run_id": self.protocol.execution_context["run_id"],
                        "error_code": "PARENT_ABORT",
                        "reason": str(exc or "isolated run aborted"),
                    },
                    created_at_utc_ms=0,
                )
                self._write_message(abort)
            except (IsolatedWorkerError, WorkerProtocolError, ValueError):
                pass
            finally:
                self._kill()


def evaluate_artifact(
    source: bytes,
    *,
    admitted_manifest: AdmittedManifest,
    instrument_id: str = "",
    timeout_s: float = 5.0,
    stack_id: str = "openpine-5.0",
    semantic_profile: str = "",
    cgroup_dir: str | Path | None = None,
    bars: list[dict[str, Any]] | None = None,
    htf_bars: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if len(source) > 500_000:
        raise IsolatedWorkerError("artifact source exceeds size limit")
    if stack_id != "openpine-5.0":
        raise IsolatedWorkerError("stack_id mismatch")
    if semantic_profile not in {"legacy_4x", "strict_5x"}:
        raise IsolatedWorkerError("semantic_profile required")
    if cgroup_dir is not None:
        try:
            prepare_worker_cgroup(cgroup_dir)
        except CgroupError as exc:
            raise IsolatedWorkerError(str(exc)) from exc
    payload = json.dumps(
        {
            "source": source.decode("utf-8"),
            "stack_id": stack_id,
            "instrument_id": instrument_id,
            "semantic_profile": semantic_profile,
            "bars": bars or [],
            "htf_bars": htf_bars or [],
            "params": {} if params is None else params,
        }
    )
    unit_name = _worker_unit_name()
    try:
        # Immutable argv: admitted bwrap + wheel-bound candidate Python. No shell or user path.
        proc = subprocess.Popen(  # noqa: S603
            _bwrap_argv(admitted_manifest, unit_name),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise IsolatedWorkerError("worker spawn failed") from exc
    if cgroup_dir is not None:
        try:
            attach_worker_tree(cgroup_dir, proc.pid)
        except CgroupError as exc:
            try:
                _cleanup_worker_process(proc, unit_name)
            except IsolatedWorkerError as cleanup_exc:
                raise cleanup_exc from exc
            raise IsolatedWorkerError(str(exc)) from exc
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        try:
            _cleanup_worker_process(proc, unit_name)
        except IsolatedWorkerError as cleanup_exc:
            raise cleanup_exc from exc
        raise IsolatedWorkerError("timeout") from exc
    completed_stdout = stdout or ""
    completed_stderr = stderr or ""
    if len(completed_stdout) > 1_000_000 or len(completed_stderr) > 1_000_000:
        raise IsolatedWorkerError("excessive worker output")
    if proc.returncode != 0:
        detail = completed_stdout.strip() or completed_stderr.strip() or "worker failed"
        raise IsolatedWorkerError(detail)
    try:
        result = json.loads(completed_stdout)
    except json.JSONDecodeError as exc:
        raise IsolatedWorkerError("malformed worker output") from exc
    if not result.get("ok"):
        raise IsolatedWorkerError(str(result.get("error") or "worker rejected artifact"))
    return result
