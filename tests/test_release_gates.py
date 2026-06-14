from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from openpine import __version__
from openpine.distribution import build_zip, distribution_manifest, source_files
from openpine.quality import architecture_report, duplicate_report
from openpine.release import release_report
from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.db_health import schema_health


def _clean_release_artifacts(root: Path) -> None:
    for name in (".marketdata-cache", ".openpine", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
        shutil.rmtree(root / name, ignore_errors=True)
    for file in root.glob("*.zip"):
        file.unlink(missing_ok=True)


def test_quality_reports_current_package_budget() -> None:
    root = Path(__file__).resolve().parents[1] / "openpine"
    arch = architecture_report(root, max_lines=4000)
    dup = duplicate_report(root)

    assert arch.oversized_count == 0
    assert dup.duplicate_group_count == 0


def test_distribution_manifest_excludes_local_runtime_artifacts(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("venv\n", encoding="utf-8")
    (tmp_path / ".openpine" / "data" / "cache").mkdir(parents=True)
    (tmp_path / ".openpine" / "openpine.sqlite").write_text("sqlite\n", encoding="utf-8")
    (tmp_path / ".marketdata-cache").mkdir()
    (tmp_path / ".marketdata-cache" / "cache.sqlite").write_text("cache\n", encoding="utf-8")
    (tmp_path / "openpine.db").write_text("sqlite\n", encoding="utf-8")
    (tmp_path / "data.parquet").write_text("parquet\n", encoding="utf-8")
    (tmp_path / "openpine.egg-info").mkdir()
    (tmp_path / "openpine.egg-info" / "PKG-INFO").write_text("metadata\n", encoding="utf-8")
    (tmp_path / "openpine-ui" / "dist" / "assets").mkdir(parents=True)
    (tmp_path / "openpine-ui" / "dist" / "assets" / "app.js").write_text(
        "bundle\n", encoding="utf-8"
    )
    (
        tmp_path / "openpine-ui" / "node_modules" / "vue" / "dist"
    ).mkdir(parents=True)
    (
        tmp_path
        / "openpine-ui"
        / "node_modules"
        / "vue"
        / "dist"
        / "vue.js"
    ).write_text("vue bundle\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")

    manifest = distribution_manifest(tmp_path)
    output = tmp_path / "openpine.zip"
    digest = build_zip(tmp_path, output, archive_root="openpine-test")

    assert manifest.hygiene_errors == ()
    assert manifest.file_count == 1
    assert manifest.byte_count == len("print('ok')\n")
    assert len(digest) == 64


def test_distribution_zip_excludes_existing_and_in_progress_archives(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "old-release.zip").write_text("old archive\n", encoding="utf-8")
    output = tmp_path / "new-release.zip"

    digest = build_zip(tmp_path, output, archive_root="openpine-test")
    manifest = distribution_manifest(tmp_path)
    source_names = {path.name for path in source_files(tmp_path)}

    with zipfile.ZipFile(output) as archive:
        archived_names = set(archive.namelist())

    assert len(digest) == 64
    assert source_names == {"keep.py"}
    assert manifest.hygiene_errors == ("new-release.zip", "old-release.zip")
    assert "openpine-test/keep.py" in archived_names
    assert "openpine-test/old-release.zip" not in archived_names
    assert "openpine-test/new-release.zip" not in archived_names


def test_distribution_manifest_and_zip_are_deterministic(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    _clean_release_artifacts(root)
    manifest = distribution_manifest(root)
    output = tmp_path / "openpine.zip"
    digest_a = build_zip(root, output, archive_root="openpine-test")
    digest_b = build_zip(root, output, archive_root="openpine-test")

    assert manifest.file_count > 0
    assert manifest.byte_count > 0
    assert digest_a == digest_b
    assert output.stat().st_size > 0


def test_release_report_is_green_for_4_0() -> None:
    root = Path(__file__).resolve().parents[1]
    _clean_release_artifacts(root)
    report = release_report(root)

    assert __version__ == "4.0.0"
    assert report.ok, report.errors
    assert report.checks["latest_migration"] >= 10


def test_schema_health_tracks_metadata_migration(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    try:
        applied = MigrationRunner().run_migrations(storage)
        health = schema_health(storage)
        rows = storage.execute(
            "SELECT key, value FROM openpine_schema_metadata ORDER BY key"
        ).fetchall()
    finally:
        storage.close()

    assert "schema_metadata" in applied
    assert health.ok
    assert dict(rows)["schema_contract"] == "openpine.sqlite.v4"


def test_release_cli_writes_json(tmp_path: Path) -> None:
    from openpine.release import main

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "release.json"

    _clean_release_artifacts(root)
    assert main(["--root", str(root), "--json", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ok"] is True
