from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import openpine.cli.compare as compare_module


def _csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_time_comparison_fails_on_missing_columns_or_timestamps(tmp_path: Path) -> None:
    tv = _csv(tmp_path / "tv.csv", "time,a,b\n1000,1,10\n2000,2,20\n")
    missing_column = _csv(tmp_path / "missing.csv", "bar_time,a\n1000,1\n2000,2\n")
    missing_time = _csv(tmp_path / "missing_time.csv", "bar_time,a,b\n1000,1,10\n")

    column_summary, _ = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=missing_column,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )
    time_summary, _ = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=missing_time,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )

    assert column_summary["status"] == "mismatch"
    assert "column_set_mismatch" in str(column_summary["classification"])
    assert column_summary["missing_column_names_in_openpine"] == ["b"]
    assert time_summary["status"] == "mismatch"
    assert "time_window_mismatch" in str(time_summary["classification"])


def test_time_comparison_rejects_text_mismatches_duplicates_and_invalid_times(
    tmp_path: Path,
) -> None:
    tv = _csv(
        tmp_path / "tv.csv",
        "time,regime\n1000,bull\n1000,bull\ninvalid,bull\n2000,bear\n",
    )
    op = _csv(
        tmp_path / "op.csv",
        "bar_time,regime\n1000,bear\n2000,bear\n",
    )

    summary, top = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
        drop_blank_tv_rows=True,
    )

    assert summary["status"] == "mismatch"
    assert "duplicate_timestamps" in str(summary["classification"])
    assert "invalid_timestamps" in str(summary["classification"])
    assert summary["duplicate_times_in_tv"] == [1_000_000]
    assert summary["invalid_time_rows_in_tv"] == 1
    assert top[0]["column"] == "regime"


def test_trade_comparison_does_not_ignore_blank_tv_cells(tmp_path: Path) -> None:
    tv = _csv(tmp_path / "tv_trades.csv", "status,max_runup\nclosed,\n")
    op = _csv(tmp_path / "op_trades.csv", "status,max_runup\nclosed,42\n")

    summary, top = compare_module._compare_rows_by_order(
        tv_path=tv,
        op_path=op,
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
    )

    assert summary["status"] == "mismatch"
    assert summary["blank_tv_cells_compared"] == 1
    assert top[0]["column"] == "max_runup"


def test_closed_bar_comparison_excludes_each_latest_open_bar(tmp_path: Path) -> None:
    tv = _csv(tmp_path / "tv.csv", "time,value\n1000,1\n2000,2\n3000,999\n")
    op = _csv(tmp_path / "op.csv", "bar_time,value\n1000,1\n2000,2\n3000,-999\n")

    summary, _ = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
        closed_bars_only=True,
    )

    assert summary["status"] == "match"
    assert summary["closed_bars_only"] is True
    assert summary["excluded_latest_tv_time_ms"] == 3_000_000
    assert summary["excluded_latest_openpine_time_ms"] == 3_000_000
    assert summary["common_times"] == 2


def test_closed_bar_comparison_rejects_divergent_latest_timestamps(tmp_path: Path) -> None:
    tv = _csv(tmp_path / "tv.csv", "time,value\n1000,1\n2000,2\n3000,3\n")
    op = _csv(
        tmp_path / "op.csv",
        "bar_time,value\n1000,1\n2000,2\n4000,4\n",
    )

    summary, _ = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
        closed_bars_only=True,
    )

    assert summary["status"] == "mismatch"
    assert summary["classification"] == "time_window_mismatch"
    assert summary["excluded_latest_tv_time_ms"] is None
    assert summary["excluded_latest_openpine_time_ms"] is None
    assert summary["missing_times_in_openpine"] == 1
    assert summary["extra_times_in_openpine"] == 1


def test_bounded_closed_bar_comparison_keeps_final_bounded_bar(tmp_path: Path) -> None:
    tv = _csv(tmp_path / "tv.csv", "time,value\n1000,1\n2000,2\n3000,3\n")
    op = _csv(tmp_path / "op.csv", "bar_time,value\n1000,1\n2000,2\n3000,4\n")

    summary, _ = compare_module._compare_rows_by_time(
        tv_path=tv,
        op_path=op,
        tv_time_column="time",
        op_time_column="bar_time",
        exclude_columns=set(),
        abs_tol=0.0,
        rel_tol=0.0,
        compare_to_ms=4_000_000,
        closed_bars_only=True,
    )

    assert summary["status"] == "mismatch"
    assert summary["excluded_latest_tv_time_ms"] is None
    assert summary["excluded_latest_openpine_time_ms"] is None
    assert summary["common_times"] == 3


def test_strategy_evidence_covers_plots_trades_equity_and_hashes_deterministically(
    tmp_path: Path,
) -> None:
    tv_chart = _csv(
        tmp_path / "tv_chart.csv",
        "time,plot\n1000,1\n2000,2\n3000,999\n",
    )
    op_plots = _csv(
        tmp_path / "op_plots.csv",
        "bar_time,plot\n1000,1\n2000,2\n3000,-999\n",
    )
    tv_equity = _csv(
        tmp_path / "tv_equity.csv",
        "time,equity\n1000,100\n2000,101\n3000,999\n",
    )
    op_equity = _csv(
        tmp_path / "op_equity.csv",
        "bar_time_ms,equity\n1000,100\n2000,101\n3000,-999\n",
    )
    tv_trades = _csv(
        tmp_path / "tv_trades.csv",
        "Trade #,Type,Date/Time,Price,Qty,Net Profit\n"
        "1,Entry Long,1000,10,1,\n"
        "1,Exit Long,2000,11,1,1\n",
    )
    op_trades = _csv(
        tmp_path / "op_trades.csv",
        "trade_id,status,direction,entry_time_ms,exit_time_ms,entry_price,exit_price,qty,"
        "net_profit,max_runup,max_drawdown,entry_signal,exit_signal\n"
        "1,closed,long,1000000,2000000,10,11,1,1,,,,\n",
    )
    exported = {
        "plots": str(op_plots),
        "equity": str(op_equity),
        "trades": str(op_trades),
    }

    first = compare_module._compare_strategy_run_with_tv_exports(
        strategy_id="synthetic-phase2",
        run=SimpleNamespace(run_id="run-a"),
        exported=exported,
        output_path=tmp_path / "report-a",
        tv_chart=str(tv_chart),
        tv_trades=str(tv_trades),
        tv_equity=str(tv_equity),
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=True,
        compare_from_ms=None,
        compare_to_ms=None,
    )
    second = compare_module._compare_strategy_run_with_tv_exports(
        strategy_id="synthetic-phase2",
        run=SimpleNamespace(run_id="run-b"),
        exported=exported,
        output_path=tmp_path / "report-b",
        tv_chart=str(tv_chart),
        tv_trades=str(tv_trades),
        tv_equity=str(tv_equity),
        abs_tol=0.0,
        rel_tol=0.0,
        include_base_columns=True,
        compare_from_ms=None,
        compare_to_ms=None,
    )

    assert first["status"] == "match"
    assert first["evidence_complete"] is True
    assert first["comparison_types"] == ["equity", "plots", "trades"]
    comparisons = first["comparisons"]
    assert isinstance(comparisons, list)
    assert {row["type"] for row in comparisons} == {
        "plots",
        "trades",
        "equity",
    }
    assert len(str(first["artifact_hash"])) == 64
    assert first["artifact_hash"] == second["artifact_hash"]
    assert (tmp_path / "report-a" / "comparison_summary.json").is_file()
