from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace


from openpine.batch import runner
from openpine.batch.tv_corpus import ChartExport, ExportEntry
from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe


def _chart(tmp_path: Path, name: str = "chart.csv") -> ChartExport:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_text(
        "time,open,high,low,close,volume\n"
        "1970-01-01T00:01:00Z,1,2,0,1.5,10\n"
        "1970-01-01T00:02:00Z,2,3,1,2.5,20\n",
        encoding="utf-8",
    )
    return ChartExport(timeframe="1m", path=path, bars=2, start_ms=60_000, end_ms=120_000)


def _entry(tmp_path: Path, kind: str = "strategy", export_id: int = 9) -> ExportEntry:
    tmp_path.mkdir(parents=True, exist_ok=True)
    pine = tmp_path / f"{kind}.pine"
    pine.write_text(f'{kind}("x")\n', encoding="utf-8")
    return ExportEntry(
        export_id=export_id,
        folder=f"{export_id:03d}_{kind}",
        kind=kind,
        source_group="grp",
        root=tmp_path / f"entry_{export_id}",
        pine_path=pine,
        charts=(_chart(tmp_path, f"chart_{export_id}.csv"),),
    )


def _args(tmp_path: Path, **overrides) -> argparse.Namespace:
    values = dict(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        root=tmp_path,
        manifest=tmp_path / "manifest.csv",
        calculation_from="60000",
        calculation_to="180000",
        provider_only_bars=True,
        timeframe=None,
        progress_every=0,
        qty_step=0.001,
        qty_rounding_mode="truncate",
        skip_completed=False,
        phase="run",
        stop_on_error=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _bars() -> list[Bar]:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return [
        Bar(
            instrument=instrument,
            timeframe=tf,
            time=60_000,
            time_close=120_000,
            open=1,
            high=2,
            low=0,
            close=1.5,
            volume=10,
            closed=True,
        ),
        Bar(
            instrument=instrument,
            timeframe=tf,
            time=120_000,
            time_close=180_000,
            open=2,
            high=3,
            low=1,
            close=2.5,
            volume=20,
            closed=True,
        ),
    ]


def test_batch_runner_executes_indicator_and_strategy_paths(monkeypatch, tmp_path):
    data_meta = {
        "calculation_from": 60_000,
        "calculation_to": 180_000,
        "compare_from": 60_000,
        "compare_to": 180_000,
        "bars_total": 2,
    }
    monkeypatch.setattr(
        runner,
        "load_calculation_bars",
        lambda entry, chart, args, timings: (_bars(), dict(data_meta)),
    )
    monkeypatch.setattr(runner, "_infer_tv_bar_index_offset", lambda chart, bars: (0, None))

    pine_runtime_mod = types.ModuleType("backtest_engine.execution_backends.pine_runtime")

    class PineRuntimeBackend:
        def execute(self, generated_class, bars, **kwargs):
            assert kwargs["is_indicator"] is True
            return SimpleNamespace(plots=[(60_000, 0, 10.0, "plot"), (120_000, 1, 11.0, "plot")])

    pine_runtime_mod.PineRuntimeBackend = PineRuntimeBackend  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backtest_engine.execution_backends.pine_runtime", pine_runtime_mod)

    import openpine.runtime.engine as engine_mod
    import openpine.data.provider_adapter as provider_mod
    import openpine.artifacts as artifacts_mod
    import openpine.export as export_mod

    monkeypatch.setattr(engine_mod, "load_generated_class_from_artifact", lambda *a, **k: type("Indicator", (), {}))
    monkeypatch.setattr(provider_mod, "create_local_runtime_data_provider_adapter", lambda **kwargs: SimpleNamespace(_provider="provider"))

    indicator_entry = _entry(tmp_path, kind="indicator", export_id=10)
    indicator_out = tmp_path / "indicator_out"
    indicator_out.mkdir()
    status = runner.run_indicator(
        indicator_entry,
        SimpleNamespace(id="pine1"),
        "art1",
        indicator_entry.charts[0],
        indicator_out,
        _args(tmp_path),
    )
    assert status["status"] == "ok"
    assert status["plots_rows"] == 2
    assert (indicator_out / "plots.csv").exists()

    class ArtifactStore:
        def get_artifact(self, artifact_id, source_id):
            return {
                "compile_meta": {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {
                                "initial_capital": 1234.0,
                                "commission_type": "cash_per_order",
                                "commission_value": 1.0,
                                "pyramiding": 2,
                            }
                        }
                    }
                }
            }

    class BacktestRunConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class BacktestEngineAdapter:
        def run(self, strategy_class, bars, config, **kwargs):
            assert config.kwargs["commission_type"] == "fixed_per_order"
            assert getattr(strategy_class, "runtime_data_provider")._provider == "provider"
            return SimpleNamespace(
                status="ok",
                bars_processed=len(bars),
                raw_result=SimpleNamespace(trades=[], open_trades=[], plots=[]),
            )

    monkeypatch.setattr(artifacts_mod, "ArtifactStore", ArtifactStore)
    monkeypatch.setattr(engine_mod, "BacktestRunConfig", BacktestRunConfig)
    monkeypatch.setattr(engine_mod, "BacktestEngineAdapter", BacktestEngineAdapter)
    monkeypatch.setattr(engine_mod, "load_strategy_class_from_artifact", lambda *a, **k: type("Strategy", (), {}))
    monkeypatch.setattr(
        export_mod,
        "export_strategy_result",
        lambda **kwargs: SimpleNamespace(
            trades_rows=1,
            equity_rows=2,
            plots_rows=3,
            initial_equity_at_export_start=1000.0,
            outputs={"trades": "trades.csv", "equity": "equity.csv", "plots": "plots.csv"},
        ),
    )

    strategy_entry = _entry(tmp_path, kind="strategy", export_id=11)
    strategy_class = SimpleNamespace(
        id="pine1",
        active_artifact_id="art1",
    )
    strategy_status = runner.run_strategy(
        strategy_entry,
        strategy_class,
        "art1",
        strategy_entry.charts[0],
        tmp_path / "strategy_out",
        _args(tmp_path),
    )
    assert strategy_status["status"] == "ok"
    assert strategy_status["trades_rows"] == 1
    assert strategy_status["equity_rows"] == 2
    assert strategy_status["plots_rows"] == 3


