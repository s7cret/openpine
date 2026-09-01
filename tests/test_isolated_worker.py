from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import InteractiveWorkerSession, IsolatedWorkerError
from tests.rc4_fixtures import admitted_manifest


















def test_partial_line_cleanup_is_bounded_when_descendant_retains_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    script = (
        "import os,time\n"
        "pid=os.fork()\n"
        "if pid == 0:\n"
        "    time.sleep(60)\n"
        "os.write(1,b'{')\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and in-test script
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    session = object.__new__(InteractiveWorkerSession)
    session.proc = proc
    session.unit_name = "openpine-worker-bounded-cleanup-test"
    session.timeout_s = 0.1
    session._closed = False
    session._stdout_buffer = bytearray()
    session.bytes_received = 0

    def unresolved_cleanup(_unit_name: str) -> None:
        raise IsolatedWorkerError("worker unit cleanup could not be verified")

    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", unresolved_cleanup)
    started = time.monotonic()
    try:
        with pytest.raises(IsolatedWorkerError, match="could not be verified"):
            session._read_message()
        assert time.monotonic() - started < 3.0
        assert proc.stdout is not None and proc.stdout.closed
        assert proc.stderr is not None and proc.stderr.closed
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_worker_unit_cleanup_escalates_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    results = iter(((0, ""), (0, "active\n"), (0, ""), (0, "inactive\n")))
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        returncode, stdout = next(results)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    monkeypatch.setattr(isolated_worker.subprocess, "run", fake_run)
    isolated_worker._stop_worker_unit("openpine-worker-test")
    assert any("kill" in argv for argv in calls)
    assert calls[-1][-1] == "openpine-worker-test"


def test_worker_unit_cleanup_fails_if_unit_remains_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    results = iter(((0, ""), (0, "active\n"), (0, ""), (0, "active\n")))

    def fake_run(argv, **_kwargs):
        returncode, stdout = next(results)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    monkeypatch.setattr(isolated_worker.subprocess, "run", fake_run)
    try:
        with pytest.raises(IsolatedWorkerError, match="remained active"):
            isolated_worker._stop_worker_unit("openpine-worker-test")
        assert "openpine-worker-test" in isolated_worker._PENDING_WORKER_UNITS
    finally:
        isolated_worker._discard_pending_worker_unit("openpine-worker-test")


def test_worker_unit_cleanup_fails_closed_on_state_query_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    results = iter(((0, ""), (1, ""), (0, ""), (1, "")))

    def fake_run(argv, **_kwargs):
        returncode, stdout = next(results)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    monkeypatch.setattr(isolated_worker.subprocess, "run", fake_run)
    try:
        with pytest.raises(IsolatedWorkerError, match="could not be verified"):
            isolated_worker._stop_worker_unit("openpine-worker-test")
        assert "openpine-worker-test" in isolated_worker._PENDING_WORKER_UNITS
    finally:
        isolated_worker._discard_pending_worker_unit("openpine-worker-test")


def test_worker_unit_stop_timeout_still_escalates_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    calls: list[list[str]] = []
    results = iter(((0, "active\n"), (0, ""), (0, "inactive\n")))

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if "stop" in argv:
            raise subprocess.TimeoutExpired(argv, 2)
        returncode, stdout = next(results)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    monkeypatch.setattr(isolated_worker.subprocess, "run", fake_run)
    isolated_worker._stop_worker_unit("openpine-worker-test")
    assert any("kill" in argv for argv in calls)
    assert "openpine-worker-test" not in isolated_worker._PENDING_WORKER_UNITS


def test_worker_unit_kill_failure_retains_name_and_retry_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    results = iter(((0, ""), (0, "active\n"), (0, "active\n")))

    def fail_kill(argv, **_kwargs):
        if "kill" in argv:
            raise subprocess.TimeoutExpired(argv, 2)
        returncode, stdout = next(results)
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

    monkeypatch.setattr(isolated_worker.subprocess, "run", fail_kill)
    try:
        with pytest.raises(IsolatedWorkerError, match="remained active"):
            isolated_worker._stop_worker_unit("openpine-worker-test")
        assert "openpine-worker-test" in isolated_worker._PENDING_WORKER_UNITS

        retry_results = iter(((0, ""), (0, "inactive\n")))

        def successful_retry(argv, **_kwargs):
            returncode, stdout = next(retry_results)
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout)

        monkeypatch.setattr(isolated_worker.subprocess, "run", successful_retry)
        isolated_worker._retry_pending_worker_unit_cleanup()
        assert "openpine-worker-test" not in isolated_worker._PENDING_WORKER_UNITS
    finally:
        isolated_worker._discard_pending_worker_unit("openpine-worker-test")












def test_worker_has_no_current_user_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_worker as worker

    monkeypatch.setattr(worker, "worker_user_uid", lambda: None)
    with pytest.raises(IsolatedWorkerError, match="dedicated openpine-worker"):
        worker._bwrap_argv(admitted_manifest())










