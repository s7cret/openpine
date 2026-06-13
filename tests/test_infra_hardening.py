from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.notifications.telegram import TelegramBotHandler, TelegramCommandPlugin, TelegramPluginConfig


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


def test_systemd_gateway_runner_sources_env_in_current_shell() -> None:
    script = _read("scripts/run_gateway_systemd.sh")

    assert "set -a" in script
    assert ". ./.env" in script or "source ./.env" in script
    assert "set +a" in script
    assert "(set -a" not in script
    assert 'exec "${OPENPINE_PYTHON:-python}"' in script
    assert "uvicorn.run(create_app()" in script


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
