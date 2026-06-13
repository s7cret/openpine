"""Storage backup/restore/verify for OpenPine. Section 11.6 TZ v3."""

from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path
from typing import Any

from openpine.config import OpenPineConfig


def backup_openpine(out_path: Path, config: OpenPineConfig | None = None) -> list[str]:
    """Create backup archive containing:
    - SQLite checkpoint
    - Parquet manifests
    - artifact store
    - state snapshots
    - config (without raw secrets)

    Returns list of backed up paths.
    """
    if config is None:
        config = OpenPineConfig.load()

    out_path = Path(out_path)
    backed_up: list[str] = []

    # Collect dirs to back up
    dirs_to_backup = [
        ("sqlite", config.sqlite_path),
        ("duckdb", config.duckdb_path),
        ("artifacts", Path(config.data_dir) / "artifacts"),
        ("manifests", Path(config.data_dir) / "manifests"),
        ("state", Path(config.data_dir) / "state"),
    ]

    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        for name, path in dirs_to_backup:
            path = Path(path)
            if not path.exists():
                continue

            if path.is_file():
                # Single file (sqlite, duckdb)
                # Checkpoint SQLite first
                if name == "sqlite" and path.suffix == ".sqlite":
                    _checkpoint_sqlite(path)
                tar.add(str(path), arcname=f"{name}/{path.name}")
                backed_up.append(str(path))
            else:
                # Directory tree
                for item in path.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(path.parent)
                        tar.add(str(item), arcname=f"{name}/{rel}")
                backed_up.append(str(path))

        # Backup config (without raw secrets)
        config_data = _config_safe_dict(config)
        config_manifest = {
            "config": config_data,
            "backup_version": "1.0",
            "schema": "openpine.backup.v1",
        }
        config_json = json.dumps(config_manifest, indent=2, default=str)
        info = tarfile.TarInfo(name="config/manifest.json")
        info.size = len(config_json.encode("utf-8"))
        tar.addfile(info, fileobj=_sio(config_json))
        backed_up.append(str(config.config_path()))

    return backed_up


def restore_openpine(backup_path: Path, target_dir: Path | None = None) -> None:
    """Restore from backup archive.

    Args:
        backup_path: Path to the backup .tar.gz file.
        target_dir: Optional target directory. Defaults to config data_dir.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    if target_dir is None:
        config = OpenPineConfig.load()
        target_dir = config.data_dir

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(backup_path, "r:gz") as tar:
        # Check manifest
        try:
            manifest_member = tar.getmember("config/manifest.json")
        except KeyError:
            raise ValueError("Invalid backup: missing manifest")

        with tar.extractfile(manifest_member) as f:
            manifest = json.loads(f.read().decode("utf-8"))

        schema = manifest.get("schema", "")
        if schema != "openpine.backup.v1":
            raise ValueError(f"Unknown backup schema: {schema}")

        # Extract all members
        tar.extractall(target_dir, filter="data")


def verify_openpine(config: OpenPineConfig | None = None) -> dict[str, bool]:
    """Verify storage integrity. Returns dict of check_name -> passed."""
    if config is None:
        config = OpenPineConfig.load()

    results: dict[str, bool] = {}

    # Check SQLite exists and is readable
    sqlite_path = Path(config.sqlite_path)
    if sqlite_path.exists():
        results["sqlite_exists"] = True
        try:
            conn = sqlite3.connect(str(sqlite_path), timeout=5)
            cursor = conn.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
            conn.close()
            results["sqlite_integrity"] = row is not None and row[0] == "ok"
        except Exception:
            results["sqlite_integrity"] = False
    else:
        results["sqlite_exists"] = False
        results["sqlite_integrity"] = False

    # Check DuckDB exists
    duckdb_path = Path(config.duckdb_path)
    results["duckdb_exists"] = duckdb_path.exists()

    # Check artifacts dir
    artifacts_dir = Path(config.data_dir) / "artifacts"
    results["artifacts_dir_exists"] = artifacts_dir.exists()

    # Check manifests dir
    manifests_dir = Path(config.data_dir) / "manifests"
    results["manifests_dir_exists"] = manifests_dir.exists()

    # Check state dir
    state_dir = Path(config.data_dir) / "state"
    results["state_dir_exists"] = state_dir.exists()

    # Check config file exists (at whatever path the config resolved to)
    config_path = config.config_path()
    results["config_exists"] = config_path.exists()

    return results


def _checkpoint_sqlite(path: Path) -> None:
    """Run SQLite WAL checkpoint on a path."""
    conn = None
    checkpoint_failed = False
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        row = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
        if row is not None:
            busy = int(row[0])
            if busy:
                raise RuntimeError(
                    f"SQLite checkpoint reported busy={busy}, log={row[1]}, checkpointed={row[2]}"
                )
    except Exception as exc:
        checkpoint_failed = True
        raise RuntimeError(f"SQLite checkpoint failed for {path}: {exc}") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:
                if not checkpoint_failed:
                    raise RuntimeError(
                        f"SQLite checkpoint close failed for {path}: {exc}"
                    ) from exc


def _config_safe_dict(config: OpenPineConfig) -> dict[str, Any]:
    """Return a safe dict for config (no raw secrets)."""
    d = config.model_dump()
    # Remove sensitive fields (token_refs, raw tokens)
    _redact_sensitive(d)
    return d


def _redact_sensitive(d: dict[str, Any]) -> None:
    """Recursively redact sensitive keys."""
    sensitive_keys = {"token", "secret", "password", "api_key", "apiSecret"}
    for key in list(d.keys()):
        low = key.lower()
        if any(s in low for s in sensitive_keys):
            d[key] = "<REDACTED>"
        elif isinstance(d[key], dict):
            _redact_sensitive(d[key])
        elif isinstance(d[key], list) and d[key] and isinstance(d[key][0], dict):
            for item in d[key]:
                if isinstance(item, dict):
                    _redact_sensitive(item)


def _sio(s: str):
    """StringIO stand-in using bytes io."""
    import io

    return io.BytesIO(s.encode("utf-8"))
