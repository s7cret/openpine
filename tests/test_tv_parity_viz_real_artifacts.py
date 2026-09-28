"""Real CSV/JSON visualization contracts for sparse and malformed TV artifacts."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from openpine.gateway.routes import tv_parity_viz as viz


def _csv(path: Path, columns: list[str], rows: list[tuple[object, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def _json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_dedicated_equity_takes_priority_over_chart_and_incomplete_ohlc_is_excluded(tmp_path: Path) -> None:
    _csv(tmp_path / "openpine_outputs/equity_curve.csv", ["bar_time", "equity"],
         [(1000, 10000), (2000, 10090), ("garbage", "nan")])
    _csv(tmp_path / "uploads/equity.csv", ["time", "value"],
         [(1000, 9900), (2000, 9950), ("not-a-time", 9800)])
    _csv(tmp_path / "uploads/chart.csv", ["time", "P092_EQUITY"], [(1000, 20000)])
    _csv(tmp_path / "openpine_outputs/plots.csv",
         ["time", "open", "high", "low", "close"],
         [(1000, 1, 3, 1, 2), (2000, 2, 3, "", 3), ("bad", 2, 3, 1, 2)])
    chart = viz.build_chart_data(run_root=tmp_path, abs_tol=1, rel_tol=0.1)
    assert chart["tv_equity"] == [(1000, 9900.0), (2000, 9950.0)]
    assert [(row["t"], row["v"]) for row in chart["series"]
            if row["kind"] == "openpine_equity"] == [(1000, 10000.0), (2000, 10090.0)]
    assert [row for row in chart["series"] if row["kind"] == "openpine_ohlc"] == [
        {"kind": "openpine_ohlc", "t": 1000, "o": 1.0, "h": 3.0, "l": 1.0, "c": 2.0}]
    assert chart["abs_tol"] == 1 and chart["rel_tol"] == 0.1
    assert chart["initial_equity"] == 10000.0 and chart["final_equity"] == 10090.0


def test_chart_equity_uses_explicit_column_then_reconstructed_fallback(tmp_path: Path) -> None:
    path = tmp_path / "uploads/chart.csv"
    _csv(path, ["time", "P092_EQUITY", "P092_DIAG_RECONSTRUCTED_EQUITY"],
         [(1000, "", 9000), (2000, 10010, 9010), (3000, "nan", "oops")])
    chart = viz.build_chart_data(run_root=tmp_path)
    assert chart["tv_equity"] == [(2000, 10010.0)]
    assert [row["source"] for row in chart["series"] if row["kind"] == "tv_equity"] == ["P092_EQUITY"]
    _csv(path, ["time", "P092_DIAG_RECONSTRUCTED_EQUITY"], [(1000, 9000), (2000, 9010)])
    chart = viz.build_chart_data(run_root=tmp_path)
    assert chart["tv_equity"] == [(1000, 9000.0), (2000, 9010.0)]
    assert [row["source"] for row in chart["series"] if row["kind"] == "tv_equity"] == [
        "P092_DIAG_RECONSTRUCTED_EQUITY"] * 2


def test_top_mismatches_rejects_bad_rows_and_deduplicates_summary_column(tmp_path: Path) -> None:
    _csv(tmp_path / "comparison/tradingview_trades_normalized.csv",
         ["entry_time_ms", "entry_price", "exit_price", "qty", "net_profit"],
         [(1000, 10, 12, 2, 4), (2000, "bad", 20, 1, "")])
    _csv(tmp_path / "openpine_outputs/trades.csv",
         ["entry_time_ms", "entry_price", "exit_price", "qty", "net_profit"],
         [(1000, 13, 12, 2, 4), (2000, 50, 22, 1, "")])
    _json(tmp_path / "comparison/comparison_summary.json", {"comparisons": [
        {"type": "trades", "worst_column": "entry_price", "max_abs_delta": 3},
        {"type": "plots", "worst_column": "close", "max_abs_delta": 5, "worst_time_ms": 3000},
        {"type": "equity", "worst_column": "equity", "max_abs_delta": 0}]})
    rows = viz.top_mismatches(run_root=tmp_path)
    assert rows["total"] == 3
    assert [(row["column"], row["delta_net_profit_abs"]) for row in rows["items"]] == [
        ("close", 5.0), ("entry_price", 3.0), ("exit_price", 2.0)]
    assert rows["items"][0]["bar_time"] == 3000
    assert viz.top_mismatches(run_root=tmp_path, limit=-2)["items"] == []


def test_summary_failure_precedence_and_price_time_split(tmp_path: Path) -> None:
    _csv(tmp_path / "openpine_outputs/equity_curve.csv", ["bar_time", "equity"],
         [(1000, 10000), (2000, 10090)])
    _json(tmp_path / "tv_parity_result.json", {"run_id": "visible", "status": "failed"})
    summary = {"failures": [], "comparisons": [
        {"type": "trades", "tv_rows": 2, "openpine_rows": 2,
         "mismatch_cells": 1, "max_abs_delta": 3_600_000, "worst_column": "entry_time_ms"},
        {"type": "plots", "status": "match", "max_abs_delta": 0.25, "worst_column": "close"},
        {"type": "equity", "status": "mismatch"}]}
    path = tmp_path / "comparison/comparison_summary.json"
    _json(path, summary)
    cards = viz.summary_cards(run_root=tmp_path)
    assert cards["overall_status"] == "failed"
    assert cards["trades_status"] == "mismatch"
    assert cards["max_abs_delta_time_ms"] == 3_600_000
    assert cards["max_abs_delta_price"] == 0.25
    assert cards["initial_equity"] == 10000 and cards["final_equity"] == 10090
    html = viz.render_html_report(run_root=tmp_path, run_id="visible<script>")
    assert "+1h" in html and "$10,000.00" in html and "PnL 90.00 (0.900%)" in html
    assert "visible&lt;script&gt;" in html and "visible<script>" not in html
    summary["failures"] = [{"type": "trades", "summary": {"classification": "different"}}]
    _json(path, summary)
    assert viz.summary_cards(run_root=tmp_path)["overall_status"] == "mismatch"


def test_missing_and_malformed_inputs_fail_soft_and_do_not_invent_markers(tmp_path: Path) -> None:
    assert viz.build_chart_data(run_root=tmp_path)["series"] == []
    assert viz.diagnostics_callouts(run_root=tmp_path) == {"chart_path": None, "callouts": []}
    _csv(tmp_path / "uploads/candles.csv", ["time", "P092_DIAG_NEW_CLOSED_TRADE"],
         [("bad", 1), (1000, 0), (2000, 1)])
    assert [row["bar_time"] for row in viz.diagnostics_callouts(run_root=tmp_path)["callouts"]] == [2000]
    (tmp_path / "uploads/chart.csv").write_bytes(b"\xff\xfe")
    (tmp_path / "comparison").mkdir()
    (tmp_path / "comparison/comparison_summary.json").write_text("{unclosed", encoding="utf-8")
    assert viz.build_chart_data(run_root=tmp_path)["failures"] == []
    assert viz.diagnostics_callouts(run_root=tmp_path)["callouts"] == []
    assert viz.top_mismatches(run_root=tmp_path)["total"] == 0
    assert viz.summary_cards(run_root=tmp_path)["overall_status"] == "match"
    assert "no data" in viz.render_html_report(run_root=tmp_path, run_id="empty")


def test_zip_and_png_are_real_self_contained_artifacts(tmp_path: Path) -> None:
    _csv(tmp_path / "openpine_outputs/equity_curve.csv", ["bar_time", "equity"], [(1000, 1), (2000, 2)])
    _csv(tmp_path / "uploads/tv_equity.csv", ["time", "value"], [(1000, 2), (2000, 3)])
    _json(tmp_path / "tv_parity_result.json", {"run_id": "zip", "status": "done"})
    png = viz.render_png_report(run_root=tmp_path, run_id="zip")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with zipfile.ZipFile(io.BytesIO(viz.build_export_zip(run_root=tmp_path))) as archive:
        names = set(archive.namelist())
        assert {"tv_parity_result.json", "openpine_outputs/equity_curve.csv", "summary-cards.json",
                "chart-data.json", "top-mismatches.json", "diagnostics-callouts.json",
                "report.html", "report.png"} <= names
        assert json.loads(archive.read("summary-cards.json"))["initial_equity"] == 1.0
        assert archive.read("report.png")[:8] == b"\x89PNG\r\n\x1a\n"
