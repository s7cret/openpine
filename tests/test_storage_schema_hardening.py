from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from openpine.cli.storage import storage as storage_group
from openpine.events.bus import EventBus
from openpine.events.types import Event, EventType
from openpine.gateway.routes.pine_sources import delete_source, delete_source_preview
from openpine.pine.source import PineSource
from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.db_health import schema_health
from openpine.storage.schema_compat import ensure_events_compat_schema, table_columns
from openpine.storage.schema_indexes import REQUIRED_INDEXES, required_index_names


def _migrated_storage(tmp_path: Path) -> SQLiteStorage:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def test_migrations_apply_index_profile_and_event_compat_columns(
    tmp_path: Path,
) -> None:
    with _migrated_storage(tmp_path) as storage:
        columns = table_columns(storage, "events")
        assert {
            "payload_json",
            "payload",
            "created_at",
            "timestamp_ms",
            "durable",
        }.issubset(columns)
        indexes = {
            row[0]
            for row in storage.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert set(required_index_names()).issubset(indexes)
        metadata = dict(
            storage.execute(
                "SELECT key, value FROM openpine_schema_metadata"
            ).fetchall()
        )
        assert metadata["schema_contract"] == "openpine.sqlite.v4"
        assert metadata["schema_index_profile"] == "openpine.sqlite.v4.indexes.011"
        report = schema_health(storage)
        assert report.ok
        assert report.index_count >= len(REQUIRED_INDEXES)
        assert report.missing_indexes == ()
        assert report.event_schema_compatible


def test_eventbus_writes_both_legacy_and_normalized_event_columns(
    tmp_path: Path,
) -> None:
    with _migrated_storage(tmp_path) as storage:
        event_bus = EventBus(storage)
        event = Event.create(
            EventType.CANDLE_CLOSED, {"symbol": "BTCUSDT"}, durable=True
        )
        event_bus.emit(event)
        row = storage.execute(
            "SELECT payload_json, payload, created_at, timestamp_ms, durable FROM events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == row[1]
        assert row[2] == row[3] == event.timestamp_ms
        assert row[4] == 1
        assert event_bus.get_events(EventType.CANDLE_CLOSED, limit=1)[0].payload == {
            "symbol": "BTCUSDT"
        }


def test_events_compat_normalizes_pre_v4_eventbus_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, durable INTEGER NOT NULL DEFAULT 1)"
    )
    raw.execute(
        "INSERT INTO events(event_id, event_type, payload, timestamp_ms, durable) VALUES ('e1', 'CandleClosed', '{\"x\": 1}', 123, 1)"
    )
    raw.commit()
    raw.close()

    with SQLiteStorage(path) as storage:
        ensure_events_compat_schema(storage)
        columns = table_columns(storage, "events")
        assert {"payload_json", "created_at", "status"}.issubset(columns)
        row = storage.execute(
            "SELECT payload_json, payload, created_at, timestamp_ms, status FROM events WHERE event_id='e1'"
        ).fetchone()
        assert row == ('{"x": 1}', '{"x": 1}', 123, 123, "NEW")


def test_migration_runner_handles_preexisting_eventbus_table_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "preexisting.sqlite"
    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, durable INTEGER NOT NULL DEFAULT 1)"
    )
    raw.commit()
    raw.close()

    with SQLiteStorage(path) as storage:
        applied = MigrationRunner().run_migrations(storage)
        assert "performance_indexes" in applied
        assert schema_health(storage).ok


def test_storage_health_cli_reports_index_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "health.sqlite"
    result = CliRunner().invoke(storage_group, ["health", "--path", str(db_path)])
    assert result.exit_code == 0
    assert "Storage health" in result.output
    assert "schema_contract: openpine.sqlite.v4" in result.output
    assert "required indexes: ok" in result.output
    assert "events schema: compatible" in result.output


def test_schema_health_reports_missing_index_and_event_columns(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE schema_migrations(version TEXT, name TEXT)")
    raw.execute(
        "CREATE TABLE events(event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL)"
    )
    raw.commit()
    raw.close()

    with SQLiteStorage(path) as storage:
        report = schema_health(storage)
        assert not report.ok
        assert report.pending_versions
        assert not report.event_schema_compatible
        assert "idx_pine_sources_hash" in report.missing_indexes


def test_candle_manifest_range_query_uses_active_range_index(tmp_path: Path) -> None:
    with _migrated_storage(tmp_path) as storage:
        plan = storage.execute(
            "EXPLAIN QUERY PLAN SELECT partition_path FROM candle_manifests WHERE exchange=? AND market_type=? AND symbol=? AND price_type=? AND timeframe=? AND is_active=1 AND min_open_time <= ? AND max_open_time >= ?",
            ("BINANCE", "spot", "BTCUSDT", "trade", "1m", 200, 100),
        ).fetchall()
        assert "idx_candle_manifests_active_range" in " ".join(str(row) for row in plan)


class _FakeRegistry:
    def __init__(self, source: PineSource) -> None:
        self.source = source
        self.removed: list[str] = []

    def get_source(self, source_id: str) -> PineSource:
        assert source_id in {self.source.id, self.source.name}
        return self.source

    def remove_source(self, source_id: str) -> None:
        self.removed.append(source_id)


class _FakeArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _source_dir(self, source_id: str) -> Path:
        return self.root / source_id


def test_pine_source_delete_uses_actual_artifact_schema_columns(tmp_path: Path) -> None:
    with _migrated_storage(tmp_path) as storage:
        now = 1
        source = PineSource(
            id="src1", name="demo", source_text="//@version=6\nindicator('x')"
        )
        storage.execute(
            "INSERT INTO pine_sources(id, name, source_text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (source.id, source.name, source.source_text, now, now),
        )
        storage.execute(
            "INSERT INTO compile_artifacts(id, source_id, params_hash, artifact_path, compile_meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("compile1", source.id, "h", "artifact.py", "{}", now),
        )
        storage.execute(
            "INSERT INTO pine_artifacts(artifact_id, pine_id, source_hash, pine2ast_version, ast2python_version, pinelib_contract_version, compile_options_hash, ast_path, generated_py_path, compile_meta_path, compile_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "artifact1",
                source.id,
                "sh",
                "4.0.0",
                "4.0.0",
                "runtime_contract_v1_4",
                "opts",
                "ast.json",
                "gen.py",
                "meta.json",
                "ok",
                now,
            ),
        )
        storage.commit()
        artifact_dir = tmp_path / "artifacts" / source.id
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "generated.py").write_text("# x", encoding="utf-8")
        state = SimpleNamespace(
            storage=storage,
            pine_registry=_FakeRegistry(source),
            artifact_store=_FakeArtifactStore(tmp_path / "artifacts"),
        )

        preview = asyncio.run(delete_source_preview(source.id, state=state))
        assert preview["resources"]["compile_artifacts"] == 1
        assert preview["resources"]["pine_artifacts"] == 1
        assert preview["resources"]["artifact_files"] == 1

        asyncio.run(delete_source(source.id, state=state))
        assert state.pine_registry.removed == [source.id]
        assert (
            storage.execute("SELECT COUNT(*) FROM compile_artifacts").fetchone()[0] == 0
        )
        assert storage.execute("SELECT COUNT(*) FROM pine_artifacts").fetchone()[0] == 0
        assert not artifact_dir.exists()
