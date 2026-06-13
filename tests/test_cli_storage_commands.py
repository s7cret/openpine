from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from openpine.cli.storage import _fmt_utc_seconds, storage as storage_group


class _Config(SimpleNamespace):
    pass


def _config(tmp_path: Path) -> _Config:
    return _Config(
        sqlite_path=tmp_path / "openpine.sqlite",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path,
        config_dir=tmp_path / "cfg",
        db_path=tmp_path / "db",
        output_root=tmp_path / "out",
        data_cache_root=tmp_path / "cache",
    )


def test_storage_init_schema_and_migrate(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)
    runner = CliRunner()

    dry = runner.invoke(storage_group, ["init", "--dry-run"])
    assert dry.exit_code == 0
    assert "Dry run" in dry.output
    assert not cfg.sqlite_path.exists()

    init = runner.invoke(storage_group, ["init"])
    assert init.exit_code == 0
    assert "Storage initialized" in init.output
    assert cfg.sqlite_path.exists()

    schema = runner.invoke(storage_group, ["schema"])
    assert schema.exit_code == 0
    assert "schema_migrations" in schema.output

    migrate = runner.invoke(storage_group, ["migrate"])
    assert migrate.exit_code == 0
    assert "Applied migrations" in migrate.output
    assert "No pending migrations" in migrate.output


def test_storage_schema_reports_missing_database(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)

    result = CliRunner().invoke(storage_group, ["schema"])
    assert result.exit_code == 0
    assert "Database not found" in result.output


def test_storage_backup_restore_verify_success_and_failure(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)
    calls: list[tuple[str, object]] = []

    def fake_backup(out_path, config):
        calls.append(("backup", out_path))
        return [Path("openpine.sqlite"), Path("config.yaml")]

    def fake_restore(backup_path, target_path):
        calls.append(("restore", (backup_path, target_path)))

    def fake_verify(config):
        calls.append(("verify", config))
        return {"sqlite_exists": True, "sqlite_integrity": True, "logs_present": False}

    monkeypatch.setattr("openpine.storage.backup.backup_openpine", fake_backup)
    monkeypatch.setattr("openpine.storage.backup.restore_openpine", fake_restore)
    monkeypatch.setattr("openpine.storage.backup.verify_openpine", fake_verify)

    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("x")
    runner = CliRunner()
    backup = runner.invoke(storage_group, ["backup", "--out", str(backup_path)])
    assert backup.exit_code == 0
    assert "Backup complete" in backup.output

    restore = runner.invoke(
        storage_group,
        ["restore", str(backup_path), "--target", str(tmp_path / "restore")],
    )
    assert restore.exit_code == 0
    assert "Restore complete" in restore.output

    verify = runner.invoke(storage_group, ["verify"])
    assert verify.exit_code == 0
    assert "Warnings" in verify.output
    assert calls[0][0] == "backup"

    monkeypatch.setattr(
        "openpine.storage.backup.verify_openpine",
        lambda config: {"sqlite_exists": False},
    )
    failed = runner.invoke(storage_group, ["verify"])
    assert failed.exit_code == 1
    assert "Critical storage checks failed" in failed.output


def test_storage_backup_restore_failures(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr("openpine.config.OpenPineConfig.load", lambda: cfg)
    monkeypatch.setattr(
        "openpine.storage.backup.backup_openpine",
        lambda *args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "openpine.storage.backup.restore_openpine",
        lambda *args: (_ for _ in ()).throw(RuntimeError("nope")),
    )

    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("x")
    runner = CliRunner()
    backup = runner.invoke(
        storage_group, ["backup", "--out", str(tmp_path / "out.tar.gz")]
    )
    assert backup.exit_code == 1
    assert "Backup failed" in backup.output

    restore = runner.invoke(storage_group, ["restore", str(backup_path)])
    assert restore.exit_code == 1
    assert "Restore failed" in restore.output


def test_fmt_utc_seconds():
    assert _fmt_utc_seconds(0) == "1970-01-01 00:00:00"
