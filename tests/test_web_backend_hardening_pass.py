from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.artifacts.store import ArtifactStore
from openpine.gateway.config import GatewayConfig
from openpine.gateway.routes import accounts_data, backtest, events, pine_ops, pine_sources, strategies
from openpine.gateway.schemas import CompareTvRequest
from openpine.gateway.server import create_app


class _AcceptingPineRegistry:
    def get_source(self, source_id: str):
        return SimpleNamespace(source_id=source_id)


class _PoisonedArtifactStore:
    def __init__(self, root: Path, poisoned_dir: Path) -> None:
        self._root = root
        self.poisoned_dir = poisoned_dir

    def get_artifact(self, artifact_id: str, source_id: str):  # noqa: ARG002
        return {"artifact_dir": str(self.poisoned_dir), "compile_meta": {"ok": True}}


class _CallableArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _artifact_dir(self, source_id: str, artifact_id: str) -> Path:
        return self._root / source_id / artifact_id

    def get_artifact(self, artifact_id: str, source_id: str):  # noqa: ARG002
        return {"compile_meta": {"compile_status": "OK"}}


class _MissingArtifactDirStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def get_artifact(self, artifact_id: str, source_id: str):  # noqa: ARG002
        return {"compile_meta": {"compile_status": "OK"}}


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]]):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _ManifestStorage:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.deleted: list[tuple[object, ...]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()):
        if sql.lstrip().upper().startswith("SELECT"):
            return _Cursor(self.rows)
        if "DELETE FROM candle_manifests" in sql:
            self.deleted.append(params)
        return _Cursor([])

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_pine_artifact_listing_rejects_source_id_path_escape(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    outside = tmp_path / "outside" / "artifact-from-outside"
    outside.mkdir(parents=True)
    (outside / "compile_meta.json").write_text(
        '{"compile_status":"OK"}', encoding="utf-8"
    )
    state = SimpleNamespace(
        pine_registry=_AcceptingPineRegistry(),
        artifact_store=store,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(pine_ops.list_artifacts("../outside", state))

    assert exc.value.status_code == 400


def test_pine_artifact_listing_skips_corrupt_compile_meta(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    source_dir = store._root / "pine-1"
    bad = source_dir / "bad-artifact"
    good = source_dir / "good-artifact"
    bad.mkdir(parents=True)
    good.mkdir(parents=True)
    (bad / "compile_meta.json").write_text("{not-json", encoding="utf-8")
    (good / "compile_meta.json").write_text(
        '{"compile_status":"OK"}', encoding="utf-8"
    )
    state = SimpleNamespace(
        pine_registry=_AcceptingPineRegistry(),
        artifact_store=store,
    )

    listed = asyncio.run(pine_ops.list_artifacts("pine-1", state))

    assert [item["artifact_id"] for item in listed] == ["good-artifact"]


@pytest.mark.asyncio
async def test_pine_artifact_inspect_rejects_metadata_artifact_dir_outside_root(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside-artifact"
    outside.mkdir()
    (outside / "diagnostics.log").write_text("outside secret", encoding="utf-8")
    state = SimpleNamespace(
        artifact_store=_PoisonedArtifactStore(artifact_root, outside),
    )

    with pytest.raises(HTTPException) as exc:
        await pine_ops.inspect_artifact("pine-1", "art-1", state)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_pine_artifact_inspect_uses_store_artifact_dir_helper(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_dir = artifact_root / "pine-1" / "art-1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "generated_strategy.py").write_text("line1\nline2\n", encoding="utf-8")
    state = SimpleNamespace(artifact_store=_CallableArtifactStore(artifact_root))

    inspected = await pine_ops.inspect_artifact("pine-1", "art-1", state)

    assert inspected["generated_python_lines"] == 2


@pytest.mark.asyncio
async def test_pine_artifact_inspect_rejects_missing_artifact_dir_metadata(
    tmp_path: Path,
) -> None:
    state = SimpleNamespace(
        artifact_store=_MissingArtifactDirStore(tmp_path / "artifacts"),
    )

    with pytest.raises(HTTPException) as exc:
        await pine_ops.inspect_artifact("pine-1", "art-1", state)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_strategy_compare_tv_rejects_paths_outside_gateway_roots(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output_root = workspace / "outputs"
    data_dir = workspace / "data"
    output_root.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    allowed_op = output_root / "op.csv"
    allowed_op.write_text("time,plot\n1,2.0\n", encoding="utf-8")
    outside_tv = tmp_path / "outside.csv"
    outside_tv.write_text("time,plot\n1,2.0\n", encoding="utf-8")

    state = SimpleNamespace(
        config=SimpleNamespace(
            workspace_root=workspace,
            output_root=output_root,
            data_dir=data_dir,
        )
    )
    req = CompareTvRequest(
        openpine_plots_path=str(allowed_op),
        tv_chart_path=str(outside_tv),
    )

    with pytest.raises(HTTPException) as exc:
        await strategies.strategy_compare_tv("s1", req, state)

    assert exc.value.status_code == 400


def test_gateway_default_cors_does_not_use_wildcard_with_credentials() -> None:
    app = create_app(GatewayConfig())
    cors = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )

    assert cors.kwargs["allow_credentials"] is True
    assert cors.kwargs["allow_origins"] != ["*"]


def test_delete_candle_manifest_does_not_move_partition_outside_candle_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    data_dir = workspace / "data"
    candle_root = data_dir / "candles"
    candle_root.mkdir(parents=True)
    outside = tmp_path / "outside.parquet"
    outside.write_text("do-not-move", encoding="utf-8")
    storage = _ManifestStorage([("manifest-1", str(outside))])
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=data_dir),
        storage=storage,
    )

    deleted = accounts_data._delete_candle_manifest_series(
        state,
        {
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
            "timeframe": "1m",
        },
    )

    assert deleted == 1
    assert storage.deleted == [("manifest-1",)]
    assert outside.exists()


def test_delete_candle_manifest_moves_partition_inside_candle_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    part = data_dir / "candles" / "partition.parquet"
    part.parent.mkdir(parents=True)
    part.write_text("move-me", encoding="utf-8")
    storage = _ManifestStorage([("manifest-1", str(part))])
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=data_dir),
        storage=storage,
    )
    monkeypatch.chdir(tmp_path)

    deleted = accounts_data._delete_candle_manifest_series(
        state,
        {
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTCUSDT",
            "price_type": "trade",
            "timeframe": "1m",
        },
    )

    assert deleted == 1
    assert storage.deleted == [("manifest-1",)]
    assert not part.exists()
    assert list((tmp_path / ".openpine" / "trash").rglob("partition.parquet"))


def test_delete_marketdata_segment_does_not_move_path_outside_store_root(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "cache"
    marketdata_root = cache_root / "marketdata"
    outside = tmp_path / "outside-segment" / "timeframe=1m"
    outside.mkdir(parents=True)
    (outside / "part.parquet").write_text("do-not-move", encoding="utf-8")
    (
        marketdata_root
        / "v1"
        / "exchange=binance"
        / "market=spot"
        / "symbol=BTCUSDT"
        / "source=.."
    ).mkdir(parents=True)
    state = SimpleNamespace(
        config=SimpleNamespace(
            data_cache_root=cache_root,
            data_dir=tmp_path / "data",
        )
    )

    deleted = accounts_data._delete_marketdata_segment_series(
        state,
        {
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "source_kinds": ["../../../../../../../../outside-segment"],
        },
    )

    assert deleted == 0
    assert outside.exists()
    assert (outside / "part.parquet").exists()
    assert not (marketdata_root / "index.sqlite").exists()


class _BacktestStore:
    def __init__(self, artifact_path: Path, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.artifact_path = artifact_path

    def get_run(self, run_id: str):
        return SimpleNamespace(run_id=run_id, strategy_id="s1")

    def get_metrics(self, run_id: str):
        return {}

    def list_trades(self, run_id: str):
        return []

    def list_artifacts(self, run_id: str):
        return [SimpleNamespace(artifact_type="report_md", path=str(self.artifact_path))]


@pytest.mark.asyncio
async def test_backtest_report_rejects_artifact_path_outside_store_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside report", encoding="utf-8")
    store_root = tmp_path / "data" / "backtests"
    store_root.mkdir(parents=True)
    state = SimpleNamespace(backtest_store=_BacktestStore(outside, store_root))

    with pytest.raises(HTTPException) as exc:
        await backtest.get_run_report("run-1", state)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_backtest_export_does_not_leak_server_artifact_paths(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "data" / "backtests" / "run-1" / "report.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("report", encoding="utf-8")
    state = SimpleNamespace(
        backtest_store=_BacktestStore(artifact, tmp_path / "data" / "backtests")
    )

    exported = await backtest.export_run("run-1", state)

    assert exported["artifacts"] == [{"type": "report_md", "filename": "report.md"}]
    assert str(tmp_path) not in repr(exported)


@pytest.mark.asyncio
async def test_backtest_export_omits_filename_for_unsafe_artifact_path(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("report", encoding="utf-8")
    state = SimpleNamespace(
        backtest_store=_BacktestStore(outside, tmp_path / "data" / "backtests")
    )

    exported = await backtest.export_run("run-1", state)

    assert exported["artifacts"] == [{"type": "report_md"}]
    assert str(outside) not in repr(exported)


def test_gateway_file_roots_deduplicates_identical_roots(tmp_path: Path) -> None:
    state = SimpleNamespace(
        config=SimpleNamespace(
            workspace_root=tmp_path,
            output_root=tmp_path,
            data_dir=tmp_path / "data",
        )
    )

    assert strategies._gateway_file_roots(state) == (
        tmp_path.resolve(),
        (tmp_path / "data").resolve(),
    )


@pytest.mark.asyncio
async def test_strategy_compare_tv_reports_csv_parse_errors_inside_allowed_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    output_root = workspace / "outputs"
    data_dir = workspace / "data"
    output_root.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    good = output_root / "op.csv"
    bad = output_root / "bad.csv"
    good.write_text("time,plot\n1,2.0\n", encoding="utf-8")
    bad.write_bytes(b"\xff\xfe\x00")
    state = SimpleNamespace(
        config=SimpleNamespace(
            workspace_root=workspace,
            output_root=output_root,
            data_dir=data_dir,
        )
    )

    with pytest.raises(HTTPException) as exc:
        await strategies.strategy_compare_tv(
            "s1",
            CompareTvRequest(openpine_plots_path=str(good), tv_chart_path=str(bad)),
            state,
        )

    assert exc.value.status_code == 400
    assert "CSV parse error" in str(exc.value.detail)


class _SourceRegistry:
    def __init__(self, source_id: str = "pine-1") -> None:
        self.source = SimpleNamespace(
            id=source_id,
            name="demo",
            source_text="//@version=6\nstrategy('x')",
            source_hash="h",
            version="1.0.0",
            source_type="strategy",
            active_artifact_id=None,
            created_at=1,
            updated_at=1,
        )
        self.removed: list[str] = []

    def get_source(self, source_id: str):
        if source_id != self.source.id:
            raise KeyError(source_id)
        return self.source

    def remove_source(self, source_id: str) -> None:
        self.removed.append(source_id)


class _FailingArtifactRowStorage:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ARG002
        if "DELETE FROM compile_artifacts" in sql:
            raise RuntimeError("artifact rows locked")
        return _Cursor([])

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FailingArtifactRowStorageWithoutRollback:
    def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ARG002
        if "DELETE FROM compile_artifacts" in sql:
            raise RuntimeError("artifact rows locked")
        return _Cursor([])

    def commit(self) -> None:
        raise AssertionError("commit should not run after failed cleanup")


@pytest.mark.asyncio
async def test_delete_pine_source_fails_visible_when_artifact_row_cleanup_fails(
    tmp_path: Path,
) -> None:
    registry = _SourceRegistry()
    artifact_root = tmp_path / "artifacts"
    artifact_dir = artifact_root / registry.source.id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "artifact.txt").write_text("keep", encoding="utf-8")
    storage = _FailingArtifactRowStorage()
    state = SimpleNamespace(
        pine_registry=registry,
        storage=storage,
        artifact_store=ArtifactStore(root=artifact_root),
    )

    with pytest.raises(HTTPException) as exc:
        await pine_sources.delete_source(registry.source.id, state)

    assert exc.value.status_code == 500
    assert registry.removed == []
    assert storage.rollbacks == 1
    assert artifact_dir.exists()


@pytest.mark.asyncio
async def test_delete_pine_source_fails_visible_without_storage_rollback(
    tmp_path: Path,
) -> None:
    registry = _SourceRegistry()
    artifact_root = tmp_path / "artifacts"
    state = SimpleNamespace(
        pine_registry=registry,
        storage=_FailingArtifactRowStorageWithoutRollback(),
        artifact_store=ArtifactStore(root=artifact_root),
    )

    with pytest.raises(HTTPException) as exc:
        await pine_sources.delete_source(registry.source.id, state)

    assert exc.value.status_code == 500
    assert registry.removed == []


class _EventStorage:
    def execute(self, sql: str, params: tuple[object, ...] = ()):  # noqa: ARG002
        compact = " ".join(sql.lower().split())
        if "pragma table_info(events)" in compact:
            return _Cursor(
                [
                    (0, "event_id"),
                    (1, "event_type"),
                    (2, "payload"),
                    (3, "timestamp_ms"),
                ]
            )
        return _Cursor([("evt-1", "bad.payload", "{not-json", 123456)])


def test_event_payload_decoder_handles_empty_and_non_dict_payloads() -> None:
    assert events._decode_event_payload("", "evt-empty") == {}
    assert events._decode_event_payload('["not", "a", "dict"]', "evt-list") == {}


@pytest.mark.asyncio
async def test_events_list_tolerates_corrupt_payload_json() -> None:
    state = SimpleNamespace(storage=_EventStorage())

    listed = await events.list_events(state=state)

    assert len(listed) == 1
    assert listed[0].event_id == "evt-1"
    assert listed[0].payload == {}
