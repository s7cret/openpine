from __future__ import annotations

from pathlib import Path

import pytest

from openpine.batch.tv_corpus import (
    build_chart_export,
    filter_entries,
    load_manifest,
    normalize_tf,
)


def _write_chart(path: Path, *, step_ms: int = 900_000) -> None:
    rows = ["time,open,high,low,close,volume"]
    start = 1_700_000_000_000
    for idx in range(3):
        ts = start + idx * step_ms
        rows.append(f"{ts},1,2,0.5,1.5,10")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_build_chart_export_reads_visible_window(tmp_path: Path) -> None:
    chart_path = tmp_path / "BTCUSDT, 15.csv"
    _write_chart(chart_path)

    chart = build_chart_export(chart_path)

    assert chart.timeframe == "15m"
    assert chart.bars == 3
    assert chart.start_ms == 1_700_000_000_000
    assert chart.end_ms == 1_700_001_800_000


def test_load_manifest_returns_typed_entries(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    export_root = root / "exports" / "001_case"
    export_root.mkdir(parents=True)
    (export_root / "source.pine").write_text("//@version=5\n", encoding="utf-8")
    _write_chart(export_root / "chart_15m.csv")
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files\n"
        "1,001_case,strategy,fixture,source.pine,chart_15m.csv\n",
        encoding="utf-8",
    )

    entries = load_manifest(manifest, root)

    assert len(entries) == 1
    assert entries[0].export_id == 1
    assert entries[0].kind == "strategy"
    assert entries[0].charts[0].timeframe == "15m"


def test_filter_entries_normalizes_timeframe(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    export_root = root / "exports" / "001_case"
    export_root.mkdir(parents=True)
    (export_root / "source.pine").write_text("//@version=5\n", encoding="utf-8")
    _write_chart(export_root / "chart_15m.csv")
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files\n"
        "1,001_case,indicator,fixture,source.pine,chart_15m.csv\n",
        encoding="utf-8",
    )

    entries = load_manifest(manifest, root)

    assert normalize_tf("15") == "15m"
    assert filter_entries(
        entries,
        kind="indicator",
        timeframe="15",
        limit=None,
        start_id=None,
        only_id=None,
    ) == entries


def test_load_manifest_fails_without_chart_csv(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    export_root = root / "exports" / "001_case"
    export_root.mkdir(parents=True)
    (export_root / "source.pine").write_text("//@version=5\n", encoding="utf-8")
    manifest = root / "manifest.csv"
    manifest.write_text(
        "id,folder,kind,source_group,pine_files,chart_csv_files\n"
        "1,001_case,indicator,fixture,source.pine,\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no chart CSVs"):
        load_manifest(manifest, root)