def test_batch_runner_completion_selection_and_main_summary_paths(monkeypatch, tmp_path):
    entry = _entry(tmp_path, kind="strategy", export_id=12)
    args = _args(tmp_path, skip_completed=True, phase="run")

    status_path = entry.root / "openpine_outputs" / "openpine_batch_status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text('{"phase":"compile","status":"ok"}', encoding="utf-8")
    assert runner.completed_for_selection(entry, args) is False

    status_path.write_text('{"phase":"run","status":"ok","runs":[]}', encoding="utf-8")
    assert runner.completed_for_selection(entry, args) is False

    out_dir = entry.root / "openpine_outputs" / entry.charts[0].timeframe
    out_dir.mkdir(parents=True)
    for file_name in ("plots.csv", "trades.csv", "equity_curve.csv"):
        (out_dir / file_name).write_text("x", encoding="utf-8")
    run_meta = {
        "schema_version": runner.RUN_META_SCHEMA_VERSION,
        "compile_profile": runner.PRODUCTION_COMPILE_PROFILE,
        "run_id": "r",
        "batch_id": "b",
        "source_id": "pine",
        "strategy_or_indicator": "strategy",
        "calculation_window": {"from_ms": 1, "to_ms": 3},
        "export_window": {"from_ms": 1, "to_ms": 3},
        "library_revisions": {name: "rev" for name in runner.LIBRARY_NAMES},
    }
    (out_dir / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps({"schema_version": runner.RUN_META_SCHEMA_VERSION, "status": "ok"}),
        encoding="utf-8",
    )
    status_path.write_text(
        json.dumps(
            {
                "phase": "run",
                "status": "ok",
                "runs": [{"timeframe": entry.charts[0].timeframe, "status": "ok"}],
            }
        ),
        encoding="utf-8",
    )
    assert runner.completed_for_selection(entry, args) is True

    args.phase = "compile"
    status_path.write_text('{"phase":"compile","status":"compiled"}', encoding="utf-8")
    assert runner.completed_for_selection(entry, args) is True

    results = [
        {"id": 12, "kind": "strategy", "runs": [{"timeframe": "1s", "status": "ok", "bars": 2, "plots_rows": 3, "trades_rows": 1, "equity_rows": 2}]}
    ]
    tf_path = runner._write_timeframe_summary_csv(root=tmp_path, phase="run", batch_id="b", results=results)
    assert tf_path and tf_path.exists()
    payload = runner._build_batch_summary_payload(
        args=_args(tmp_path),
        batch_id="b",
        errors_path=tmp_path / "errors.jsonl",
        library_revisions={name: "rev" for name in runner.LIBRARY_NAMES},
        selected=[entry],
        entries=[entry],
        results=results,
        timeframe_summary=runner.summary_by_timeframe(results),
    )
    assert payload["summary_by_timeframe"]["1s"]["selected"] == 1

    selected = [entry, _entry(tmp_path, kind="indicator", export_id=13)]
    flags = {"skip_first": True}

    def fake_completed(current, current_args):
        return flags.pop("skip_first", False)

    def fake_run_entry(current, current_args, **kwargs):
        if current.export_id == 13:
            raise RuntimeError("boom")
        return {**runner.entry_summary(current), "status": "ok", "runs": []}

    monkeypatch.setattr(runner, "completed_for_selection", fake_completed)
    monkeypatch.setattr(runner, "run_entry", fake_run_entry)
    selected_args = _args(tmp_path, stop_on_error=True)
    selected_args.root = tmp_path / "selected_root"
    results = runner._run_selected_entries(
        args=selected_args,
        selected=selected,
        batch_id="batch",
        library_revisions={name: "rev" for name in runner.LIBRARY_NAMES},
        errors_path=tmp_path / "selected_errors.jsonl",
    )
    assert [r["status"] for r in results] == ["skipped_completed", "fatal_error"]
    assert (tmp_path / "selected_errors.jsonl").exists()

    main_root = tmp_path / "main_root"
    main_entry = _entry(main_root, kind="strategy", export_id=14)
    monkeypatch.setattr(runner, "load_manifest", lambda manifest, root: [main_entry])
    monkeypatch.setattr(runner, "filter_entries", lambda entries, **kwargs: entries)
    monkeypatch.setattr(runner, "_get_library_revisions", lambda: {name: "rev" for name in runner.LIBRARY_NAMES})
    monkeypatch.setattr(
        runner,
        "_run_selected_entries",
        lambda **kwargs: [
            {**runner.entry_summary(main_entry), "status": "ok", "runs": [{"timeframe": "1s", "status": "ok", "bars": 2}]}
        ],
    )
    code = runner.main([
        "--root",
        str(main_root),
        "--manifest",
        str(main_root / "manifest.csv"),
        "--phase",
        "run",
        "--summary-name",
        "summary.json",
    ])
    assert code == 0
    assert (main_root / "summary.json").exists()
