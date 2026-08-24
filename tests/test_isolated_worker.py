from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import (
    InteractiveWorkerSession,
    IsolatedWorkerError,
    evaluate_artifact,
)
from tests.admission_helpers import make_sealed_artifact
from tests.rc4_fixtures import HASH_A, admitted_manifest, execution_context


def _eval(source: bytes, **kwargs):
    kwargs.setdefault("semantic_profile", "legacy_4x")
    kwargs.setdefault("admitted_manifest", admitted_manifest())
    kwargs.setdefault("instrument_id", "test:S")
    return evaluate_artifact(source, **kwargs)


def _session(source, _context, instrument_id, manifest, **kwargs):
    artifact = make_sealed_artifact(python_code=source.decode("utf-8"))[
        "generated_artifact"
    ]
    context = execution_context(
        generated_artifact_hash=artifact["content_hash"],
        emitted_module_hash=artifact["emitted_module_hash"],
    )
    return InteractiveWorkerSession(
        source,
        context,
        instrument_id,
        manifest,
        artifact,
        HASH_A,
        Path("/tmp/openpine-test-protocol-artifacts"),
        **kwargs,
    )


def test_parent_does_not_import_generated_module() -> None:
    source = "VALUE = 42\n"
    result = _eval(source.encode("utf-8"), timeout_s=5)
    assert result["ok"] is True
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)


def test_worker_does_not_inherit_host_secrets() -> None:
    os.environ["OPENPINE_SECRET"] = "super-secret-token"
    os.environ["VIRTUAL_ENV"] = "/tmp/should-not-leak"
    source = textwrap.dedent("""
        import os
        LEAKED = os.environ.get("OPENPINE_SECRET")
        VENV = os.environ.get("VIRTUAL_ENV")
        """)
    try:
        result = _eval(source.encode("utf-8"), timeout_s=5)
    finally:
        os.environ.pop("OPENPINE_SECRET", None)
        os.environ.pop("VIRTUAL_ENV", None)
    assert result["ok"] is True
    assert result["namespace"].get("LEAKED") in (None, "")
    assert result["namespace"].get("VENV") in (None, "")


def test_worker_executes_captured_bytes_not_later_path(tmp_path: Path) -> None:
    path = tmp_path / "generated_strategy.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    payload = path.read_bytes()
    path.write_text("VALUE = 999\n", encoding="utf-8")
    result = _eval(payload, timeout_s=5)
    assert result["ok"] is True
    assert result["namespace"]["VALUE"] == 1


def test_worker_rejects_socket_import() -> None:
    source = "import socket\n"
    with pytest.raises(IsolatedWorkerError, match="socket"):
        _eval(source.encode("utf-8"), timeout_s=5)


def test_worker_times_out_infinite_loop() -> None:
    source = "while True:\n    pass\n"
    with pytest.raises(IsolatedWorkerError, match="timeout"):
        _eval(source.encode("utf-8"), timeout_s=0.4)


def test_interactive_worker_timeout_covers_partial_line_output() -> None:
    source = b"""
import os
os.write(1, b"{")
os.read(0, 1)
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        pass
    def _process_bar(self, bar, bar_index):
        pass
"""
    with pytest.raises(IsolatedWorkerError, match="timeout"):
        _session(
            source,
            execution_context(),
            "test:S",
            admitted_manifest(),
            semantic_profile="strict_5x",
            chart_timeframe="1m",
            timeout_s=0.2,
        )


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


