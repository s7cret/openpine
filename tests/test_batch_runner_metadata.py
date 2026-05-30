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
    _build_run_meta,
    _build_run_summary,
    _run_meta_valid,
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
    write_json(out_dir / "summary.json", _build_run_summary(entry=entry, chart=chart, run_meta=meta, run_info=run_info))
    for name in ("plots.csv", "trades.csv", "equity_curve.csv"):
        (out_dir / name).write_text("header\n", encoding="utf-8")
    write_json(entry.root / "openpine_outputs" / "openpine_batch_status.json", status)


def test_run_meta_schema_v2_requires_production_profile_and_revisions(tmp_path: Path) -> None:
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
    (entry.root / "openpine_outputs" / "15m" / "trades.csv").write_text("", encoding="utf-8")

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

    payload = json.loads((tmp_path / "current_progress.json").read_text(encoding="utf-8"))

    assert payload["selected_count"] == 3
    assert payload["processed_count"] == 1
    assert payload["summary_by_timeframe"] == summary


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
