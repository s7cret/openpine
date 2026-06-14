from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
import pydantic
import pytest
import yaml

from openpine.config.model import OpenPineConfig
from openpine.gateway.routes import settings


def _state(tmp_path: Path) -> SimpleNamespace:
    cfg = OpenPineConfig(
        workspace_root=tmp_path,
        config_dir=tmp_path / ".openpine",
        timezone="UTC+03:00",
        marketdata_stable_quotes_only=True,
        marketdata_stable_quote_assets=("USDT", "USDC"),
        marketdata_symbol_search_limit=25,
        marketdata_timeframes=("1m", "3m", "5m", "1h"),
        marketdata_default_timeframe="3m",
    )
    return SimpleNamespace(config=cfg)


def test_default_stable_quote_assets_keep_usd_for_native_exchanges(tmp_path: Path) -> None:
    cfg = OpenPineConfig(workspace_root=tmp_path)

    assert "USD" in cfg.marketdata_stable_quote_assets


def test_settings_payload_exposes_persisted_marketdata_defaults(tmp_path: Path) -> None:
    payload = asyncio.run(settings.get_settings(_state(tmp_path)))

    assert payload["timezone"] == "UTC+03:00"
    assert payload["timezone_label"] == "MSK"
    assert payload["marketdata"]["stable_quotes_only"] is True
    assert payload["marketdata"]["stable_quote_assets"] == ["USDT", "USDC"]
    assert payload["marketdata"]["symbol_search_limit"] == 25
    assert payload["marketdata"]["timeframes"] == ["1m", "3m", "5m", "1h"]
    assert payload["marketdata"]["default_timeframe"] == "3m"
    assert "3m" in payload["marketdata"]["supported_timeframes"]


def test_update_settings_validates_and_saves_runtime_config(tmp_path: Path) -> None:
    state = _state(tmp_path)

    payload = asyncio.run(settings.update_settings(
        {
            "timezone": "Europe/Moscow",
            "marketdata": {
                "stable_quotes_only": False,
                "stable_quote_assets": ["usdt", "usdc", "USDT", ""],
                "symbol_search_limit": 75,
                "timeframes": ["3m", "1m", "3m", "1h"],
                "default_timeframe": "1h",
            },
        },
        state,
    ))

    assert state.config.timezone == "Europe/Moscow"
    assert state.config.marketdata_stable_quotes_only is False
    assert state.config.marketdata_stable_quote_assets == ("USDT", "USDC")
    assert state.config.marketdata_symbol_search_limit == 75
    assert state.config.marketdata_timeframes == ("3m", "1m", "1h")
    assert state.config.marketdata_default_timeframe == "1h"
    assert payload["marketdata"]["stable_quote_assets"] == ["USDT", "USDC"]
    assert (tmp_path / ".openpine" / "config.yaml").exists()


def test_loaded_config_without_config_dir_saves_settings_to_active_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openpine.config.loader as config_loader

    monkeypatch.setattr(config_loader, "load_env_file", lambda: None)
    config_path = tmp_path / "active" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("timezone: UTC\nmarketdata_symbol_search_limit: 25\n", encoding="utf-8")

    cfg = config_loader.load_config(config_path)

    assert cfg.config_path() == config_path


def test_update_settings_does_not_persist_env_only_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openpine.config.loader as config_loader

    monkeypatch.setattr(config_loader, "load_env_file", lambda: None)
    monkeypatch.setenv("OPENPINE_MARKETDATA_SYMBOL_SEARCH_LIMIT", "99")
    config_path = tmp_path / "active" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("timezone: UTC\nmarketdata_symbol_search_limit: 25\n", encoding="utf-8")
    state = SimpleNamespace(config=config_loader.load_config(config_path))

    payload = asyncio.run(settings.update_settings({"timezone": "Europe/Moscow"}, state))
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert payload["timezone"] == "Europe/Moscow"
    assert payload["marketdata"]["symbol_search_limit"] == 99
    assert state.config.marketdata_symbol_search_limit == 99
    assert saved["marketdata_symbol_search_limit"] == 25
    assert saved["timezone"] == "Europe/Moscow"