def test_worker_rejects_malformed_and_nonzero_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import openpine.runtime.isolated_worker as worker

    real_popen = worker.subprocess.Popen

    def _popen_malformed(cmd, *args, **kwargs):
        if isinstance(cmd, list) and worker.BWRAP in cmd:
            return SimpleNamespace(
                pid=0,
                returncode=0,
                communicate=lambda input=None, timeout=None: ("not-json", ""),
                kill=lambda: None,
            )
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(worker.subprocess, "Popen", _popen_malformed)
    with pytest.raises(IsolatedWorkerError, match="malformed"):
        _eval(b"VALUE = 1\n", timeout_s=1)

    def _popen_boom(cmd, *args, **kwargs):
        if isinstance(cmd, list) and worker.BWRAP in cmd:
            return SimpleNamespace(
                pid=0,
                returncode=1,
                communicate=lambda input=None, timeout=None: ("", "boom"),
                kill=lambda: None,
            )
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(worker.subprocess, "Popen", _popen_boom)
    with pytest.raises(IsolatedWorkerError, match="boom"):
        _eval(b"VALUE = 1\n", timeout_s=1)


def test_sandbox_blocks_network_and_host_filesystem(tmp_path: Path) -> None:
    sentinel = tmp_path / "host-secret.sqlite"
    sentinel.write_text("host-only", encoding="utf-8")
    source = textwrap.dedent(f"""
        import os
        HOME_VISIBLE = os.path.exists("/home/moltbot1")
        SSH_VISIBLE = os.path.exists("/home/moltbot1/.ssh")
        ETC_VISIBLE = os.path.exists("/etc/passwd")
        VAR_VISIBLE = os.path.exists("/var/lib")
        HOST_SECRET_VISIBLE = os.path.exists({str(sentinel)!r})
        VISIBLE_PIDS = sorted(int(item) for item in os.listdir("/proc") if item.isdigit())
        stat = os.statvfs("/tmp")
        SCRATCH_BYTES = stat.f_frsize * stat.f_blocks
        def writable(path):
            try:
                open(path, "w").write("x")
                return True
            except OSError:
                return False
        ROOT_WRITABLE = writable("/.openpine-write-probe")
        TRUSTED_WRITABLE = writable("/tmp/openpine-trusted/.openpine-write-probe")
        SCRATCH_WRITABLE = writable("/tmp/.openpine-write-probe")
        try:
            open("/usr/bin/.openpine-write-probe", "w").write("x")
            USR_WRITABLE = True
        except Exception:
            USR_WRITABLE = False
        """)
    result = _eval(source.encode("utf-8"), timeout_s=5)
    assert result["ok"] is True
    assert result["namespace"]["HOME_VISIBLE"] is False
    assert result["namespace"]["SSH_VISIBLE"] is False
    assert result["namespace"]["ETC_VISIBLE"] is False
    assert result["namespace"]["VAR_VISIBLE"] is False
    assert result["namespace"]["HOST_SECRET_VISIBLE"] is False
    assert result["namespace"]["ROOT_WRITABLE"] is False
    assert result["namespace"]["TRUSTED_WRITABLE"] is False
    assert result["namespace"]["SCRATCH_WRITABLE"] is True
    assert result["namespace"]["SCRATCH_BYTES"] <= 16 * 1024 * 1024
    assert 1 in result["namespace"]["VISIBLE_PIDS"]
    assert len(result["namespace"]["VISIBLE_PIDS"]) <= 2
    assert result["namespace"]["USR_WRITABLE"] is False
    assert result["isolation"]["network"] == "blocked"
    assert result["isolation"]["usr_writable"] is False
    assert "OPENPINE_SECRET" not in result["isolation"]["env"]
    assert "VIRTUAL_ENV" not in result["isolation"]["env"]


def test_dynamic_socket_import_is_denied() -> None:
    with pytest.raises(IsolatedWorkerError, match="socket"):
        _eval(b'__import__("socket")\n', timeout_s=5)


def test_in_process_generated_import_is_forbidden(tmp_path: Path) -> None:
    from openpine.runtime.engine import BacktestArtifactError, _load_generated_module

    path = tmp_path / "generated_strategy.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(BacktestArtifactError, match="in-process"):
        _load_generated_module(path, "src", "art")


def test_sandbox_requires_dedicated_openpine_worker() -> None:
    from openpine.runtime.isolated_worker import worker_user_available, worker_user_uid

    assert worker_user_available() is True
    result = _eval(b"VALUE = 1\n", timeout_s=5)
    assert result["isolation"]["uid"] == worker_user_uid()


