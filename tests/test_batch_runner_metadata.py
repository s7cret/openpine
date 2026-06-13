from __future__ import annotations

import argparse
import json
from pathlib import Path

from click.testing import CliRunner

from openpine.batch import runner as batch_runner
from openpine.batch.runner import (
    LIBRARY_NAMES,
    ChartExport,
    ExportEntry,
    _build_arg_parser,
    _build_batch_summary_payload,
    _build_run_meta,
    _build_run_summary,
    _build_strategy_run_config,
    _finish_entry_status,
    _infer_tv_bar_index_offset,
    _run_meta_valid,
    _write_timeframe_summary_csv,
    _write_progress,
    completed_for_selection,
    summary_by_timeframe,
    write_json,
)


def _entry(tmp_path: Path, kind: str = "strategy") -> ExportEntry:
    root = tmp_path / "exports" / "001_case"
    root.mkdir(parents=True)
    pine_path = root / "source.pine"
    pine_path.write_text("// test\n", encoding="utf-8")
    return ExportEntry(
        export_id=1,
        folder="001_case",
        kind=kind,
        source_group="fixture",
        root=root,
        pine_path=pine_path,
        charts=(
            ChartExport(
                timeframe="15m",
                path=root / "chart.csv",
                bars=10,
                start_ms=1_700_000_000_000,
                end_ms=1_700_008_100_000,
            ),
        ),
    )


def _args() -> argparse.Namespace:
    return argparse.Namespace(skip_completed=True, phase="run", timeframe=None)


def _revisions() -> dict[str, str]:
    return {name: "test-rev" for name in LIBRARY_NAMES}


def _write_completed_run(entry: ExportEntry) -> None:
    chart = entry.charts[0]
    out_dir = entry.root / "openpine_outputs" / chart.timeframe
    out_dir.mkdir(parents=True)
    run_info = {
        "status": "ok",
        "kind": entry.kind,
        "bars": 10,
        "plots_rows": 2,
        "trades_rows": 1,
        "equity_rows": 3,
        "timeframe": chart.timeframe,
        "data": {
            "calculation_from": 1_699_000_000_000,
            "calculation_to": 1_700_008_100_000,
            "compare_from": chart.start_ms,
            "compare_to": chart.end_ms,
        },
    }
    status = {
        "source_id": "pine-1",
        "artifact_id": "artifact-1",
        "phase": "run",
        "status": "ok",
        "runs": [{"timeframe": chart.timeframe, "status": "ok"}],
    }
    meta = _build_run_meta(
        entry=entry,
        chart=chart,
        status=status,
        run_info=run_info,
        batch_id="batch-1",
        library_revisions=_revisions(),
    )
    write_json(out_dir / "run_meta.json", meta)
    write_json(
        out_dir / "summary.json",
        _build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info=run_info),
    )
    for name in ("plots.csv", "trades.csv", "equity_curve.csv"):
        (out_dir / name).write_text("header\n", encoding="utf-8")
    write_json(entry.root / "openpine_outputs" / "openpine_batch_status.json", status)


def test_run_meta_schema_v2_requires_production_profile_and_revisions(
    tmp_path: Path,
) -> None:
    entry = _entry(tmp_path)
    _write_completed_run(entry)
    meta_path = entry.root / "openpine_outputs" / "15m" / "run_meta.json"

    assert _run_meta_valid(meta_path)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["compile_profile"] = "diagnostic"
    write_json(meta_path, meta)

    assert not _run_meta_valid(meta_path)


