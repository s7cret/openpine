from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openpine.gateway.deps import get_state
from openpine.gateway.routes import tv_parity


def _seed_run(
    tmp_path: Path,
    *,
    run_id: str = "run_viz_1",
    trades_rows: int = 5,
    mismatches: list[dict[str, object]] | None = None,
    include_chart: bool = True,
) -> Path:
    """Build a run directory with comparison artifacts needed for visualization tests."""
    run_root = tmp_path / "tv-parity" / run_id
    openpine_outputs = run_root / "openpine_outputs"
    comparison = run_root / "comparison"
    uploads = run_root / "uploads"
    openpine_outputs.mkdir(parents=True)
    comparison.mkdir(parents=True)
    uploads.mkdir(parents=True)

    # OpenPine equity_curve.csv
    equity_rows = [
        {"bar_time": 1_700_000_000_000 + i * 3_600_000, "equity": 10_000 + i * 10.0}
        for i in range(10)
    ]
    with (openpine_outputs / "equity_curve.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["bar_time", "equity"])
        writer.writeheader()
        for row in equity_rows:
            writer.writerow(row)

    # OpenPine trades.csv (closed trades)
    trade_fields = [
        "bar_time",
        "exit_bar_time",
        "side",
        "entry_price",
        "exit_price",
        "qty",
        "pnl",
        "net_profit",
        "status",
    ]
    trades_path = openpine_outputs / "trades.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=trade_fields)
        writer.writeheader()
        for i in range(trades_rows):
            writer.writerow(
                {
                    "bar_time": 1_700_000_000_000 + i * 3_600_000,
                    "exit_bar_time": 1_700_000_000_000 + (i + 1) * 3_600_000,
                    "side": "long" if i % 2 == 0 else "short",
                    "entry_price": 100 + i * 0.1,
                    "exit_price": 101 + i * 0.1,
                    "qty": 0.01,
                    "pnl": 0.5,
                    "net_profit": 0.4,
                    "status": "closed",
                }
            )

    # Comparison summary JSON with mismatches
    summary = {
        "strategy_id": "strat_viz_1",
        "comparisons": [
            {
                "type": "trades",
                "tv_rows": trades_rows,
                "openpine_rows": trades_rows,
                "common_columns": 8,
                "mismatch_cells": 0,
                "max_abs_delta": 0.0,
                "worst_column": "",
                "worst_time_ms": 0,
            }
        ],
        "failures": [],
    }
    (comparison / "comparison_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )

    # Comparison summary CSV (legacy, kept for back-compat but no longer
    # used by top_mismatches).  Real per-row data lives in
    # tradingview_trades_normalized.csv (TV side) + openpine_outputs/trades.csv
    # (OP side); the visualizer joins them by row index.
    delta_fields = [
        "bar_time",
        "row_kind",
        "trade_index",
        "delta_entry_price",
        "delta_exit_price",
        "delta_qty",
        "delta_net_profit",
        "delta_entry_price_abs",
        "delta_net_profit_abs",
    ]
    delta_rows = mismatches or [
        {
            "bar_time": 1_700_000_000_000 + i * 3_600_000,
            "row_kind": "trade",
            "trade_index": i,
            "delta_entry_price": (i - 2) * 1e-6,
            "delta_exit_price": (i - 2) * 1e-6,
            "delta_qty": 0.0,
            "delta_net_profit": (i - 2) * 1e-5,
            "delta_entry_price_abs": abs((i - 2) * 1e-6),
            "delta_net_profit_abs": abs((i - 2) * 1e-5),
        }
        for i in range(trades_rows)
    ]
    with (comparison / "comparison_summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=delta_fields)
        writer.writeheader()
        for row in delta_rows:
            writer.writerow({k: row.get(k, "") for k in delta_fields})

    # TV normalized trades CSV (mirror of TV List-of-Trades).  Used by
    # top_mismatches + summary_cards as the TV-side per-row ground truth.
    tv_trade_fields = [
        "trade_id",
        "status",
        "direction",
        "entry_time_ms",
        "exit_time_ms",
        "entry_price",
        "exit_price",
        "qty",
        "net_profit",
        "max_runup",
        "max_drawdown",
        "entry_signal",
        "exit_signal",
    ]
    with (comparison / "tradingview_trades_normalized.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=tv_trade_fields)
        writer.writeheader()
        for i in range(trades_rows):
            writer.writerow(
                {
                    "trade_id": str(i + 1),
                    "status": "closed",
                    "direction": "long" if i % 2 == 0 else "short",
                    "entry_time_ms": 1_700_000_000_000 + i * 3_600_000,
                    "exit_time_ms": 1_700_000_000_000 + (i + 1) * 3_600_000,
                    "entry_price": 100 + i * 0.1,
                    "exit_price": 101 + i * 0.1,
                    "qty": 0.01,
                    "net_profit": 0.4,
                    "max_runup": 0.5,
                    "max_drawdown": -0.2,
                    "entry_signal": "P092_LONG",
                    "exit_signal": "P092_CLOSE_LONG",
                }
            )

    # TradingView chart CSV with P092_DIAG_* columns (for diagnostics callouts)
    chart_fields = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "P092_DIAG_NEW_CLOSED_TRADE",
        "P092_DIAG_LAST_CLOSED_PROFIT",
        "P092_DIAG_LAST_CLOSED_SIZE",
        "P092_DIAG_LAST_CLOSED_ENTRY_PRICE",
        "P092_DIAG_LAST_CLOSED_EXIT_PRICE",
    ]
    with (uploads / "chart.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=chart_fields)
        writer.writeheader()
        for i in range(10):
            writer.writerow(
                {
                    "time": 1_700_000_000_000 + i * 3_600_000,
                    "open": 100 + i,
                    "high": 101 + i,
                    "low": 99 + i,
                    "close": 100.5 + i,
                    "P092_DIAG_NEW_CLOSED_TRADE": 1 if i in {1, 3, 5} else 0,
                    "P092_DIAG_LAST_CLOSED_PROFIT": 0.5 if i in {1, 3, 5} else "",
                    "P092_DIAG_LAST_CLOSED_SIZE": 0.01 if i in {1, 3, 5} else "",
                    "P092_DIAG_LAST_CLOSED_ENTRY_PRICE": 100 + i if i in {1, 3, 5} else "",
                    "P092_DIAG_LAST_CLOSED_EXIT_PRICE": 101 + i if i in {1, 3, 5} else "",
                }
            )

    (run_root / "tv_parity_result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "strategy_id": "strat_viz_1",
                "status": "done",
                "compare_from": 1_700_000_000_000,
                "compare_to": 1_700_000_000_000 + 36 * 3_600_000,
            }
        ),
        encoding="utf-8",
    )
    return run_root


def _client(state) -> TestClient:
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: state
    return TestClient(app)


def test_tv_parity_chart_data_returns_aligned_equity_series(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_chart", include_chart=True)
    client = _client(state)

    response = client.get("/api/tv-parity/runs/run_chart/chart-data")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run_chart"
    series = payload["series"]
    # OpenPine equity series must include all 10 rows.
    op_eq = [row for row in series if row["kind"] == "openpine_equity"]
    assert len(op_eq) == 10
    assert op_eq[0]["t"] == 1_700_000_000_000
    assert op_eq[0]["v"] == 10_000.0
    # TV equity curve is optional, but if present in comparison_summary we surface it.
    assert "tv_equity" in payload
    assert payload["abs_tol"] >= 0


def test_tv_parity_top_mismatches_returns_sorted_with_limit(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    run_root = _seed_run(tmp_path, run_id="run_top")
    # Inject a per-row mismatch: shift OP trade #2 entry_time_ms by 86_400_000
    # ms (1 day).  This is the same root-cause pattern we see in production
    # (entry_time_ms column on a per-trade comparison row).
    op_path = run_root / "openpine_outputs" / "trades.csv"
    with op_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    # header + 5 rows, mutate row index 2 (third trade)
    fields = lines[2].strip().split(",")
    if "bar_time" in lines[0]:
        # old shape, no-op
        pass
    # The OP trades.csv has columns: bar_time, exit_bar_time, side,
    # entry_price, exit_price, qty, pnl, net_profit, status.  We rebuild a
    # clean trades.csv with entry_time_ms / exit_time_ms columns so the
    # new top_mismatches reader can join against TV normalized.
    new_fields = [
        "entry_time_ms",
        "exit_time_ms",
        "direction",
        "entry_price",
        "exit_price",
        "qty",
        "net_profit",
        "max_runup",
        "max_drawdown",
        "status",
    ]
    op_rows = [
        {
            "entry_time_ms": 1_700_000_000_000 + i * 3_600_000,
            "exit_time_ms": 1_700_000_000_000 + (i + 1) * 3_600_000,
            "direction": "long" if i % 2 == 0 else "short",
            "entry_price": 100 + i * 0.1,
            "exit_price": 101 + i * 0.1,
            "qty": 0.01,
            "net_profit": 0.4,
            "max_runup": 0.5,
            "max_drawdown": -0.2,
            "status": "closed",
        }
        for i in range(5)
    ]
    # Shift trade #2 entry_time_ms by 1 day to introduce a real mismatch.
    op_rows[2]["entry_time_ms"] += 86_400_000
    with op_path.open("w", encoding="utf-8") as f:
        f.write(",".join(new_fields) + "\n")
        for row in op_rows:
            f.write(",".join(str(row[k]) for k in new_fields) + "\n")
    client = _client(state)

    response = client.get(
        "/api/tv-parity/runs/run_top/mismatches/top?limit=10"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    # Top must be sorted desc by |delta_net_profit|.
    deltas = [row["delta_net_profit_abs"] for row in payload["items"]]
    assert deltas == sorted(deltas, reverse=True)
    # Worst is the entry_time_ms shift we injected.
    worst = payload["items"][0]
    assert worst["column"] == "entry_time_ms"
    assert worst["delta_net_profit_abs"] == 86_400_000
    assert worst["trade_index"] == 2


def test_tv_parity_diagnostics_callouts_collects_p092_markers(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_diag")
    client = _client(state)

    response = client.get(
        "/api/tv-parity/runs/run_diag/diagnostics/callouts"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "run_diag"
    assert len(payload["callouts"]) == 3
    sample = payload["callouts"][0]
    assert sample["bar_time"] >= 1_700_000_000_000
    assert sample["new_closed_trade"] == 1
    assert "last_closed_profit" in sample
    assert "last_closed_size" in sample


def test_tv_parity_summary_cards_for_match(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_match")
    client = _client(state)

    response = client.get("/api/tv-parity/runs/run_match/summary-cards")
    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "match"
    assert payload["trades_match"] is True
    assert payload["failure_count"] == 0
    # Both max_abs_delta subfields must be 0 (no time / no price mismatches).
    assert payload["max_abs_delta"] == 0.0
    assert payload["max_abs_delta_time_ms"] == 0.0
    assert payload["max_abs_delta_price"] == 0.0
    assert payload["initial_equity"] == 10_000.0
    assert payload["final_equity"] == 10_090.0
    assert "initial_equity" in payload
    assert "final_equity" in payload


def test_tv_parity_report_html_is_self_contained(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_html")
    client = _client(state)

    response = client.get("/api/tv-parity/runs/run_html/report.html")
    assert response.status_code == 200
    body = response.text
    assert "<!doctype html>" in body.lower()
    assert "OpenPine" in body
    assert "run_html" in body
    assert "P092" in body  # diagnostics callout summary section


def test_tv_parity_report_png_is_well_formed(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_png")
    client = _client(state)

    response = client.get("/api/tv-parity/runs/run_png/report.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    # PNG magic header
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_tv_parity_export_zip_includes_all_artifacts(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    _seed_run(tmp_path, run_id="run_zip")
    client = _client(state)

    response = client.get("/api/tv-parity/runs/run_zip/export.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(__import__("io").BytesIO(response.content)) as zf:
        names = set(zf.namelist())
    assert "tv_parity_result.json" in names
    assert "openpine_outputs/trades.csv" in names
    assert "openpine_outputs/equity_curve.csv" in names
    assert "comparison/comparison_summary.csv" in names
    assert "report.html" in names
    assert "report.png" in names


def test_tv_parity_visualization_routes_404_for_missing_run(tmp_path: Path) -> None:
    state = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    client = _client(state)
    for url in (
        "/api/tv-parity/runs/missing/chart-data",
        "/api/tv-parity/runs/missing/mismatches/top",
        "/api/tv-parity/runs/missing/diagnostics/callouts",
        "/api/tv-parity/runs/missing/summary-cards",
        "/api/tv-parity/runs/missing/report.html",
        "/api/tv-parity/runs/missing/report.png",
        "/api/tv-parity/runs/missing/export.zip",
    ):
        response = client.get(url)
        assert response.status_code == 404, url