def test_worker_has_no_current_user_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.runtime.isolated_worker as worker

    monkeypatch.setattr(worker, "worker_user_uid", lambda: None)
    with pytest.raises(IsolatedWorkerError, match="dedicated openpine-worker"):
        worker._bwrap_argv(admitted_manifest())


def test_worker_rejects_huge_source_and_subprocess() -> None:
    with pytest.raises(IsolatedWorkerError, match="size limit"):
        _eval(b"x = 1\n" * 100_000, timeout_s=5)
    with pytest.raises(IsolatedWorkerError, match="subprocess"):
        _eval(b'__import__("subprocess")\n', timeout_s=5)


def test_worker_does_not_retry_internal_type_error() -> None:
    source = b"""
CALLS = 0
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        pass
    def _process_bar(self, *args):
        global CALLS
        CALLS += 1
        if CALLS == 1:
            raise TypeError("intentional-first-call")
        raise RuntimeError("strategy-was-retried")
"""
    with pytest.raises(IsolatedWorkerError, match="intentional-first-call") as error:
        _eval(source, bars=_bar_dicts(), timeout_s=5)
    assert "strategy-was-retried" not in str(error.value)


def test_worker_reports_constructor_and_bar_commit_failures_without_fallback() -> None:
    constructor = b"""
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        raise TypeError("constructor-internal")
    def _process_bar(self, bar, bar_index):
        pass
"""
    with pytest.raises(IsolatedWorkerError, match="STRATEGY_CONSTRUCTOR_ERROR"):
        _eval(constructor, bars=_bar_dicts(), timeout_s=5)

    commit = b"""
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.rt = runtime
        def fail_commit(_runtime):
            raise RuntimeError("commit-failed")
        type(self.rt).end_bar = fail_commit
    def _process_bar(self, bar, bar_index):
        pass
"""
    with pytest.raises(IsolatedWorkerError, match="BAR_COMMIT_ERROR"):
        _eval(commit, bars=_bar_dicts(), timeout_s=5)


def test_interactive_worker_receives_broker_projection_before_each_decision() -> None:
    from tests.test_isolated_run import _bars, _cfg, _run_artifact

    source = b"""
from pinelib.strategy.context import StrategyContext
class GeneratedStrategy:
    def __init__(self, params=None, runtime=None):
        self.ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        self.ctx.attach_runtime(runtime)
    def _process_bar(self, bar, bar_index):
        if self.ctx.position_size == 0:
            self.ctx.entry("L", "long", qty=1)
        else:
            self.ctx.close("L")
"""
    result = _run_artifact(source, bars=_bars(), config=_cfg())
    assert [event["kind"] for event in result["intent_tape"]][:2] == ["entry", "close"]


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


def test_worker_argv_has_no_new_session() -> None:
    from openpine.runtime.isolated_worker import (
        IsolatedWorkerError,
        TRUSTED_DEST,
        _BOOTSTRAP,
        _bwrap_argv,
    )

    argv: list[str] = []
    try:
        argv = _bwrap_argv(admitted_manifest())
    except IsolatedWorkerError:
        pytest.skip("bubblewrap missing")
    assert "--new-session" not in argv
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv
    assert "--clearenv" in argv
    assert "/usr/bin/systemd-run" in argv
    assert str(Path(sys.executable).resolve()) in argv
    assert "--uid=openpine-worker" in argv
    assert "--property=MemoryMax=134217728" in argv
    assert "--property=MemorySwapMax=0" in argv
    assert "--property=TasksMax=32" in argv
    assert "--property=KillMode=control-group" in argv
    assert "--property=SystemCallFilter=@system-service @mount" in argv
    remount_index = argv.index("--remount-ro")
    assert argv[remount_index : remount_index + 2] == ["--remount-ro", "/"]
    assert remount_index > argv.index("--dev")
    trusted_dest = TRUSTED_DEST
    assert trusted_dest in argv
    tmpfs_index = argv.index("--tmpfs")
    trusted_dest_index = argv.index(trusted_dest)
    assert argv[tmpfs_index : tmpfs_index + 2] == ["--tmpfs", "/tmp"]
    assert argv[trusted_dest_index - 2] == "--ro-bind"
    assert Path(argv[trusted_dest_index - 1]).name.startswith("openpine-trusted-")
    assert tmpfs_index < trusted_dest_index
    assert f"sys.path.insert(0, {trusted_dest!r})" in _BOOTSTRAP
    assert not any(item.endswith("dist-packages") for item in argv)