def test_skip_completed_accepts_only_complete_schema_v2_outputs(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    _write_completed_run(entry)

    assert completed_for_selection(entry, _args())


def test_skip_completed_rejects_empty_expected_output(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    _write_completed_run(entry)
    (entry.root / "openpine_outputs" / "15m" / "trades.csv").write_text(
        "", encoding="utf-8"
    )

    assert not completed_for_selection(entry, _args())


def test_skip_completed_rejects_missing_run_summary(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    _write_completed_run(entry)
    (entry.root / "openpine_outputs" / "15m" / "summary.json").unlink()

    assert not completed_for_selection(entry, _args())


def test_summary_by_timeframe_reports_actual_run_statuses() -> None:
    results = [
        {
            "id": 1,
            "kind": "strategy",
            "status": "partial_or_error",
            "runs": [
                {
                    "timeframe": "15m",
                    "status": "ok",
                    "bars": 10,
                    "plots_rows": 2,
                    "trades_rows": 1,
                    "equity_rows": 3,
                },
                {
                    "timeframe": "1h",
                    "status": "run_error",
                    "error": "boom",
                },
            ],
        },
        {
            "id": 2,
            "kind": "indicator",
            "status": "compile_error",
            "selected_timeframes": ["15m"],
        },
    ]

    assert summary_by_timeframe(results) == {
        "15m": {
            "selected": 2,
            "statuses": {"ok": 1, "compile_error": 1},
            "bars": 10,
            "plots_rows": 2,
            "trades_rows": 1,
            "equity_rows": 3,
        },
        "1h": {
            "selected": 1,
            "statuses": {"run_error": 1},
            "bars": 0,
            "plots_rows": 0,
            "trades_rows": 0,
            "equity_rows": 0,
        },
    }


def test_current_progress_can_publish_timeframe_summary(tmp_path: Path) -> None:
    summary = {
        "15m": {
            "selected": 1,
            "statuses": {"ok": 1},
            "bars": 10,
            "plots_rows": 2,
            "trades_rows": 1,
            "equity_rows": 3,
        },
    }

    _write_progress(
        tmp_path,
        "batch-1",
        1,
        "run",
        "ok",
        selected_count=3,
        processed_count=1,
        summary_by_timeframe=summary,
    )

    payload = json.loads(
        (tmp_path / "current_progress.json").read_text(encoding="utf-8")
    )

    assert payload["selected_count"] == 3
    assert payload["processed_count"] == 1
    assert payload["summary_by_timeframe"] == summary


def test_strategy_run_config_uses_declaration_values() -> None:
    chart = ChartExport(
        timeframe="15m",
        path=Path("chart.csv"),
        bars=10,
        start_ms=1_700_000_000_000,
        end_ms=1_700_008_100_000,
    )
    args = argparse.Namespace(
        symbol="BTCUSDT",
        exchange="binance",
        market_type="spot",
        qty_step=0.001,
        qty_rounding_mode="truncate",
    )
    data_meta = {
        "calculation_from": 1_699_000_000_000,
        "calculation_to": 1_700_008_100_000,
    }
    decl_args = {
        "initial_capital": 25_000,
        "default_qty_type": "percent_of_equity",
        "default_qty_value": 50,
        "commission_type": "percent",
        "commission_value": 0.1,
        "close_entries_rule": "any",
        "pyramiding": 2,
    }

    config = _build_strategy_run_config(
        chart=chart,
        args=args,
        data_meta=data_meta,
        decl_args=decl_args,
        config_cls=argparse.Namespace,
    )

    assert config.symbol == "BTCUSDT"
    assert config.timeframe == "15m"
    assert config.initial_capital == 25_000
    assert config.default_qty_type == "percent_of_equity"
    assert config.default_qty_value == 50
    assert config.commission_type == "percent"
    assert config.commission_value == 0.1
    assert config.exit_matching == "ANY"
    assert config.pyramiding == 2
    assert config.qty_step == 0.001
    assert config.qty_rounding_mode == "truncate"
    assert config.plot_from_ms == chart.start_ms
    assert config.plot_to_ms == chart.end_ms + 900_000


def test_infer_tv_bar_index_offset_from_exported_bar_plot(tmp_path: Path) -> None:
    chart_path = tmp_path / "chart.csv"
    chart_path.write_text(
        "\n".join(
            [
                "time,open,high,low,close,P012_INT_BAR",
                "1700001800,1,2,1,2,21304",
                "1700002700,2,3,2,3,21305",
                "1700003600,3,4,3,4,21306",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    chart = ChartExport(
        timeframe="15m",
        path=chart_path,
        bars=3,
        start_ms=1_700_001_800_000,
        end_ms=1_700_003_600_000,
    )
    bars = [
        argparse.Namespace(time=1_700_000_000_000),
        argparse.Namespace(time=1_700_000_900_000),
        argparse.Namespace(time=1_700_001_800_000),
        argparse.Namespace(time=1_700_002_700_000),
        argparse.Namespace(time=1_700_003_600_000),
    ]

    offset, meta = _infer_tv_bar_index_offset(chart, bars)

    assert offset == 21302
    assert meta == {
        "status": "inferred",
        "column": "P012_INT_BAR",
        "first_visible_local_index": 2,
        "tv_first_bar_index": 21304,
        "offset": 21302,
    }


def test_infer_tv_bar_index_offset_from_periodic_artificial_na(tmp_path: Path) -> None:
    chart_path = tmp_path / "chart.csv"
    rows = ["time,open,high,low,close,P006_MAYBE_NA,P006_MAYBE_NA2"]
    first_tv_bar_mod_77 = 51
    for idx in range(40):
        tv_bar_index = first_tv_bar_mod_77 + idx
        maybe_na = "" if tv_bar_index % 7 == 0 else "100"
        maybe_na2 = "" if tv_bar_index % 11 == 0 else "200"
        rows.append(f"{1_700_000_000 + idx * 900},1,2,1,2,{maybe_na},{maybe_na2}")
    chart_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    chart = ChartExport(
        timeframe="15m",
        path=chart_path,
        bars=40,
        start_ms=1_700_000_000_000,
        end_ms=1_700_035_100_000,
    )
    first_visible_local_index = 18
    bars = [
        *[
            argparse.Namespace(
                time=chart.start_ms - (first_visible_local_index - idx) * 900_000
            )
            for idx in range(first_visible_local_index)
        ],
        *[argparse.Namespace(time=chart.start_ms + idx * 900_000) for idx in range(40)],
    ]

    offset, meta = _infer_tv_bar_index_offset(chart, bars)

    assert offset == 33
    assert meta is not None
    assert meta["status"] == "inferred_periodic_na"
    assert meta["tv_first_bar_index_mod"] == 51
    assert meta["modulus"] == 77


def test_finish_entry_status_adds_elapsed_seconds(monkeypatch) -> None:
    ticks = iter([10.0, 12.3456])
    monkeypatch.setattr(batch_runner.time, "perf_counter", lambda: next(ticks))

    status = _finish_entry_status(
        {"status": "planned"}, batch_runner.time.perf_counter()
    )

    assert status == {"status": "planned", "elapsed_sec": 2.346}


def test_batch_arg_parser_exposes_run_defaults() -> None:
    args = _build_arg_parser().parse_args(["--phase", "run", "--limit", "3"])

    assert args.phase == "run"
    assert args.limit == 3
    assert args.provider_only_bars is False
    assert args.symbol == "BTCUSDT"
    assert args.exchange == "binance"
    assert args.market_type == "spot"
    assert args.progress_every == 10_000


def test_write_timeframe_summary_csv_writes_run_rows(tmp_path: Path) -> None:
    path = _write_timeframe_summary_csv(
        root=tmp_path,
        phase="run",
        batch_id="batch-1",
        results=[
            {
                "id": 1,
                "kind": "strategy",
                "runs": [
                    {
                        "timeframe": "15m",
                        "status": "ok",
                        "bars": 10,
                        "plots_rows": 2,
                        "trades_rows": 1,
                        "equity_rows": 3,
                    }
                ],
            }
        ],
    )

    assert path == tmp_path / "openpine_batch_run_batch-1_by_timeframe.csv"
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "batch_id,export_id,kind,timeframe,status,bars" in text
    assert "batch-1,1,strategy,15m,ok,10" in text


def test_build_batch_summary_payload_serializes_timeframe_bounds(
    tmp_path: Path,
) -> None:
    entry = _entry(tmp_path)
    args = argparse.Namespace(
        phase="run",
        root=tmp_path,
        manifest=tmp_path / "manifest.json",
        symbol="ETHUSDT",
        exchange="binance",
        market_type="spot",
        calculation_from="2024-01-01",
        calculation_to=None,
        _calculation_to_by_timeframe={"15m": entry.charts[0].end_ms},
    )

    payload = _build_batch_summary_payload(
        args=args,
        batch_id="batch-1",
        errors_path=tmp_path / "errors.jsonl",
        library_revisions={"openpine": "abc123"},
        selected=[entry],
        entries=[entry, entry],
        results=[{"status": "ok"}],
        timeframe_summary={"15m": {"selected": 1}},
    )

    assert payload["batch_id"] == "batch-1"
    assert payload["selected"] == 1
    assert payload["total_manifest_entries"] == 2
    assert payload["summary"]["stats"] == {"ok": 1}
    assert payload["summary_by_timeframe"] == {"15m": {"selected": 1}}
    assert payload["calculation_to_by_timeframe"] == {
        "15m": "2023-11-15T00:28:20+00:00"
    }


def test_batch_run_cli_pins_run_phase(monkeypatch) -> None:
    from openpine.cli.batch import batch

    captured: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(batch_runner, "main", fake_main)

    result = CliRunner().invoke(batch, ["run", "--phase", "plan", "--limit", "1"])

    assert result.exit_code == 0
    assert captured["argv"] == ["--phase", "plan", "--limit", "1", "--phase", "run"]