def test_update_settings_rejects_unbounded_symbol_search_limit(tmp_path: Path) -> None:
    state = _state(tmp_path)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(settings.update_settings({"marketdata": {"symbol_search_limit": 50_000}}, state))

    assert excinfo.value.status_code == 422


def test_update_settings_rejects_non_object_marketdata_and_bad_config_file(tmp_path: Path) -> None:
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as bad_marketdata:
        settings._updated_config(state.config, {"marketdata": ["bad"]})
    assert bad_marketdata.value.status_code == 422

    config_path = state.config.config_path()
    config_path.parent.mkdir(parents=True)
    config_path.write_text("- not\n- object\n", encoding="utf-8")
    with pytest.raises(HTTPException) as bad_file:
        settings._raw_config_data(state.config)
    assert bad_file.value.status_code == 422


def test_config_save_replaces_config_symlink_without_following_target(tmp_path: Path) -> None:
    cfg = OpenPineConfig(workspace_root=tmp_path, config_dir=tmp_path / ".openpine", timezone="UTC")
    cfg.config_dir.mkdir(parents=True)
    target = tmp_path / "outside.yaml"
    target.write_text("timezone: UTC\n", encoding="utf-8")
    cfg.config_path().symlink_to(target)

    cfg.save()

    assert target.read_text(encoding="utf-8") == "timezone: UTC\n"
    assert not cfg.config_path().is_symlink()
    assert "timezone: UTC" in cfg.config_path().read_text(encoding="utf-8")


def test_config_save_removes_temp_file_when_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = OpenPineConfig(workspace_root=tmp_path, config_dir=tmp_path / ".openpine", timezone="UTC")

    def fail_dump(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(yaml, "safe_dump", fail_dump)

    with pytest.raises(RuntimeError, match="boom"):
        cfg.save()

    assert list(cfg.config_dir.glob("config.*.tmp")) == []
    assert not cfg.config_path().exists()


def test_config_loader_applies_marketdata_timeframes_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import openpine.config.loader as config_loader

    monkeypatch.setattr(config_loader, "load_env_file", lambda: None)
    monkeypatch.setenv("OPENPINE_MARKETDATA_TIMEFRAMES", "5m, 1M, ,1h")
    monkeypatch.setenv("OPENPINE_MARKETDATA_DEFAULT_TIMEFRAME", "1M")

    cfg = config_loader.load_config(tmp_path / "missing.yaml")

    assert cfg.marketdata_timeframes == ("5m", "1M", "1h")
    assert cfg.marketdata_default_timeframe == "1M"


def test_config_marketdata_validators_cover_invalid_and_string_inputs(tmp_path: Path) -> None:
    cfg = OpenPineConfig(
        workspace_root=tmp_path,
        marketdata_timeframes="M, 1m, ,1m",
        marketdata_default_timeframe="1mo",
    )
    assert cfg.marketdata_timeframes == ("1M", "1m")
    assert cfg.marketdata_default_timeframe == "1M"

    invalid_cases = [
        {"marketdata_symbol_search_limit": 0},
        {"marketdata_timeframes": ["1m", "bad"]},
        {"marketdata_timeframes": " , "},
        {"marketdata_default_timeframe": "bad"},
        {"marketdata_timeframes": ["1m"], "marketdata_default_timeframe": "3m"},
    ]
    for kwargs in invalid_cases:
        with pytest.raises(pydantic.ValidationError):
            OpenPineConfig(workspace_root=tmp_path, **kwargs)


def test_updated_config_wraps_plain_value_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state(tmp_path)

    def raise_value_error(**_data):
        raise ValueError("plain failure")

    monkeypatch.setattr(settings, "OpenPineConfig", raise_value_error)

    with pytest.raises(HTTPException) as excinfo:
        settings._updated_config(state.config, {"timezone": "UTC"})

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "plain failure"
