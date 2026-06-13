from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest


def test_fixed_slash_commands_and_remaining_callback_mappings():
    from openpine.telegram_commands import (
        TelegramCommandError,
        map_callback_data,
        map_telegram_command,
    )

    with pytest.raises(TelegramCommandError, match="/status does not accept arguments"):
        map_telegram_command("/status extra")

    expected_mappings = {
        "op:strategy:paper_stop:sid": ["strategy", "paper", "sid", "stop"],
        "op:strategy:live_start:sid": ["strategy", "live", "sid", "start"],
        "op:strategy:live_stop:sid": ["strategy", "live", "sid", "stop"],
        "op:strategy:error_clear:sid": ["strategy", "error", "sid", "clear"],
        "op:strat:paper_start:sid": ["strategy", "paper", "sid", "start"],
        "op:strat:paper_stop:sid": ["strategy", "paper", "sid", "stop"],
        "op:strat:live_enable:sid": ["strategy", "live", "sid", "enable"],
        "op:strat:live_start:sid": ["strategy", "live", "sid", "start"],
        "op:strat:live_stop:sid": ["strategy", "live", "sid", "stop"],
        "op:strat:error_clear:sid": ["strategy", "error", "sid", "clear"],
    }
    for callback_data, argv in expected_mappings.items():
        assert map_callback_data(callback_data) == argv

    with pytest.raises(TelegramCommandError, match="unknown callback data"):
        map_callback_data("op:reports:bad:summary")


def test_stdlib_transport_get_file_posts_get_file_payload_without_network():
    from openpine.notifications import telegram

    class RecordingTransport(telegram.StdlibHTTPTransport):
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def _post_json(self, token: str, method: str, payload: dict[str, object]):
            self.calls.append((token, method, payload))
            return {"ok": True, "result": {"file_path": "docs/test.pine"}}

    transport = RecordingTransport()

    assert transport.get_file("token-123", "file-abc") == {
        "ok": True,
        "result": {"file_path": "docs/test.pine"},
    }
    assert transport.calls == [("token-123", "getFile", {"file_id": "file-abc"})]


def test_bot_handler_list_keyboard_builders_fall_back_to_empty_on_bad_json(monkeypatch):
    from openpine.notifications import telegram

    class Keyboards:
        def __init__(self) -> None:
            self.strategy_inputs: list[list[dict[str, object]]] = []
            self.pine_inputs: list[list[dict[str, object]]] = []

        def strategy_list_keyboard(self, strategies):
            self.strategy_inputs.append(strategies)
            return {"strategies": strategies}

        def pine_list_keyboard(self, sources):
            self.pine_inputs.append(sources)
            return {"pine": sources}

    cli_calls: list[tuple[list[str], str]] = []

    def fake_run_cli_argv(argv: list[str], cli_path: str = "openpine") -> str:
        cli_calls.append((argv, cli_path))
        return "{not valid json"

    monkeypatch.setattr(telegram, "_run_cli_argv", fake_run_cli_argv)
    keyboards = Keyboards()
    handler = telegram.TelegramBotHandler(
        plugin=cast(Any, SimpleNamespace()),
        commands_module=cast(Any, keyboards),
        cli_path="openpine-test",
    )

    assert handler._build_strategies_list_keyboard() == {"strategies": []}
    assert handler._build_pine_list_keyboard() == {"pine": []}

    assert keyboards.strategy_inputs == [[]]
    assert keyboards.pine_inputs == [[]]
    assert cli_calls == [
        (["strategy", "list", "--json"], "openpine-test"),
        (["pine", "list", "--json"], "openpine-test"),
    ]


@pytest.mark.asyncio
async def test_telegram_daemon_stop_logs_timeout_for_stubborn_poll_task(monkeypatch):
    from openpine.daemon import telegram_service

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        telegram_service,
        "log",
        SimpleNamespace(
            info=lambda event, **kwargs: events.append(("info", event)),
            warning=lambda event, **kwargs: events.append(("warning", event)),
        ),
    )

    class Handler:
        async def stop(self) -> None:
            events.append(("handler", "stop"))

    async def stubborn_poll_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise

    service = telegram_service.TelegramDaemonService()
    service._handler = cast(Any, Handler())
    service._poll_task = asyncio.create_task(stubborn_poll_task())
    await asyncio.sleep(0)

    await service._on_stop(timeout=0.01)

    assert ("handler", "stop") in events
    assert ("warning", "telegram_daemon.stop.timeout") in events
    assert service._poll_task.done()
