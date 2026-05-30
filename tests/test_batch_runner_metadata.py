from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpine.batch.runner import (
    LIBRARY_NAMES,
    ChartExport,
    ExportEntry,
    _build_run_meta,
    _build_run_summary,
    _run_meta_valid,
    completed_for_selection,
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
