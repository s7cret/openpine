from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpine_contracts import AdmitError

from openpine.batch.runner import _build_arg_parser, run_indicator
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
    pine = tmp_path / "ind.pine"
    pine.write_text("indicator()\n", encoding="utf-8")
    return ExportEntry(
        export_id=1,
        folder="001_indicator",
        kind="indicator",
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
        calculation_from="60000",
        calculation_to="180000",
        provider_only_bars=True,
        tv_authoritative_bars=False,
        semantic_profile=None,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_batch_parser_does_not_default_semantic_profile() -> None:
    args = _build_arg_parser().parse_args(["--phase", "run"])
    assert hasattr(args, "semantic_profile")
    assert args.semantic_profile is None


def test_batch_indicator_requires_semantic_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: ([], {}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: b"src",
    )
    monkeypatch.setattr(
        "openpine.batch.runner._infer_tv_bar_index_offset",
        lambda chart, bars: (0, None),
    )

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    monkeypatch.setattr(
        "openpine.export.export_plot_records",
        lambda *a, **k: 0,
    )
    with pytest.raises(AdmitError, match="semantic profile"):
        run_indicator(
            _entry(tmp_path),
            SimpleNamespace(id="pine"),
            "art",
            _entry(tmp_path).charts[0],
            tmp_path / "out",
            _args(),
        )
    assert "semantic_profile" not in captured


def test_batch_indicator_forwards_admitted_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: ([], {}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: b"src",
    )
    monkeypatch.setattr(
        "openpine.batch.runner._infer_tv_bar_index_offset",
        lambda chart, bars: (0, None),
    )

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["semantic_profile"] = semantic_profile
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    monkeypatch.setattr(
        "openpine.export.export_plot_records",
        lambda *a, **k: 0,
    )
    status = run_indicator(
        _entry(tmp_path),
        SimpleNamespace(id="pine"),
        "art",
        _entry(tmp_path).charts[0],
        tmp_path / "out",
        _args(semantic_profile="legacy_4x"),
    )
    assert status["status"] == "ok"
    assert captured["semantic_profile"] == "legacy_4x"


def test_batch_indicator_rejects_unknown_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "openpine.batch.runner.load_calculation_bars",
        lambda *a, **k: ([], {}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: b"src",
    )
    monkeypatch.setattr(
        "openpine.batch.runner._infer_tv_bar_index_offset",
        lambda chart, bars: (0, None),
    )
    with pytest.raises(AdmitError, match="unknown semantic profile"):
        run_indicator(
            _entry(tmp_path),
            SimpleNamespace(id="pine"),
            "art",
            _entry(tmp_path).charts[0],
            tmp_path / "out",
            _args(semantic_profile="nope"),
        )


def test_batch_indicator_forwards_confirmed_htf_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        lambda *a, **k: ([], {}),
    )
    monkeypatch.setattr(
        "openpine.batch.runner.timed_call",
        lambda timings, name, fn, *a, **k: b"src",
    )
    monkeypatch.setattr(
        "openpine.batch.runner._infer_tv_bar_index_offset",
        lambda chart, bars: (0, None),
    )

    def fake_run(source, bars, *, semantic_profile=None, htf_bars=None):
        captured["htf_bars"] = htf_bars
        return SimpleNamespace(plots=())

    monkeypatch.setattr(
        "openpine.runtime.isolated_run.run_isolated_indicator", fake_run
    )
    monkeypatch.setattr(
        "openpine.export.export_plot_records",
        lambda *a, **k: 0,
    )
    status = run_indicator(
        _entry(tmp_path),
        SimpleNamespace(id="pine"),
        "art",
        _entry(tmp_path).charts[0],
        tmp_path / "out",
        _args(semantic_profile="legacy_4x", htf_bars=htf_bars),
    )
    assert status["status"] == "ok"
    assert captured["htf_bars"] == htf_bars
