from __future__ import annotations

import pytest

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    InstrumentKey,
    parse_timeframe,
)

from openpine.adapters.bars import from_provider_bars, to_engine_bars, to_pinelib_bars
from openpine.runtime.engine import (
    BacktestArtifactError,
    BacktestEngineAdapter,
    load_generated_class_from_artifact,
)


def test_bar_adapters_preserve_canonical_window_semantics() -> None:
    timeframe = parse_timeframe("15m")
    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=timeframe,
        start_ms=1_700_000_000_000,
        end_ms=1_700_001_800_000,
        source="provider",
    )
    bar = Bar(
        instrument=query.instrument,
        timeframe=timeframe,
        time=1_700_000_000_000,
        time_close=1_700_000_899_999,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=None,
        closed=True,
    )
    series = BarSeries(
        query=query, bars=(bar,), coverage=from_provider_bars((bar,), query).coverage
    )

    engine_bar = BacktestEngineAdapter()._to_engine_bar(bar)
    engine_series = to_engine_bars(series)
    pine_bar = to_pinelib_bars(series)[0]

    assert engine_bar.volume is None
    assert engine_bar.time == bar.time
    assert engine_bar.time_close == bar.time_close
    assert engine_series.get_bar(0).time == bar.time
    assert engine_series.get_bar(0).time_close == bar.time_close
    assert engine_series.get_bar(0).volume is None
    assert pine_bar.time == bar.time
    assert pine_bar.time_close == bar.time_close
    assert pine_bar.volume == 0.0


def test_runtime_rejects_failed_compile_artifact(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "art_failed"
    artifact_dir.mkdir()
    (artifact_dir / "generated_strategy.py").write_text(
        "class GeneratedStrategy: pass\n"
    )

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "compile_meta": {"compile_status": "FAILED", "errors": ["boom"]},
            }

    import openpine.artifacts

    monkeypatch.setattr(openpine.artifacts, "ArtifactStore", Store)

    with pytest.raises(
        BacktestArtifactError, match="not a successful production compile"
    ):
        load_generated_class_from_artifact("pine_test", "art_failed")


def test_runtime_rejects_unsafe_compile_artifact(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "art_unsafe"
    artifact_dir.mkdir()
    (artifact_dir / "generated_strategy.py").write_text(
        "class GeneratedStrategy: pass\n"
    )

    class Store:
        def get_artifact(self, artifact_id: str, source_id: str) -> dict:
            return {
                "artifact_dir": str(artifact_dir),
                "compile_meta": {
                    "compile_status": "OK",
                    "unsafe": True,
                    "unsafe_reasons": ["implicit_pine_version_rewrite"],
                },
            }

    import openpine.artifacts

    monkeypatch.setattr(openpine.artifacts, "ArtifactStore", Store)

    with pytest.raises(BacktestArtifactError, match="marked unsafe"):
        load_generated_class_from_artifact("pine_test", "art_unsafe")
