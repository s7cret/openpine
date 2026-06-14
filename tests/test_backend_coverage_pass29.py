from __future__ import annotations

import importlib
import sqlite3
import tarfile
from pathlib import Path

from click.testing import CliRunner

from openpine.config import OpenPineConfig
from openpine.storage import backup as backup_mod

cli_main = importlib.import_module("openpine.cli.main")


def test_cli_run_command_indicator_and_strategy_paths(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> str:
        calls.append(list(args))
        if args[:2] == ["strategy", "create"]:
            return "Strategy created: strat_1\n"
        return "ok\n"

    monkeypatch.setattr(cli_main, "_run_openpine_cli", fake_run)
    indicator = tmp_path / "001_alpha_indicator.pine"
    indicator.write_text("//@version=6\nindicator('x')\nplot(close)\n", encoding="utf-8")
    result = runner.invoke(
        cli_main.cli,
        [
            "run",
            str(indicator),
            "--symbol",
            "BTCUSDT",
            "--timeframe",
            "1m",
            "--from",
            "2026-01-01",
            "--to",
            "2026-01-02",
            "--compare-from",
            "2026-01-01",
            "--compare-to",
            "2026-01-02",
            "--output",
            str(tmp_path / "out1"),
            "--tv-chart",
            str(tmp_path / "tv.csv"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert ["pine", "pine-compile", "po_0001_001_alpha_indicator"] in calls
    assert any(call[:2] == ["pine", "run-plots"] and "--compare-from" in call for call in calls)
    assert any(call[:2] == ["pine", "compare-tv"] for call in calls)

    calls.clear()
    strategy = tmp_path / "beta_strategy.pine"
    strategy.write_text("//@version=6\nstrategy('s')\n", encoding="utf-8")
    result = runner.invoke(
        cli_main.cli,
        [
            "run",
            str(strategy),
            "--symbol",
            "ETHUSDT",
            "--timeframe",
            "5m",
            "--from",
            "2026-01-01",
            "--history-from",
            "2025-12-31",
            "--compare-from",
            "2026-01-01",
            "--capture-plots",
            "--output",
            str(tmp_path / "out2"),
            "--tv-chart",
            str(tmp_path / "chart.csv"),
            "--tv-trades",
            str(tmp_path / "trades.csv"),
            "--tv-equity",
            str(tmp_path / "equity.csv"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert any(call[:2] == ["strategy", "backtest"] and "--history-from" in call for call in calls)
    assert any(call[:2] == ["strategy", "compare-tv"] and "--tv-trades" in call for call in calls)


def test_cli_run_command_errors(monkeypatch, tmp_path: Path):
    runner = CliRunner()
    bad = tmp_path / "bad.pine"
    bad.write_text("//@version=6\nplot(close)\n", encoding="utf-8")
    result = runner.invoke(
        cli_main.cli,
        ["run", str(bad), "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--output", str(tmp_path / "out")],
    )
    assert result.exit_code != 0
    assert "Cannot detect" in result.output

    strat = tmp_path / "s.pine"
    strat.write_text("//@version=6\nstrategy('s')\n", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_run_openpine_cli", lambda args: "created but no id")
    result = runner.invoke(
        cli_main.cli,
        ["run", str(strat), "--symbol", "BTCUSDT", "--timeframe", "1m", "--from", "2026-01-01", "--output", str(tmp_path / "out")],
    )
    assert result.exit_code != 0
    assert "Could not parse created strategy id" in result.output


def test_storage_backup_restore_verify_and_redaction(monkeypatch, tmp_path: Path):
    config = OpenPineConfig(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "data" / "control.sqlite",
        duckdb_path=tmp_path / "data" / "analytics.duckdb",
    )
    config.config_dir.mkdir(parents=True)
    config.data_dir.mkdir(parents=True)
    (config.data_dir / "artifacts").mkdir()
    (config.data_dir / "artifacts" / "a.txt").write_text("a", encoding="utf-8")
    (config.data_dir / "manifests").mkdir()
    (config.data_dir / "state").mkdir()
    conn = sqlite3.connect(config.sqlite_path)
    conn.execute("create table t(x int)")
    conn.commit()
    conn.close()
    config.duckdb_path.write_text("duck", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    backed = backup_mod.backup_openpine(archive, config=config)
    assert archive.exists() and any("control.sqlite" in path for path in backed)
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "config/manifest.json" in names
    target = tmp_path / "restore"
    backup_mod.restore_openpine(archive, target_dir=target)
    assert (target / "config" / "manifest.json").exists()
    assert backup_mod.verify_openpine(config)["sqlite_integrity"] is True
    empty_cfg = OpenPineConfig(
        config_dir=tmp_path / "empty_cfg",
        data_dir=tmp_path / "empty_data",
        sqlite_path=tmp_path / "missing.sqlite",
        duckdb_path=tmp_path / "missing.duckdb",
    )
    checks = backup_mod.verify_openpine(empty_cfg)
    assert checks["sqlite_exists"] is False and checks["sqlite_integrity"] is False
    redacted = {"token": "secret", "nested": {"api_key": "x"}, "items": [{"password": "p"}]}
    backup_mod._redact_sensitive(redacted)
    assert redacted == {"token": "<REDACTED>", "nested": {"api_key": "<REDACTED>"}, "items": [{"password": "<REDACTED>"}]}


def test_storage_backup_restore_errors(tmp_path: Path):
    missing = tmp_path / "missing.tar.gz"
    try:
        backup_mod.restore_openpine(missing, tmp_path / "restore")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("missing backup should fail")

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo("x.txt")
        data = b"x"
        info.size = len(data)
        import io

        tar.addfile(info, io.BytesIO(data))
    try:
        backup_mod.restore_openpine(bad, tmp_path / "restore2")
    except ValueError as exc:
        assert "missing manifest" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("bad backup should fail")
