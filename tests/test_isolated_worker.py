from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import IsolatedWorkerError, evaluate_artifact


def _eval(source: bytes, **kwargs):
    kwargs.setdefault("semantic_profile", "legacy_4x")
    return evaluate_artifact(source, **kwargs)


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


def test_sandbox_blocks_network_and_host_filesystem() -> None:
    source = textwrap.dedent("""
        import os
        HOME_VISIBLE = os.path.exists("/home/moltbot1")
        SSH_VISIBLE = os.path.exists("/home/moltbot1/.ssh")
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


def test_sandbox_drops_to_openpine_worker_when_host_allows() -> None:
    from openpine.runtime.isolated_worker import worker_user_available

    result = _eval(b"VALUE = 1\n", timeout_s=5)
    if worker_user_available():
        assert result["isolation"]["uid"] != 1000
        assert result["isolation"]["uid"] > 0
    else:
        assert result["isolation"]["uid"] > 0


def test_worker_rejects_huge_source_and_subprocess() -> None:
    with pytest.raises(IsolatedWorkerError, match="size limit"):
        _eval(b"x = 1\n" * 100_000, timeout_s=5)
    with pytest.raises(IsolatedWorkerError, match="subprocess"):
        _eval(b'__import__("subprocess")\n', timeout_s=5)


def test_worker_argv_has_no_new_session() -> None:
    from openpine.runtime.isolated_worker import (
        IsolatedWorkerError,
        TRUSTED_DEST,
        _BOOTSTRAP,
        _bwrap_argv,
    )

    argv: list[str] = []
    try:
        argv = _bwrap_argv()
    except IsolatedWorkerError:
        pytest.skip("bubblewrap missing")
    assert "--new-session" not in argv
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv
    assert "--clearenv" in argv
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


def test_worker_argv_mounts_optional_lib64_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from openpine.runtime.isolated_worker import _runtime_ro_bind_args

    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: str(self) in {"/usr", "/lib", "/lib64"},
    )

    argv = _runtime_ro_bind_args()

    assert argv == [
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


def test_worker_handshake_rejects_unknown_stack_and_profile() -> None:
    with pytest.raises(IsolatedWorkerError, match="stack_id"):
        evaluate_artifact(b"VALUE = 1\n", stack_id="wrong")
    with pytest.raises(IsolatedWorkerError, match="semantic_profile"):
        evaluate_artifact(b"VALUE = 1\n", semantic_profile="nope")


def test_evaluate_artifact_requires_semantic_profile() -> None:
    with pytest.raises(IsolatedWorkerError, match="semantic_profile"):
        evaluate_artifact(b"VALUE = 1\n")


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
    assert event["origin_command_kind"] == "entry.long"
    assert event["content_hash"]
    from openpine.runtime.isolated_worker import _TRUSTED_STAGE, _bwrap_argv

    argv = _bwrap_argv()
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
