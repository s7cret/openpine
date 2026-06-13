from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from openpine.config.loader import load_config
from openpine.config.model import OpenPineConfig
from openpine.timezones import (
    DEFAULT_TIMEZONE,
    configured_timezone,
    parse_timestamp_ms,
    parse_ymd_ms,
    resolve_timezone,
)


def test_timezone_resolution_supports_msk_offsets_utc_and_iana() -> None:
    assert resolve_timezone("MSK").name == DEFAULT_TIMEZONE
    assert resolve_timezone("UTC+3").name == DEFAULT_TIMEZONE
    assert resolve_timezone("+03:00").label == "MSK"
    assert resolve_timezone("UTC").tz is timezone.utc
    assert resolve_timezone("Europe/Moscow").label == "Europe/Moscow"
    with pytest.raises(ValueError):
        resolve_timezone("UTC+99:00")


def test_date_parsing_uses_configured_timezone_for_naive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENPINE_TIMEZONE", raising=False)
    assert parse_ymd_ms("2024-01-01") == 1_704_056_400_000
    assert parse_timestamp_ms("2024-01-01T00:00:00", 0) == 1_704_056_400_000
    assert parse_timestamp_ms("2024-01-01T00:00:00Z", 0) == 1_704_067_200_000
    assert parse_timestamp_ms("1700000000", 0) == 1_700_000_000_000
    assert parse_timestamp_ms(None, 42) == 42

    monkeypatch.setenv("OPENPINE_TIMEZONE", "UTC")
    assert configured_timezone().name == "UTC"
    assert parse_ymd_ms("2024-01-01") == 1_704_067_200_000


def test_config_timezone_field_and_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = OpenPineConfig(workspace_root=tmp_path, timezone="MSK")
    assert cfg.timezone == DEFAULT_TIMEZONE

    config_path = tmp_path / "config.yaml"
    config_path.write_text("timezone: UTC\n", encoding="utf-8")
    assert load_config(config_path).timezone == "UTC"

    monkeypatch.setenv("OPENPINE_TIMEZONE", "UTC+03:00")
    assert load_config(config_path).timezone == DEFAULT_TIMEZONE


def test_config_cli_prints_timezone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENPINE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("OPENPINE_TIMEZONE", "UTC")
    from openpine.cli.config import config

    result = CliRunner().invoke(config, ["show"])
    assert result.exit_code == 0, result.output
    assert "timezone:" in result.output
    assert "UTC" in result.output


def test_timezone_report_is_json_serializable() -> None:
    payload = {"timezone": resolve_timezone("MSK").name}
    assert json.loads(json.dumps(payload))["timezone"] == DEFAULT_TIMEZONE


def test_parse_helpers_use_yaml_config_when_env_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("timezone: UTC\n", encoding="utf-8")
    monkeypatch.setenv("OPENPINE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("OPENPINE_TIMEZONE", raising=False)

    assert configured_timezone().name == "UTC"
    assert parse_ymd_ms("2024-01-01") == 1_704_067_200_000