def test_worker_ledger_projection_exposes_complete_indexed_trade_api() -> None:
    from openpine.runtime.isolated_worker import _BOOTSTRAP
    from pinelib.core.na import na

    namespace = {"__name__": "__ledger_projection_test__"}
    exec(_BOOTSTRAP, namespace)
    projection = namespace["_LedgerProjection"](
        {
            "equity": 101.0,
            "closed_trade_log": [
                {
                    "entry_price": 10.0,
                    "exit_price": 12.0,
                    "entry_time": 1,
                    "exit_time": 2,
                    "profit": 2.0,
                    "profit_percent": 20.0,
                    "commission": 0.1,
                    "qty": 3.0,
                    "side": "long",
                    "size": 3.0,
                    "entry_id": "L",
                    "exit_id": "X",
                    "entry_comment": "enter",
                    "exit_comment": "leave",
                    "max_runup": 4.0,
                    "max_drawdown": 1.0,
                    "entry_bar_index": 5,
                    "exit_bar_index": 6,
                }
            ],
            "open_trade_log": [
                {
                    "entry_price": 20.0,
                    "exit_price": None,
                    "entry_time": 3,
                    "exit_time": None,
                    "profit": -1.0,
                    "profit_percent": -5.0,
                    "commission": 0.2,
                    "qty": 2.0,
                    "side": "short",
                    "size": -2.0,
                    "entry_id": "S",
                    "exit_id": None,
                    "max_runup": 2.0,
                    "max_drawdown": 3.0,
                    "entry_bar_index": 7,
                }
            ],
        }
    )

    closed = {
        "entry_price": 10.0,
        "exit_price": 12.0,
        "entry_time": 1,
        "exit_time": 2,
        "profit": 2.0,
        "profit_percent": 20.0,
        "commission": 0.1,
        "qty": 3.0,
        "side": "long",
        "size": 3.0,
        "entry_id": "L",
        "exit_id": "X",
        "entry_comment": "enter",
        "exit_comment": "leave",
        "max_runup": 4.0,
        "max_drawdown": 1.0,
        "entry_bar_index": 5,
        "exit_bar_index": 6,
    }
    opened = {
        "entry_price": 20.0,
        "entry_time": 3,
        "profit": -1.0,
        "profit_percent": -5.0,
        "commission": 0.2,
        "qty": 2.0,
        "side": "short",
        "size": -2.0,
        "entry_id": "S",
        "max_runup": 2.0,
        "max_drawdown": 3.0,
        "entry_bar_index": 7,
    }
    for field, expected in closed.items():
        assert getattr(projection, f"closedtrades_{field}")(0) == expected
    assert projection.closedtrades_net_profit(0) == closed["profit"]
    for field, expected in opened.items():
        assert getattr(projection, f"opentrades_{field}")(0) == expected
    assert projection.opentrades_exit_price(0) is na
    assert projection.opentrades_exit_time(0) is na
    assert projection.opentrades_exit_id(0) is na
    assert projection.closedtrades_entry_price(2) is na
    assert projection.equity == 101.0


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


def test_worker_handshake_rejects_unknown_stack_and_profile() -> None:
    with pytest.raises(IsolatedWorkerError, match="stack_id"):
        evaluate_artifact(
            b"VALUE = 1\n", admitted_manifest=admitted_manifest(), stack_id="wrong"
        )
    with pytest.raises(IsolatedWorkerError, match="semantic_profile"):
        evaluate_artifact(
            b"VALUE = 1\n",
            admitted_manifest=admitted_manifest(),
            semantic_profile="nope",
        )


