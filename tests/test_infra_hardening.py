from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from openpine.notifications.telegram import (
    TelegramBotHandler,
    TelegramCommandPlugin,
    TelegramPluginConfig,
)


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["Dockerfile", "docker-compose.yml", "openpine-gateway.service"])
def test_deployment_artifacts_do_not_enable_pickle_snapshots_by_default(rel: str) -> None:
    text = _read(rel)

    assert "OPENPINE_ALLOW_PICKLE_STATE=1" not in text
    assert 'OPENPINE_ALLOW_PICKLE_STATE: "1"' not in text


def test_dockerfile_copies_openpine_package_tree() -> None:
    text = _read("Dockerfile")

    assert "COPY openpine ./openpine" in text
    assert "COPY accounts ./accounts" not in text
    assert "COPY __init__.py integrations.py exchange_metadata.py ./" not in text


def test_systemd_gateway_has_bounded_memory_and_worker_restart_policy() -> None:
    unit = _read("openpine-gateway.service")

    for setting in (
        "MemoryAccounting=yes",
        "MemoryHigh=1G",
        "MemoryMax=1536M",
        "MemorySwapMax=512M",
        "TasksMax=256",
        "OOMPolicy=continue",
        "Environment=OPENPINE_WORKER_MAX_RESTARTS=3",
        "Environment=OPENPINE_WORKER_RESTART_WINDOW_SECONDS=300",
        "Environment=OPENPINE_WORKER_RESTART_BACKOFF_MAX_SECONDS=30",
    ):
        assert setting in unit


def test_systemd_gateway_runner_sources_env_in_current_shell() -> None:
    script = _read("scripts/run_gateway_systemd.sh")

    assert '"${1:-}" == "--systemd-env-loaded"' in script
    assert "set -a" in script
    assert ". ./.env" in script or "source ./.env" in script
    assert "set +a" in script
    assert "(set -a" not in script
    assert 'exec "${OPENPINE_PYTHON:-python}"' in script
    assert "uvicorn.run(create_app()" in script


def test_systemd_gateway_requires_immutable_identity_after_runtime_env() -> None:
    unit = _read("openpine-gateway.service")

    runtime_env = "EnvironmentFile=/opt/openpine/.env"
    identity_env = "EnvironmentFile=/etc/openpine/immutable-identity.env"
    assert runtime_env in unit
    assert identity_env in unit
    assert unit.index(runtime_env) < unit.index(identity_env)
    assert "ExecStart=/opt/openpine/scripts/run_gateway_systemd.sh --systemd-env-loaded" in unit


def test_systemd_runner_flag_prevents_dotenv_identity_override(tmp_path: Path) -> None:
    expected = "a" * 40
    (tmp_path / ".env").write_text(
        "OPENPINE_BUILD_COMMIT=" + "b" * 40 + "\nOPENPINE_SKIP_DOTENV=0\n",
        encoding="utf-8",
    )
    output = tmp_path / "result.txt"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$OPENPINE_BUILD_COMMIT" > "$OPENPINE_PROBE_OUTPUT"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "OPENPINE_ROOT": str(tmp_path),
            "OPENPINE_PYTHON": str(fake_python),
            "OPENPINE_BUILD_COMMIT": expected,
            "OPENPINE_PROBE_OUTPUT": str(output),
        }
    )

    result = subprocess.run(  # noqa: S603
        [
            "/usr/bin/bash",
            str(ROOT / "scripts" / "run_gateway_systemd.sh"),
            "--systemd-env-loaded",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").strip() == expected


def test_telegram_poll_advances_offset_after_failed_update(monkeypatch: pytest.MonkeyPatch) -> None:
    class Transport:
        def send(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(ok=True, error_message=None)

        def get_updates(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {
                "ok": True,
                "result": [
                    {"update_id": 41, "message": {"chat": {"id": 42}, "text": "/bad"}},
                ],
            }

    monkeypatch.setenv("OPENPINE_TELEGRAM_TOKEN", "token")
    plugin = TelegramCommandPlugin(
        config=TelegramPluginConfig(enabled=True, chat_allowlist=["42"]),
        transport=Transport(),
    )
    handler = TelegramBotHandler(plugin)
    processed: list[int] = []

    def process(update):  # noqa: ANN001
        processed.append(update.update_id)
        if update.update_id == 41:
            raise RuntimeError("poison update")

    monkeypatch.setattr(handler, "_process_update", process)

    assert handler._poll_once() == 0
    assert processed == [41]
    assert handler._offset == 42


def test_telegram_poll_never_regresses_existing_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    class Transport:
        def send(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(ok=True, error_message=None)

        def get_updates(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {
                "ok": True,
                "result": [
                    {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/ok"}},
                ],
            }

    monkeypatch.setenv("OPENPINE_TELEGRAM_TOKEN", "token")
    plugin = TelegramCommandPlugin(
        config=TelegramPluginConfig(enabled=True, chat_allowlist=["42"]),
        transport=Transport(),
    )
    handler = TelegramBotHandler(plugin)
    handler._offset = 42
    monkeypatch.setattr(handler, "_process_update", lambda update: None)

    assert handler._poll_once() == 1
    assert handler._offset == 42
