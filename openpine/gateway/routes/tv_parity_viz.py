"""TV Parity visualization helpers.

Pure helpers that consume comparison artifacts (CSV/JSON) and produce
ready-to-render structures for the UI:

* :func:`build_chart_data` — equity overlay points
* :func:`top_mismatches` — ranked per-bar/per-trade deltas
* :func:`diagnostics_callouts` — ``P092_DIAG_*`` markers from the chart CSV
* :func:`summary_cards` — Bloomberg-style compact status
* :func:`render_html_report` — self-contained HTML
* :func:`render_png_report` — equity overlay PNG via matplotlib
* :func:`build_export_zip` — zip bundle of all artifacts + reports

All helpers degrade gracefully when an artifact is missing or malformed: a
malformed comparison summary is treated as no-mismatch, and a missing chart
CSV is reported as ``callouts=[]``. The visualization layer is intentionally
forgiving because the upstream comparison path is already strict and these
helpers must never break a TV parity read endpoint.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

_P092_DIAG_PATTERN = re.compile(r"^P092_DIAG_(?P<name>[A-Z0-9_]+)$")


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        log.warning("tv_parity_viz: csv read failed path=%s err=%s", path, exc)
        return []


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("tv_parity_viz: json read failed path=%s err=%s", path, exc)
        return default


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _as_int(value: Any) -> int | None:
    f = _as_float(value)
    if f is None:
        return None
    return int(f)


# ---------------------------------------------------------------------------
# Chart data
# ---------------------------------------------------------------------------


def build_chart_data(
    *,
    run_root: Path,
    abs_tol: float = 0.0,
    rel_tol: float = 0.0,
) -> dict[str, Any]:
    """Aligned equity + chart series for the overlay canvas.

    Returns:
        ``series`` is a list of ``{kind, t, v, ...}`` records where ``kind``
        is one of ``openpine_equity``, ``tv_equity``, ``openpine_ohlc``,
        ``tv_ohlc``, ``signal`` and ``marker``. ``tv_equity`` and ``tv_ohlc``
        are only populated when the comparison summary references a TV
        equity / chart file.

        ``max_abs_delta``/``mismatch_cells`` are pulled from the comparison
        summary when present, and used by the chart's tolerance band.
    """
    series: list[dict[str, Any]] = []
    openpine_equity_path = run_root / "openpine_outputs" / "equity_curve.csv"
    openpine_equity_rows = _read_csv_dicts(openpine_equity_path)
    op_eq_pts: list[tuple[int, float]] = []
    for row in openpine_equity_rows:
        t = _as_int(
            row.get("bar_time")
            or row.get("bar_time_ms")
            or row.get("time")
            or row.get("t")
        )
        v = _as_float(row.get("equity") or row.get("v"))
        if t is None or v is None:
            continue
        op_eq_pts.append((t, v))
        series.append(
            {"kind": "openpine_equity", "t": t, "v": v}
        )

    # TV equity curve (when uploaded) — column 'equity' or 'value' or 'tv_equity'
    tv_equity_pts: list[tuple[int, float]] = []
    for candidate in (
        run_root / "uploads" / "equity.csv",
        run_root / "uploads" / "tv_equity.csv",
    ):
        if not candidate.exists():
            continue
        for row in _read_csv_dicts(candidate):
            t = _as_int(row.get("bar_time") or row.get("time") or row.get("t"))
            v = _as_float(row.get("equity") or row.get("value") or row.get("v"))
            if t is None or v is None:
                continue
            tv_equity_pts.append((t, v))
            series.append({"kind": "tv_equity", "t": t, "v": v})
        break

    # OpenPine OHLC (when plots captured) — emitted as a hint for future
    # candle rendering. Today we only carry equity, but the wire format is
    # forward-compatible.
    openpine_plots = _read_csv_dicts(run_root / "openpine_outputs" / "plots.csv")
    for row in openpine_plots:
        t = _as_int(row.get("bar_time") or row.get("time") or row.get("t"))
        if t is None:
            continue
        o = _as_float(row.get("open") or row.get("o"))
        h = _as_float(row.get("high") or row.get("h"))
        lo = _as_float(row.get("low") or row.get("l"))
        c = _as_float(row.get("close") or row.get("c"))
        if None in (o, h, lo, c):
            continue
        series.append(
            {"kind": "openpine_ohlc", "t": t, "o": o, "h": h, "l": lo, "c": c}
        )

    comparison = _read_json(run_root / "comparison" / "comparison_summary.json", {})
    failures = comparison.get("failures") or []
    trades_summary = next(
        (c for c in (comparison.get("comparisons") or []) if c.get("type") == "trades"),
        {},
    )
    plots_summary = next(
        (c for c in (comparison.get("comparisons") or []) if c.get("type") == "plots"),
        {},
    )
    max_abs_delta = _as_float(trades_summary.get("max_abs_delta")) or 0.0
    mismatch_cells = _as_int(trades_summary.get("mismatch_cells")) or 0

    initial_equity = op_eq_pts[0][1] if op_eq_pts else None
    final_equity = op_eq_pts[-1][1] if op_eq_pts else None

    return {
        "series": series,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "max_abs_delta": max_abs_delta,
        "mismatch_cells": mismatch_cells,
        "tv_equity": tv_equity_pts,
        "failures": failures,
        "plots": plots_summary,
        "trades": trades_summary,
        "initial_equity": initial_equity,
        "final_equity": final_equity,
    }


# ---------------------------------------------------------------------------
# Top mismatches
# ---------------------------------------------------------------------------


def top_mismatches(*, run_root: Path, limit: int = 20) -> dict[str, Any]:
    """Top-N bars/trades by absolute delta (descending).

    Reads ``comparison/comparison_summary.csv`` and ranks by
    ``delta_net_profit_abs`` then ``delta_entry_price_abs`` (ties).
    """
    rows = _read_csv_dicts(run_root / "comparison" / "comparison_summary.csv")
    enriched: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        bar_time = _as_int(row.get("bar_time"))
        delta_net = _as_float(row.get("delta_net_profit")) or 0.0
        delta_net_abs = _as_float(row.get("delta_net_profit_abs"))
        if delta_net_abs is None:
            delta_net_abs = abs(delta_net)
        delta_entry = _as_float(row.get("delta_entry_price")) or 0.0
        delta_entry_abs = _as_float(row.get("delta_entry_price_abs"))
        if delta_entry_abs is None:
            delta_entry_abs = abs(delta_entry)
        delta_exit = _as_float(row.get("delta_exit_price")) or 0.0
        delta_qty = _as_float(row.get("delta_qty")) or 0.0
        enriched.append(
            {
                "bar_time": bar_time,
                "row_kind": row.get("row_kind") or "trade",
                "trade_index": _as_int(row.get("trade_index")),
                "delta_entry_price": delta_entry,
                "delta_exit_price": delta_exit,
                "delta_qty": delta_qty,
                "delta_net_profit": delta_net,
                "delta_entry_price_abs": delta_entry_abs,
                "delta_net_profit_abs": delta_net_abs,
            }
        )
    enriched.sort(
        key=lambda r: (r["delta_net_profit_abs"], r["delta_entry_price_abs"]),
        reverse=True,
    )
    return {
        "total": len(enriched),
        "limit": limit,
        "items": enriched[: max(0, int(limit))],
    }


# ---------------------------------------------------------------------------
# Diagnostics callouts
# ---------------------------------------------------------------------------


def diagnostics_callouts(*, run_root: Path) -> dict[str, Any]:
    """Collect ``P092_DIAG_*`` markers from the uploaded TV chart CSV.

    Each marker is normalized to ``{bar_time, name, new_closed_trade,
    last_closed_profit, last_closed_size, last_closed_entry_price,
    last_closed_exit_price}``. The marker is only emitted when
    ``P092_DIAG_NEW_CLOSED_TRADE == 1`` (i.e. the bar actually closed a
    trade in TV).
    """
    chart_paths: list[Path] = [
        run_root / "uploads" / "chart.csv",
        run_root / "uploads" / "candles.csv",
    ]
    chart_rows: list[dict[str, str]] = []
    chart_path_used: Path | None = None
    for candidate in chart_paths:
        if candidate.exists():
            chart_rows = _read_csv_dicts(candidate)
            chart_path_used = candidate
            break
    callouts: list[dict[str, Any]] = []
    for row in chart_rows:
        bar_time = _as_int(
            row.get("bar_time") or row.get("time") or row.get("t")
        )
        if bar_time is None:
            continue
        new_trade = _as_int(row.get("P092_DIAG_NEW_CLOSED_TRADE")) or 0
        if new_trade != 1:
            continue
        callouts.append(
            {
                "bar_time": bar_time,
                "new_closed_trade": 1,
                "last_closed_profit": _as_float(
                    row.get("P092_DIAG_LAST_CLOSED_PROFIT")
                ),
                "last_closed_size": _as_float(
                    row.get("P092_DIAG_LAST_CLOSED_SIZE")
                ),
                "last_closed_entry_price": _as_float(
                    row.get("P092_DIAG_LAST_CLOSED_ENTRY_PRICE")
                ),
                "last_closed_exit_price": _as_float(
                    row.get("P092_DIAG_LAST_CLOSED_EXIT_PRICE")
                ),
            }
        )
    return {
        "chart_path": str(chart_path_used) if chart_path_used else None,
        "callouts": callouts,
    }


# ---------------------------------------------------------------------------
# Summary cards
# ---------------------------------------------------------------------------


def summary_cards(*, run_root: Path) -> dict[str, Any]:
    """Bloomberg-style compact status payload for the UI cards row."""
    result = _read_json(run_root / "tv_parity_result.json", {})
    comparison = _read_json(run_root / "comparison" / "comparison_summary.json", {})
    failures: list[dict[str, Any]] = comparison.get("failures") or []
    comparisons: list[dict[str, Any]] = comparison.get("comparisons") or []
    trades_summary = next(
        (c for c in comparisons if c.get("type") == "trades"), {}
    )
    plots_summary = next(
        (c for c in comparisons if c.get("type") == "plots"), {}
    )
    equity_summary = next(
        (c for c in comparisons if c.get("type") == "equity"), {}
    )
    trades_status = trades_summary.get("status")
    if not trades_status:
        # Infer status from the rest of the comparison summary so legacy
        # payloads (no explicit ``status`` field) still render correctly.
        mismatch_cells = _as_int(trades_summary.get("mismatch_cells")) or 0
        max_abs_delta = _as_float(trades_summary.get("max_abs_delta")) or 0.0
        if _as_int(trades_summary.get("tv_rows")) and _as_int(
            trades_summary.get("openpine_rows")
        ):
            trades_status = "match" if (mismatch_cells == 0 and max_abs_delta == 0.0) else "mismatch"
        else:
            trades_status = "skipped"
    plots_status = plots_summary.get("status") or "skipped"
    equity_status = equity_summary.get("status") or "skipped"
    overall = "match"
    if failures:
        overall = "mismatch"
    elif result.get("status") == "failed":
        overall = "failed"
    elif trades_status == "mismatch":
        overall = "mismatch"

    chart = build_chart_data(run_root=run_root)
    return {
        "run_id": result.get("run_id"),
        "strategy_id": result.get("strategy_id"),
        "status": result.get("status"),
        "compare_from": result.get("compare_from"),
        "compare_to": result.get("compare_to"),
        "overall_status": overall,
        "trades_match": trades_status == "match",
        "trades_status": trades_status,
        "plots_status": plots_status,
        "equity_status": equity_status,
        "max_abs_delta": chart["max_abs_delta"],
        "mismatch_cells": chart["mismatch_cells"],
        "failure_count": len(failures),
        "failures": failures,
        "initial_equity": chart["initial_equity"],
        "final_equity": chart["final_equity"],
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def _sparkline_svg(
    points: Iterable[tuple[int, float]], *, width: int = 720, height: int = 180
) -> str:
    pts = list(points)
    if not pts:
        return (
            f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
            '<text x="10" y="20" fill="#888">no data</text></svg>'
        )
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max == x_min:
        x_max = x_min + 1
    if y_max == y_min:
        y_max = y_min + 1.0
    coords: list[str] = []
    for x, y in pts:
        nx = (x - x_min) / (x_max - x_min)
        ny = (y - y_min) / (y_max - y_min)
        coords.append(
            f"{nx * (width - 20) + 10:.2f},{height - 10 - ny * (height - 20):.2f}"
        )
    polyline = " ".join(coords)
    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="equity overlay sparkline">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#101418"/>'
        f'<polyline points="{polyline}" fill="none" stroke="#3ad29f" stroke-width="1.5"/>'
        "</svg>"
    )


def render_html_report(
    *, run_root: Path, run_id: str, strategy_id: str | None = None
) -> str:
    """Render a self-contained HTML report (no external CSS/JS)."""
    cards = summary_cards(run_root=run_root)
    chart = build_chart_data(run_root=run_root)
    top = top_mismatches(run_root=run_root, limit=20)
    diag = diagnostics_callouts(run_root=run_root)
    op_eq = [(row["t"], row["v"]) for row in chart["series"] if row["kind"] == "openpine_equity"]
    tv_eq = chart.get("tv_equity") or []
    initial = cards.get("initial_equity")
    final = cards.get("final_equity")
    pnl = (final - initial) if (initial is not None and final is not None) else None

    def _fmt_pct(n: float | None) -> str:
        if n is None or initial in (None, 0):
            return "n/a"
        return f"{(n / initial) * 100:.3f}%"

    overall_badge = {
        "match": '<span class="badge ok">MATCH</span>',
        "mismatch": '<span class="badge bad">MISMATCH</span>',
        "failed": '<span class="badge bad">FAILED</span>',
    }.get(cards["overall_status"], f'<span class="badge">{cards["overall_status"]}</span>')

    def _fmt_money(v: float | None) -> str:
        return f"${v:,.2f}" if v is not None else "n/a"

    def _fmt_pnl(v: float | None) -> str:
        return f"{v:,.2f}" if v is not None else "n/a"

    def _fmt_delta(v: float | None) -> str:
        return f"{v:.3e}" if v is not None else "n/a"

    cards_html = (
        '<div class="cards">'
        f'<div class="card"><div class="card-title">Status</div><div class="card-value">{overall_badge}</div></div>'
        f'<div class="card"><div class="card-title">Trades</div><div class="card-value">{escape(cards["trades_status"])}</div></div>'
        f'<div class="card"><div class="card-title">Plots</div><div class="card-value">{escape(cards["plots_status"])}</div></div>'
        f'<div class="card"><div class="card-title">Equity</div><div class="card-value">{escape(cards["equity_status"])}</div></div>'
        f'<div class="card"><div class="card-title">Max |Δ|</div><div class="card-value">{_fmt_delta(cards["max_abs_delta"])}</div></div>'
        f'<div class="card"><div class="card-title">Mismatched cells</div><div class="card-value">{cards["mismatch_cells"]}</div></div>'
        f'<div class="card"><div class="card-title">Failures</div><div class="card-value">{cards["failure_count"]}</div></div>'
        f'<div class="card"><div class="card-title">Initial → Final</div><div class="card-value">{_fmt_money(initial)} → {_fmt_money(final)}</div>'
        f'<div class="card-sub">PnL {_fmt_pnl(pnl)} ({_fmt_pct(pnl)})</div></div>'
        "</div>"
    )

    op_svg = _sparkline_svg(op_eq)
    tv_svg = (
        _sparkline_svg(tv_eq)
        if tv_eq
        else '<div class="muted">no TV equity uploaded</div>'
    )

    empty_top = '<tr><td colspan="7" class="muted">no mismatches</td></tr>'
    top_rows_list = []
    for i, item in enumerate(top["items"]):
        top_rows_list.append(
            "<tr>"
            f"<td>{i + 1}</td>"
            f"<td>{item['bar_time']}</td>"
            f"<td>{escape(str(item['row_kind']))}</td>"
            f"<td>{escape(str(item['trade_index']) if item['trade_index'] is not None else '')}</td>"
            f"<td>{item['delta_entry_price']:.3e}</td>"
            f"<td>{item['delta_exit_price']:.3e}</td>"
            f"<td>{item['delta_net_profit']:.3e}</td>"
            "</tr>"
        )
    top_rows = "".join(top_rows_list)
    top_table = (
        '<table class="grid"><thead><tr>'
        '<th>#</th><th>bar_time</th><th>row</th><th>trade</th>'
        '<th>Δ entry</th><th>Δ exit</th><th>Δ net_profit</th>'
        '</tr></thead><tbody>'
        f'{top_rows or empty_top}'
        '</tbody></table>'
    )

    diag_rows = "".join(
        "<tr>"
        f"<td>{item['bar_time']}</td>"
        f"<td>{item['last_closed_profit'] if item['last_closed_profit'] is not None else ''}</td>"
        f"<td>{item['last_closed_size'] if item['last_closed_size'] is not None else ''}</td>"
        f"<td>{item['last_closed_entry_price'] if item['last_closed_entry_price'] is not None else ''}</td>"
        f"<td>{item['last_closed_exit_price'] if item['last_closed_exit_price'] is not None else ''}</td>"
        "</tr>"
        for item in diag["callouts"]
    )
    empty_diag = '<tr><td colspan="5" class="muted">no P092 markers</td></tr>'
    diag_table = (
        '<table class="grid"><thead><tr>'
        '<th>bar_time</th><th>profit</th><th>size</th><th>entry</th><th>exit</th>'
        f'</tr></thead><tbody>{diag_rows or empty_diag}</tbody></table>'
    )

    failures_html = "".join(
        f"<li><b>{escape(str(f.get('type', '')))}</b>: {escape(str(f.get('summary', {}).get('classification', '')))}</li>"
        for f in cards.get("failures") or []
    )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    empty_failures = '<li class="muted">none</li>'

    style_lines = [
        " body { background:#101418; color:#eef4f8; font-family: -apple-system, system-ui, sans-serif; margin:0; padding:24px; }",
        " h1 { font-size:18px; margin:0 0 8px 0; }",
        " h2 { font-size:14px; margin:24px 0 8px 0; color:#9bb3c5; text-transform:uppercase; letter-spacing:.08em; }",
        " .muted { color:#6c8194; }",
        " .cards { display:grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap:8px; margin:12px 0 18px; }",
        " .card { background:#172029; border:1px solid #1f2a35; border-radius:6px; padding:10px 12px; }",
        " .card-title { font-size:11px; color:#7e94a7; text-transform:uppercase; letter-spacing:.05em; }",
        " .card-value { font-size:18px; margin-top:4px; font-weight:600; }",
        " .card-sub { font-size:11px; color:#7e94a7; margin-top:2px; }",
        " .badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:12px; }",
        " .badge.ok { background:#103324; color:#3ad29f; }",
        " .badge.bad { background:#3a1a1d; color:#ff7479; }",
        " .grid { width:100%; border-collapse:collapse; font-size:12px; }",
        " .grid th, .grid td { padding:6px 8px; border-bottom:1px solid #1f2a35; text-align:left; }",
        " .grid th { color:#9bb3c5; font-weight:500; text-transform:uppercase; letter-spacing:.05em; font-size:11px; }",
        " ul.failures { margin:0; padding-left:20px; font-size:12px; }",
        " footer { color:#6c8194; font-size:11px; margin-top:24px; }",
    ]
    style_block = "\n".join(style_lines)

    header_meta = (
        f'<div class="muted">run_id: {escape(run_id)} &middot; strategy: '
        f"{escape(str(strategy_id) if strategy_id else '')} &middot; generated: "
        f"{escape(generated)}</div>"
    )
    failures_block = f'<ul class="failures">{failures_html or empty_failures}</ul>'

    parts = [
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        f'<title>OpenPine TV Parity — {escape(run_id)}</title>',
        f'<style>{style_block}</style>',
        '</head>',
        '<body>',
        '<h1>OpenPine TV Parity report</h1>',
        header_meta,
        cards_html,
        '<h2>Equity overlay (OpenPine)</h2>',
        op_svg,
        '<h2>Equity curve (TradingView)</h2>',
        tv_svg,
        '<h2>Top mismatches</h2>',
        top_table,
        '<h2>P092 diagnostics callouts</h2>',
        diag_table,
        '<h2>Failures</h2>',
        failures_block,
        '<footer>OpenPine TV Parity visualization &middot; self-contained report</footer>',
        '</body>',
        '</html>',
        '',
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PNG report (matplotlib headless)
# ---------------------------------------------------------------------------


def render_png_report(*, run_root: Path, run_id: str) -> bytes:
    """Render a small PNG equity overlay with matplotlib (Agg backend)."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    chart = build_chart_data(run_root=run_root)
    op_eq = [(row["t"], row["v"]) for row in chart["series"] if row["kind"] == "openpine_equity"]
    tv_eq = chart.get("tv_equity") or []
    cards = summary_cards(run_root=run_root)

    fig = Figure(figsize=(8, 4), dpi=110)
    ax = fig.add_subplot(111)
    if op_eq:
        xs, ys = zip(*op_eq)
        ax.plot(xs, ys, color="#3ad29f", label="OpenPine", linewidth=1.5)
    if tv_eq:
        xs, ys = zip(*tv_eq)
        ax.plot(xs, ys, color="#5aa9ff", label="TradingView", linewidth=1.0, linestyle="--")
    ax.set_title(f"OpenPine TV Parity — {run_id}", color="#eef4f8")
    ax.set_xlabel("bar_time (ms)")
    ax.set_ylabel("equity")
    ax.grid(True, color="#1f2a35")
    ax.set_facecolor("#101418")
    fig.patch.set_facecolor("#101418")
    for spine in ax.spines.values():
        spine.set_color("#1f2a35")
    ax.tick_params(colors="#9bb3c5")
    if op_eq or tv_eq:
        ax.legend(facecolor="#172029", edgecolor="#1f2a35", labelcolor="#eef4f8")
    status_text = (
        f"status={cards['overall_status']} "
        f"max|Δ|={cards['max_abs_delta'] if cards['max_abs_delta'] is not None else 0.0:.3e} "
        f"mismatches={cards['mismatch_cells'] if cards['mismatch_cells'] is not None else 0}"
    )
    ax.text(
        0.02,
        0.95,
        status_text,
        transform=ax.transAxes,
        color="#9bb3c5",
        fontsize=9,
        verticalalignment="top",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ZIP export
# ---------------------------------------------------------------------------


def build_export_zip(*, run_root: Path) -> bytes:
    """Bundle all comparison artifacts + reports into a single zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Always include the result manifest.
        manifest = run_root / "tv_parity_result.json"
        if manifest.exists():
            zf.write(manifest, "tv_parity_result.json")
        # Walk well-known relative locations.
        for rel in (
            "openpine_outputs/plots.csv",
            "openpine_outputs/trades.csv",
            "openpine_outputs/all_trades.csv",
            "openpine_outputs/equity_curve.csv",
            "comparison/comparison_summary.csv",
            "comparison/comparison_summary.json",
            "comparison/comparison_report.md",
            "comparison/tradingview_trades_normalized.csv",
            "uploads/candles.csv",
            "uploads/chart.csv",
            "uploads/trades.csv",
            "uploads/equity.csv",
        ):
            path = run_root / rel
            if path.exists() and path.is_file():
                zf.write(path, rel)
        # Visualization reports (always generated on demand).
        cards = summary_cards(run_root=run_root)
        zf.writestr("summary-cards.json", json.dumps(cards, indent=2, default=str))
        zf.writestr(
            "chart-data.json",
            json.dumps(
                build_chart_data(run_root=run_root), indent=2, default=str
            ),
        )
        zf.writestr(
            "top-mismatches.json",
            json.dumps(
                top_mismatches(run_root=run_root, limit=50), indent=2, default=str
            ),
        )
        zf.writestr(
            "diagnostics-callouts.json",
            json.dumps(diagnostics_callouts(run_root=run_root), indent=2, default=str),
        )
        zf.writestr(
            "report.html",
            render_html_report(
                run_root=run_root,
                run_id=str(cards.get("run_id") or run_root.name),
                strategy_id=cards.get("strategy_id"),
            ),
        )
        zf.writestr("report.png", render_png_report(run_root=run_root, run_id=str(cards.get("run_id") or run_root.name)))
    return buf.getvalue()