def test_evaluate_artifact_requires_semantic_profile() -> None:
    with pytest.raises(IsolatedWorkerError, match="semantic_profile"):
        evaluate_artifact(b"VALUE = 1\n", admitted_manifest=admitted_manifest())


def test_evaluate_artifact_rejects_non_object_params() -> None:
    with pytest.raises(IsolatedWorkerError, match="params must be an object"):
        evaluate_artifact(
            b"VALUE = 1\n",
            admitted_manifest=admitted_manifest(),
            semantic_profile="strict_5x",
            params=[],  # type: ignore[arg-type]
        )


def test_worker_kills_memory_bomb() -> None:
    source = "x = []\nwhile True:\n    x.append('x' * 1048576)\n"
    with pytest.raises(IsolatedWorkerError):
        _eval(source.encode("utf-8"), timeout_s=2)


def test_worker_kills_fork_bomb() -> None:
    source = "import os\nwhile True:\n    os.fork()\n"
    with pytest.raises(IsolatedWorkerError):
        _eval(source.encode("utf-8"), timeout_s=2)


def test_isolated_worker_emits_live_intent_tape() -> None:
    source = textwrap.dedent(
        """
        from pinelib.strategy.context import StrategyContext
        ctx = StrategyContext(intent_run_id="run", intent_strategy_id="s")
        ctx.entry("L", "long", qty=1)
        """
    )
    result = _eval(source.encode("utf-8"), timeout_s=8)
    tape = result["intent_tape"]
    assert tape
    event = tape[0]
    assert event["schema_id"] == "openpine.intent.v2"
    assert event["kind"] == "entry"
    assert event["qty"] == "1"
    assert "origin_command_kind" not in event
    assert event["content_hash"]
    from openpine.runtime.isolated_worker import _TRUSTED_STAGE, _bwrap_argv

    argv = _bwrap_argv(admitted_manifest())
    assert all("/home/" not in item for item in argv)
    assert "/tmp/openpine-trusted" in argv
    assert _TRUSTED_STAGE is not None
    assert (_TRUSTED_STAGE / "pinelib").is_dir()
    assert (_TRUSTED_STAGE / "openpine_contracts").is_dir()


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


def _bar_dicts() -> list[dict]:
    return [
        {
            "time": 1_000 + i,
            "open": 10.0 + i,
            "high": 11.0 + i,
            "low": 9.0 + i,
            "close": 10.5 + i,
        }
        for i in range(6)
    ]


def test_isolated_worker_drives_generated_process_bar() -> None:
    result = _eval(
        CLASS_SOURCE.encode("utf-8"),
        bars=_bar_dicts(),
        timeout_s=8,
    )
    tape = result["intent_tape"]
    assert tape
    assert tape[0]["schema_id"] == "openpine.intent.v2"
    assert tape[0]["kind"] == "entry"
    assert tape[0]["bar_index"] == 2
    assert tape[0]["qty"] == "1"


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


def test_isolated_worker_runs_real_generated_contract() -> None:
    result = _eval(
        REAL_SOURCE.encode("utf-8"),
        bars=_bar_dicts(),
        timeout_s=8,
    )
    tape = result["intent_tape"]
    assert tape
    assert tape[0]["schema_id"] == "openpine.intent.v2"
    assert tape[0]["kind"] == "entry"
    assert tape[0]["qty"] == "1"
    from openpine.runtime.isolated_worker import _TRUSTED_STAGE

    assert _TRUSTED_STAGE is not None
    assert (_TRUSTED_STAGE / "ast2python").is_dir()


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