def test_interactive_worker_streams_more_than_ten_megabytes_across_bounded_messages() -> None:
    class Sink:
        closed = False

        def write(self, value: str) -> int:
            return len(value)

        def flush(self) -> None:
            return None

    session = object.__new__(InteractiveWorkerSession)
    session._closed = False
    session.proc = type("Proc", (), {"stdin": Sink()})()
    session.bytes_sent = 0
    payload = {"padding": "x" * 900_000}
    for _ in range(12):
        session._write_json_line(payload)
    assert session.bytes_sent > 10 * 1024 * 1024






def test_worker_argv_mounts_optional_lib64_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from openpine.runtime.isolated_worker import _runtime_ro_bind_args

    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: str(self) in {"/usr", "/lib", "/lib64"},
    )

    argv = _runtime_ro_bind_args()

    expected = [
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
    ]
    python_prefix = Path(sys.base_prefix).resolve()
    if not python_prefix.is_relative_to(Path("/usr").resolve()):
        expected.extend(["--ro-bind", str(python_prefix), str(python_prefix)])
    assert argv == expected


def test_trusted_stage_cleanup_removes_partial_copy(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import openpine.runtime.isolated_worker as worker

    stage = tmp_path / "openpine-trusted-partial"
    worker._cleanup_trusted_stage()

    def make_stage(**_kwargs):
        stage.mkdir()
        return str(stage)

    monkeypatch.setattr(worker.tempfile, "mkdtemp", make_stage)
    monkeypatch.setattr(
        worker.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(
            origin=str(tmp_path / "package" / "__init__.py"),
            submodule_search_locations=[str(tmp_path / "package")],
        ),
    )

    def fail_copy(_src, target, **_kwargs):
        Path(target).mkdir(parents=True)
        raise OSError("copy failed")

    monkeypatch.setattr(worker.shutil, "copytree", fail_copy)
    with pytest.raises(OSError, match="copy failed"):
        worker._stage_trusted_packages()
    assert not stage.exists()
    assert worker._TRUSTED_STAGE is None














CLASS_SOURCE = textwrap.dedent(
    """
    from pinelib.strategy.context import StrategyContext

    class GeneratedStrategy:
        def __init__(self, params=None, runtime=None):
            self.params = params or {}
            self.rt = runtime
            self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")

        def _process_bar(self, bar, bar_index=None):
            idx = self.rt.bar_index if bar_index is None else bar_index
            if idx != 2:
                return
            self.ctx._runtime = type(
                "RT",
                (),
                {
                    "bar_index": 2,
                    "current_bar": type("B", (), {"time": getattr(bar, "time", 1002)})(),
                },
            )()
            self.ctx.entry("L", "long", qty=1)
    """
)






REAL_SOURCE = textwrap.dedent(
    """
    from __future__ import annotations
    from ast2python.errors import RuntimeContractError
    from pinelib.strategy.context import StrategyContext

    REQUIRED_RUNTIME_CONTRACT = "1.4"

    class GeneratedStrategy:
        def __init__(self, params=None, runtime=None):
            self.params = params or {}
            self.rt = runtime
            if self.rt is None:
                raise RuntimeContractError("runtime is required for generated modules")
            if getattr(self.rt, "contract_version", None) != REQUIRED_RUNTIME_CONTRACT:
                raise RuntimeContractError("contract mismatch")
            self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
            self.ctx.attach_runtime(self.rt)

        def _process_bar(self, bar):
            if getattr(bar, "time", None) != 1002:
                return
            self.ctx.entry("L", "long", qty=1)
    """
)




PLOT_SOURCE = textwrap.dedent(
    """
    class GeneratedStrategy:
        def __init__(self, params=None, runtime=None):
            self.rt = runtime
        def _process_bar(self, bar, i=0):
            rec = getattr(self.rt, "plot_recorder", None)
            if rec is None:
                return
            rec.record_plot(int(bar.time), int(i), bar.close, "close")
    """
).strip()


def test_close_process_pipes_swallows_broken_pipe_on_stdin_close() -> None:
    from openpine.runtime.isolated_worker import _close_process_pipes

    class Boom:
        closed = False

        def close(self) -> None:
            self.closed = True
            raise BrokenPipeError(32, "Broken pipe")

    stdin = Boom()
    proc = type("Proc", (), {"stdin": stdin, "stdout": None, "stderr": None})()
    _close_process_pipes(proc)
    assert stdin.closed is True


def test_write_pipe_closed_is_not_masked_by_cleanup_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openpine.runtime import isolated_worker

    class BoomStdin:
        closed = False

        def write(self, value: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True
            raise BrokenPipeError(32, "Broken pipe")

    class BoomProc:
        stdin = BoomStdin()
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 1

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 1

    session = object.__new__(InteractiveWorkerSession)
    session._closed = False
    session.proc = BoomProc()
    session.unit_name = "openpine-worker-pipe-mask-test"
    session.bytes_sent = 0
    monkeypatch.setattr(isolated_worker, "_stop_worker_unit", lambda *_a, **_k: None)
    with pytest.raises(IsolatedWorkerError, match="interactive worker pipe closed"):
        try:
            session._write_json_line({"k": "v"})
        except Exception:
            session._kill()
            raise
