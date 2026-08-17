from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from openpine.batch.runner import run_strategy
from openpine.batch.tv_corpus import ChartExport, ExportEntry


def _chart(tmp_path: Path) -> ChartExport:
    path = tmp_path / "chart.csv"
    path.write_text("time,open,high,low,close,volume\n", encoding="utf-8")
    return ChartExport(
        timeframe="1m",
        path=path,
        bars=1,
        start_ms=60_000,
        end_ms=120_000,
    )


def _entry(tmp_path: Path) -> ExportEntry:
    pine = tmp_path / "strat.pine"
    pine.write_text("strategy()\n", encoding="utf-8")
    return ExportEntry(
        export_id=1,
        folder="001_strategy",
        kind="strategy",
        source_group="grp",
        root=tmp_path / "entry",
        pine_path=pine,
        charts=(_chart(tmp_path),),
    )


def _args(**overrides) -> argparse.Namespace:
    values = dict(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        qty_step=0.001,
        qty_rounding_mode="truncate",
        semantic_profile="strict_5x",
        htf_bars=None,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_batch_strategy_forwards_confirmed_htf_bars(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]

    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: ([], {"calculation_from": 0, "calculation_to": 60_000}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: (
            b"src"
            if name == "load_artifact_sec"
            else {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {}}}}}
        ),
    )

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(
                status="ok",
                bars_processed=0,
                raw_result=SimpleNamespace(),
            )

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(
        "openpine.export.export_strategy_result",
        lambda **kwargs: SimpleNamespace(
            trades_rows=0,
            equity_rows=0,
            plots_rows=0,
            initial_equity_at_export_start=0,
            outputs={},
        ),
    )

    status = run_strategy(
        _entry(tmp_path),
        SimpleNamespace(id="pine"),
        "art",
        _entry(tmp_path).charts[0],
        tmp_path / "out",
        _args(htf_bars=htf_bars),
    )
    assert status["status"] == "ok"
    assert captured["htf_bars"] == htf_bars


def test_batch_strategy_stamps_confirmed_provider_htf_bars(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    bars = [
        SimpleNamespace(
            time=0,
            time_close=59_999,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=3,
        )
    ]

    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: (bars, {"calculation_from": 0, "calculation_to": 60_000}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: (
            b"src"
            if name == "load_artifact_sec"
            else {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {}}}}}
        ),
    )

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(
                status="ok",
                bars_processed=1,
                raw_result=SimpleNamespace(),
            )

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(
        "openpine.export.export_strategy_result",
        lambda **kwargs: SimpleNamespace(
            trades_rows=0,
            equity_rows=0,
            plots_rows=0,
            initial_equity_at_export_start=0,
            outputs={},
        ),
    )

    status = run_strategy(
        _entry(tmp_path),
        SimpleNamespace(id="pine"),
        "art",
        _entry(tmp_path).charts[0],
        tmp_path / "out",
        _args(),
    )
    assert status["status"] == "ok"
    assert captured["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "time": 0,
            "time_close": 59_999,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ]


def test_batch_strategy_does_not_invent_time_close(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: (
            [SimpleNamespace(time=1, open=1, high=1, low=1, close=1, volume=1)],
            {"calculation_from": 0, "calculation_to": 60_000},
        ),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: (
            b"src"
            if name == "load_artifact_sec"
            else {"compile_meta": {"translation_metadata": {"declaration": {"arguments": {}}}}}
        ),
    )

    class Adapter:
        def run_isolated(self, source, bars, config, **kwargs):
            captured["htf_bars"] = kwargs.get("htf_bars")
            return SimpleNamespace(
                status="ok",
                bars_processed=1,
                raw_result=SimpleNamespace(),
            )

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    monkeypatch.setattr(
        "openpine.export.export_strategy_result",
        lambda **kwargs: SimpleNamespace(
            trades_rows=0,
            equity_rows=0,
            plots_rows=0,
            initial_equity_at_export_start=0,
            outputs={},
        ),
    )

    status = run_strategy(
        _entry(tmp_path),
        SimpleNamespace(id="pine"),
        "art",
        _entry(tmp_path).charts[0],
        tmp_path / "out",
        _args(),
    )
    assert status["status"] == "ok"
    assert captured["htf_bars"] is None