def test_isolated_worker_emits_plot_records() -> None:
    bars = [
        {"time": 1000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        {"time": 1001, "open": 100, "high": 101, "low": 99, "close": 101, "volume": 1},
    ]
    result = _eval(
        PLOT_SOURCE.encode("utf-8"),
        bars=bars,
        timeout_s=8,
    )
    plots = result["plots"]
    assert plots
    assert plots[0]["title"] == "close"
    assert plots[0]["bar_time"] == 1000
    assert plots[0]["bar_index"] == 0
    assert isinstance(plots[0]["value"], str)
    assert plots[0]["value"] == "100"


def test_isolated_worker_echoes_semantic_profile() -> None:
    result = _eval(
        b"VALUE = 1\n",
        semantic_profile="strict_5x",
        timeout_s=5,
    )
    assert result["semantic_profile"] == "strict_5x"


@pytest.mark.parametrize("missing", ["time", "open", "high", "low", "close"])
def test_evaluate_artifact_rejects_missing_required_chart_field(missing: str) -> None:
    source = textwrap.dedent(
        """
        class GeneratedStrategy:
            def __init__(self, params=None, runtime=None):
                self.rt = runtime

            def _process_bar(self, bar, bar_index=None):
                return None
        """
    )
    bar = {
        "time": 0,
        "time_close": 59_999,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 1,
    }
    del bar[missing]

    with pytest.raises(IsolatedWorkerError, match=f"chart bar required field {missing}"):
        _eval(source.encode("utf-8"), bars=[bar], timeout_s=8)


@pytest.mark.parametrize(
    "missing",
    ["symbol", "timeframe", "time", "time_close", "open", "high", "low", "close"],
)
def test_evaluate_artifact_rejects_missing_required_htf_field(missing: str) -> None:
    source = textwrap.dedent(
        """
        class GeneratedStrategy:
            def __init__(self, params=None, runtime=None):
                self.rt = runtime

            def _process_bar(self, bar, bar_index=None):
                return None
        """
    )
    htf_bar = {
        "symbol": "BTCUSDT",
        "timeframe": "1D",
        "time": 0,
        "time_close": 86_399_999,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 1,
    }
    del htf_bar[missing]

    with pytest.raises(IsolatedWorkerError, match=f"HTF bar required field {missing}"):
        _eval(
            source.encode("utf-8"),
            bars=[
                {
                    "time": 0,
                    "time_close": 59_999,
                    "open": 1,
                    "high": 1,
                    "low": 1,
                    "close": 1,
                    "volume": 1,
                }
            ],
            htf_bars=[htf_bar],
            timeout_s=8,
        )


def test_isolated_request_security_without_htf_is_fail_closed() -> None:
    source = textwrap.dedent(
        """
        from pinelib.request.security import security

        class GeneratedStrategy:
            def __init__(self, params=None, runtime=None):
                self.rt = runtime

            def _process_bar(self, bar, bar_index=None):
                security(
                    "BTCUSDT",
                    "1D",
                    lambda ctx: 1,
                    runtime=self.rt,
                    state_id="htf",
                    ignore_invalid_symbol=True,
                )
        """
    )
    bars = [
        {"time": 1000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    with pytest.raises(IsolatedWorkerError, match="request.security"):
        _eval(source.encode("utf-8"), bars=bars, timeout_s=8)


def test_isolated_request_security_uses_stamped_htf_bars() -> None:
    source = textwrap.dedent(
        """
        from pinelib.request.security import security

        class GeneratedStrategy:
            def __init__(self, params=None, runtime=None):
                self.rt = runtime

            def _process_bar(self, bar, i=0):
                value = security(
                    "BTCUSDT",
                    "1D",
                    [42],
                    runtime=self.rt,
                    state_id="htf",
                )
                rec = getattr(self.rt, "plot_recorder", None)
                if rec is None:
                    return
                rec.record_plot(int(bar.time), int(i), value, "htf")
        """
    )
    chart_bars = [
        {
            "time": 86_400_000,
            "time_close": 86_459_999,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        },
    ]
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        },
    ]
    result = _eval(
        source.encode("utf-8"),
        bars=chart_bars,
        htf_bars=htf_bars,
        timeout_s=8,
    )
    plots = result["plots"]
    assert plots
    assert plots[0]["title"] == "htf"
    assert plots[0]["value"] == 42
