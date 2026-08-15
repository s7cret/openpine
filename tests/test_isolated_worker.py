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
