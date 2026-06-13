from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from openpine.cli.config import config as config_group
from openpine.cli.optimizer import optimizer as optimizer_group
from openpine.cli.reports import (
    _find_report_files,
    _load_report_file,
    _report_search_names,
    reports as reports_group,
)


@dataclass
class _FakeConfig:
    workspace_root: Path
    data_cache_root: Path
    output_root: Path
    db_path: Path
    data_dir: Path
    config_dir: Path
    sqlite_path: Path
    duckdb_path: Path
    log_level: str = "INFO"
    timezone: str = "UTC"
    live_enabled: bool = False
    kill_switch: bool = False
    plugins: object = field(
        default_factory=lambda: SimpleNamespace(
            telegram=SimpleNamespace(enabled=False, token_ref=None)
        )
    )


def _config(tmp_path: Path, **overrides: object) -> _FakeConfig:
    values = dict(
        workspace_root=tmp_path,
        data_cache_root=tmp_path / "cache",
        output_root=tmp_path / "out",
        db_path=tmp_path / "db",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        sqlite_path=tmp_path / "openpine.sqlite",
        duckdb_path=tmp_path / "openpine.duckdb",
    )
    values.update(overrides)
    return _FakeConfig(**values)


def test_config_show_and_validate_use_configured_timezone(monkeypatch, tmp_path):
    cfg = _config(tmp_path, timezone="UTC+03:00")
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)

    runner = CliRunner()
    show = runner.invoke(config_group, ["show"])
    assert show.exit_code == 0
    assert "UTC+03:00" in show.output

    validate = runner.invoke(config_group, ["validate"])
    assert validate.exit_code == 0
    assert "Configuration is valid" in validate.output


def test_config_validate_reports_errors(monkeypatch, tmp_path):
    cfg = _config(tmp_path, log_level="TRACE", timezone="Mars/Phobos")
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)

    result = CliRunner().invoke(config_group, ["validate"])
    assert result.exit_code == 1
    assert "Validation failed" in result.output
    assert "log_level" in result.output
    assert "Unsupported timezone" in result.output


def test_report_file_helpers_find_json_and_text(tmp_path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    json_path = reports_dir / "data_coverage.json"
    text_path = reports_dir / "worker-health.txt"
    json_path.write_text('{"ok": true}')
    text_path.write_text("worker ok")

    assert _report_search_names("worker_health") == {"worker_health", "worker-health"}
    assert _load_report_file(json_path) == {"ok": True}
    assert _load_report_file(text_path) == "worker ok"
    assert _find_report_files(reports_dir, "worker_health") == [text_path]
    assert _find_report_files(tmp_path / "missing", "anything") == []


def test_reports_cli_list_show_export_and_missing(monkeypatch, tmp_path):
    cfg = _config(tmp_path)
    reports_dir = cfg.data_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "strategy_summary.json").write_text('{"trades": 2, "pnl": 5}')
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)

    runner = CliRunner()
    listed = runner.invoke(reports_group, ["list"])
    assert listed.exit_code == 0
    assert "strategy_summary" in listed.output

    shown = runner.invoke(reports_group, ["show", "strategy-summary"])
    assert shown.exit_code == 0
    assert '"trades": 2' in shown.output

    exported = runner.invoke(
        reports_group, ["export", "strategy_summary", "--format", "csv"]
    )
    assert exported.exit_code == 0
    assert "trades,pnl" in exported.output

    fallback = runner.invoke(
        reports_group, ["export", "worker_health", "--format", "json"]
    )
    assert fallback.exit_code == 0
    assert '"id": "worker_health"' in fallback.output

    missing = runner.invoke(reports_group, ["show", "missing_report"])
    assert missing.exit_code == 1
    assert "Report not found" in missing.output


def test_reports_export_text_file(monkeypatch, tmp_path):
    cfg = _config(tmp_path)
    reports_dir = cfg.data_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "custom.txt").write_text("plain report")
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)

    runner = CliRunner()
    json_export = runner.invoke(reports_group, ["export", "custom", "--format", "json"])
    assert json_export.exit_code == 0
    assert '"content": "plain report"' in json_export.output

    csv_export = runner.invoke(reports_group, ["export", "custom", "--format", "csv"])
    assert csv_export.exit_code == 0
    assert "plain report" in csv_export.output


def test_optimizer_dry_run_success_and_invalid_trials(monkeypatch):
    class FakeService:
        def validate_config(self, *, strategy_id: str, trials: int):
            return SimpleNamespace(
                strategy_id=strategy_id,
                trials_requested=trials,
                status="ok",
                reason="ready",
            )

    monkeypatch.setattr("openpine.optimizer.OptimizerService", lambda: FakeService())
    runner = CliRunner()
    ok = runner.invoke(
        optimizer_group, ["dry-run", "--strategy", "s1", "--trials", "3"]
    )
    assert ok.exit_code == 0
    assert "s1" in ok.output
    assert "ready" in ok.output

    bad = runner.invoke(
        optimizer_group, ["dry-run", "--strategy", "s1", "--trials", "0"]
    )
    assert bad.exit_code == 1
    assert "--trials must be >= 1" in bad.output
