from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from openpine.runtime.isolated_worker import IsolatedWorkerError, evaluate_artifact


def test_parent_does_not_import_generated_module() -> None:
    source = "VALUE = 42\n"
    result = evaluate_artifact(source.encode("utf-8"), timeout_s=5)
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
        result = evaluate_artifact(source.encode("utf-8"), timeout_s=5)
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
    result = evaluate_artifact(payload, timeout_s=5)
    assert result["ok"] is True
    assert result["namespace"]["VALUE"] == 1


def test_worker_rejects_socket_import() -> None:
    source = "import socket\n"
    with pytest.raises(IsolatedWorkerError, match="socket"):
        evaluate_artifact(source.encode("utf-8"), timeout_s=5)


def test_worker_times_out_infinite_loop() -> None:
    source = "while True:\n    pass\n"
    with pytest.raises(IsolatedWorkerError, match="timeout"):
        evaluate_artifact(source.encode("utf-8"), timeout_s=0.4)


def test_worker_rejects_malformed_and_nonzero_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import openpine.runtime.isolated_worker as worker

    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="not-json", stderr=""
        ),
    )
    with pytest.raises(IsolatedWorkerError, match="malformed"):
        evaluate_artifact(b"VALUE = 1\n", timeout_s=1)

    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with pytest.raises(IsolatedWorkerError, match="boom"):
        evaluate_artifact(b"VALUE = 1\n", timeout_s=1)


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
    result = evaluate_artifact(source.encode("utf-8"), timeout_s=5)
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
        evaluate_artifact(b'__import__("socket")\n', timeout_s=5)


def test_in_process_generated_import_is_forbidden(tmp_path: Path) -> None:
    from openpine.runtime.engine import BacktestArtifactError, _load_generated_module

    path = tmp_path / "generated_strategy.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(BacktestArtifactError, match="in-process"):
        _load_generated_module(path, "src", "art")


def test_sandbox_drops_to_openpine_worker_when_host_allows() -> None:
    from openpine.runtime.isolated_worker import worker_user_available

    result = evaluate_artifact(b"VALUE = 1\n", timeout_s=5)
    if worker_user_available():
        assert result["isolation"]["uid"] != 1000
        assert result["isolation"]["uid"] > 0
    else:
        assert result["isolation"]["uid"] > 0


def test_worker_rejects_huge_source_and_subprocess() -> None:
    with pytest.raises(IsolatedWorkerError, match="size limit"):
        evaluate_artifact(b"x = 1\n" * 100_000, timeout_s=5)
    with pytest.raises(IsolatedWorkerError, match="subprocess"):
        evaluate_artifact(b'__import__("subprocess")\n', timeout_s=5)
