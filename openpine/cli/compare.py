"""TradingView/OpenPine CSV comparison helpers for strategy exports."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import click
from openpine.timezones import parse_timestamp_ms

def _compare_csv_float(value) -> float:
    import math as _math

    if value is None:
        return _math.nan
    text = str(value).strip()
    if not text or text.lower() in {"na", "nan", "none", "null"}:
        return _math.nan
    text = text.replace("\u2212", "-").replace("\xa0", "").replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    elif "," in text and "." in text:
        text = text.replace(",", "")
    try:
        return float(text)
    except Exception:
        return _math.nan


def _compare_csv_time_ms(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        raw = int(float(text))
    except Exception:
        try:
            default_tz = "UTC" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
            return parse_timestamp_ms(text, 0, default_tz=default_tz)
        except Exception:
            return None
    return raw * 1000 if abs(raw) < 10_000_000_000 else raw


def _read_compare_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    import csv as _csv

    with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = _csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _compare_normalized_header(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("\xa0", " ").split())


def _find_compare_column(
    fields: list[str], *needles: str, reject: tuple[str, ...] = ()
) -> str | None:
    normalized = {field: _compare_normalized_header(field) for field in fields}
    for field, header in normalized.items():
        if reject and any(item in header for item in reject):
            continue
        if any(item in header for item in needles):
            return field
    return None


def _trade_action_and_direction(value: str | None) -> tuple[str | None, str | None]:
    text = _compare_normalized_header(value or "")
    action = None
    direction = None
    if "entry" in text or "вход" in text:
        action = "entry"
    elif "exit" in text or "выход" in text:
        action = "exit"
    if "long" in text or "длин" in text:
        direction = "long"
    elif "short" in text or "корот" in text:
        direction = "short"
    return action, direction


def _write_normalized_tv_trades(
    *,
    tv_path: Path,
    output_path: Path,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
) -> Path:
    import csv as _csv

    fields, rows = _read_compare_csv(tv_path)
    trade_no_col = _find_compare_column(
        fields, "номер сделки", "trade #", "trade no", "trade number", "trade"
    )
    type_col = _find_compare_column(fields, "тип", "type")
    time_col = _find_compare_column(
        fields, "дата и время", "date/time", "date and time", "date"
    )
    signal_col = _find_compare_column(fields, "сигнал", "signal")
    price_col = _find_compare_column(fields, "цена", "price")
    qty_col = _find_compare_column(
        fields,
        "размер (кол-во)",
        "qty",
        "quantity",
        "contracts",
        "size",
        reject=("сумма", "value", "amount"),
    )
    net_col = _find_compare_column(
        fields,
        "чистая пр/уб",
        "net profit",
        "net p/l",
        "net pnl",
        reject=("%", "percent"),
    )
    runup_col = _find_compare_column(
        fields,
        "благоприятное отклонение",
        "run-up",
        "runup",
        "mfe",
        reject=("%", "percent"),
    )
    drawdown_col = _find_compare_column(
        fields, "неблагоприятное отклонение", "drawdown", "mae", reject=("%", "percent")
    )

    missing = [
        name
        for name, col in {
            "trade": trade_no_col,
            "type": type_col,
            "time": time_col,
            "price": price_col,
        }.items()
        if col is None
    ]
    if missing:
        raise click.ClickException(
            f"Cannot normalize TV trades, missing columns: {', '.join(missing)}"
        )

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        trade_no = str(row.get(trade_no_col) or "").strip()
        if not trade_no:
            continue
        action, direction = _trade_action_and_direction(row.get(type_col))
        if action is None:
            continue
        bucket = grouped.setdefault(trade_no, {"trade_no": trade_no})
        bucket[action] = row
        if direction:
            bucket["direction"] = direction

    normalized_rows: list[dict[str, object]] = []

    def trade_sort_key(item: str) -> tuple[int, float | str]:
        numeric = _compare_csv_float(item)
        return (1, item) if numeric != numeric else (0, numeric)

    for trade_no in sorted(grouped, key=trade_sort_key):
        bucket = grouped[trade_no]
        entry = bucket.get("entry")
        exit_ = bucket.get("exit")
        if not isinstance(entry, dict) and not isinstance(exit_, dict):
            continue
        entry_row = entry if isinstance(entry, dict) else exit_
        exit_row = exit_ if isinstance(exit_, dict) else entry
        entry_time = _compare_csv_time_ms(entry_row.get(time_col))
        exit_time = (
            _compare_csv_time_ms(exit_row.get(time_col))
            if isinstance(exit_, dict)
            else None
        )
        status = "closed" if exit_time is not None else "open"
        window_time = exit_time if status == "closed" else entry_time
        if compare_from_ms is not None and (
            window_time is None or window_time < compare_from_ms
        ):
            continue
        if compare_to_ms is not None and (
            window_time is None or window_time >= compare_to_ms
        ):
            continue
        normalized_rows.append(
            {
                "trade_id": trade_no,
                "status": status,
                "direction": bucket.get("direction"),
                "entry_time_ms": entry_time,
                "exit_time_ms": exit_time,
                "entry_price": entry_row.get(price_col),
                "exit_price": exit_row.get(price_col),
                "qty": (
                    exit_row.get(qty_col)
                    if qty_col and isinstance(exit_row, dict)
                    else None
                )
                or (
                    entry_row.get(qty_col)
                    if qty_col and isinstance(entry_row, dict)
                    else None
                ),
                "net_profit": (
                    exit_row.get(net_col)
                    if net_col and isinstance(exit_row, dict)
                    else None
                ),
                "max_runup": (
                    exit_row.get(runup_col)
                    if runup_col and isinstance(exit_row, dict)
                    else None
                ),
                "max_drawdown": (
                    exit_row.get(drawdown_col)
                    if drawdown_col and isinstance(exit_row, dict)
                    else None
                ),
                "entry_signal": (
                    entry_row.get(signal_col)
                    if signal_col and isinstance(entry_row, dict)
                    else None
                ),
                "exit_signal": (
                    exit_row.get(signal_col)
                    if signal_col and isinstance(exit_row, dict)
                    else None
                ),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
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
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized_rows)
    return output_path


def _chart_diagnostic_prefix(fields: list[str]) -> str | None:
    suffix = "_DIAG_NEW_CLOSED_TRADE"
    required = (
        "_LOCAL_BAR",
        "_CLOSED_TRADES",
        "_DIAG_LAST_CLOSED_PROFIT",
        "_DIAG_LAST_CLOSED_SIZE",
        "_DIAG_LAST_CLOSED_ENTRY_PRICE",
        "_DIAG_LAST_CLOSED_EXIT_PRICE",
        "_DIAG_LAST_CLOSED_ENTRY_BAR",
        "_DIAG_LAST_CLOSED_EXIT_BAR",
    )
    field_set = set(fields)
    for field in fields:
        if not field.endswith(suffix):
            continue
        prefix = field[: -len(suffix)]
        if all(prefix + item in field_set for item in required):
            return prefix
    return None


def _trade_direction_from_prices(
    *, entry_price: float, exit_price: float, qty: float, profit: float
) -> str | None:
    import math as _math

    if any(_math.isnan(value) for value in (entry_price, exit_price, qty, profit)):
        return None
    move = (exit_price - entry_price) * abs(qty)
    if abs(move) <= 1e-12 or abs(profit) <= 1e-12:
        return None
    return "long" if move * profit > 0 else "short"


def _write_chart_diagnostic_tv_trades(
    *,
    tv_chart_path: Path,
    output_path: Path,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
) -> Path | None:
    import csv as _csv
    import math as _math

    fields, rows = _read_compare_csv(tv_chart_path)
    prefix = _chart_diagnostic_prefix(fields)
    if prefix is None:
        return None
    time_col = "time" if "time" in fields else _find_compare_column(fields, "time")
    if time_col is None:
        return None

    local_bar_col = prefix + "_LOCAL_BAR"
    closed_trades_col = prefix + "_CLOSED_TRADES"
    new_closed_col = prefix + "_DIAG_NEW_CLOSED_TRADE"
    profit_col = prefix + "_DIAG_LAST_CLOSED_PROFIT"
    size_col = prefix + "_DIAG_LAST_CLOSED_SIZE"
    entry_price_col = prefix + "_DIAG_LAST_CLOSED_ENTRY_PRICE"
    exit_price_col = prefix + "_DIAG_LAST_CLOSED_EXIT_PRICE"
    entry_bar_col = prefix + "_DIAG_LAST_CLOSED_ENTRY_BAR"
    exit_bar_col = prefix + "_DIAG_LAST_CLOSED_EXIT_BAR"

    time_by_bar: dict[int, int] = {}
    for row in rows:
        bar_number = _compare_csv_float(row.get(local_bar_col))
        time_ms = _compare_csv_time_ms(row.get(time_col))
        if not _math.isnan(bar_number) and time_ms is not None:
            time_by_bar[int(round(bar_number))] = time_ms

    normalized_rows: list[dict[str, object]] = []
    sequence = 0
    for row in rows:
        new_closed = _compare_csv_float(row.get(new_closed_col))
        if _math.isnan(new_closed) or abs(new_closed) <= 1e-12:
            continue
        exit_time = _compare_csv_time_ms(row.get(time_col))
        if exit_time is None:
            exit_bar = _compare_csv_float(row.get(exit_bar_col))
            if not _math.isnan(exit_bar):
                exit_time = time_by_bar.get(int(round(exit_bar)))
        if exit_time is None:
            continue
        if not _time_in_compare_window(exit_time, compare_from_ms, compare_to_ms):
            continue

        entry_bar = _compare_csv_float(row.get(entry_bar_col))
        entry_time = None if _math.isnan(entry_bar) else time_by_bar.get(int(round(entry_bar)))
        entry_price = _compare_csv_float(row.get(entry_price_col))
        exit_price = _compare_csv_float(row.get(exit_price_col))
        qty = _compare_csv_float(row.get(size_col))
        profit = _compare_csv_float(row.get(profit_col))
        closed_count = _compare_csv_float(row.get(closed_trades_col))
        qty_text = None if _math.isnan(qty) else f"{abs(qty):.12g}"
        sequence += 1
        trade_id = (
            str(int(round(closed_count)))
            if not _math.isnan(closed_count)
            else str(sequence)
        )
        normalized_rows.append(
            {
                "trade_id": trade_id,
                "status": "closed",
                "direction": _trade_direction_from_prices(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    qty=qty,
                    profit=profit,
                ),
                "entry_time_ms": entry_time,
                "exit_time_ms": exit_time,
                "entry_price": row.get(entry_price_col),
                "exit_price": row.get(exit_price_col),
                "qty": qty_text,
                "net_profit": row.get(profit_col),
            }
        )

    if not normalized_rows:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "trade_id",
        "status",
        "direction",
        "entry_time_ms",
        "exit_time_ms",
        "entry_price",
        "exit_price",
        "qty",
        "net_profit",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(normalized_rows)
    return output_path


def _time_in_compare_window(
    ts: int,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
) -> bool:
    if compare_from_ms is not None and ts < compare_from_ms:
        return False
    if compare_to_ms is not None and ts >= compare_to_ms:
        return False
    return True


def _sha256_compare_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_rows_by_time(
    *,
    tv_path: Path,
    op_path: Path,
    tv_time_column: str,
    op_time_column: str,
    exclude_columns: set[str],
    abs_tol: float,
    rel_tol: float,
    compare_from_ms: int | None = None,
    compare_to_ms: int | None = None,
    drop_blank_tv_rows: bool = False,
    closed_bars_only: bool = False,
    require_exact_columns: bool = True,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    import math as _math
    import statistics as _statistics

    tv_fields, tv_rows = _read_compare_csv(tv_path)
    op_fields, op_rows = _read_compare_csv(op_path)
    tv_value_fields = set(tv_fields) - exclude_columns - {tv_time_column}
    op_value_fields = set(op_fields) - exclude_columns - {op_time_column}
    common_columns = sorted(tv_value_fields & op_value_fields)
    missing_column_names = sorted(tv_value_fields - op_value_fields)
    extra_column_names = sorted(op_value_fields - tv_value_fields)
    blank_tv_times: set[int] = set()
    duplicate_tv_times: set[int] = set()
    duplicate_op_times: set[int] = set()
    invalid_tv_rows: list[None] = []
    invalid_op_rows: list[None] = []

    def has_comparable_values(row: dict[str, str]) -> bool:
        return any(
            row.get(column) is not None
            and str(row.get(column)).strip().lower()
            not in {"", "na", "nan", "none", "null"}
            for column in common_columns
        )

    def rows_by_time(
        rows: list[dict[str, str]],
        time_column: str,
        *,
        drop_blank_rows: bool = False,
        duplicate_times: set[int],
        invalid_rows: list[None],
    ) -> dict[int, dict[str, str]]:
        indexed: dict[int, dict[str, str]] = {}
        seen_times: set[int] = set()
        for row in rows:
            ts = _compare_csv_time_ms(row.get(time_column))
            if ts is None:
                invalid_rows.append(None)
                continue
            if not _time_in_compare_window(ts, compare_from_ms, compare_to_ms):
                continue
            if ts in seen_times:
                duplicate_times.add(ts)
            seen_times.add(ts)
            if drop_blank_rows and not has_comparable_values(row):
                blank_tv_times.add(ts)
                continue
            indexed[ts] = row
        return indexed

    def latest_time(rows: list[dict[str, str]], time_column: str) -> int | None:
        times = [
            ts
            for row in rows
            if (ts := _compare_csv_time_ms(row.get(time_column))) is not None
            and _time_in_compare_window(ts, compare_from_ms, compare_to_ms)
        ]
        return max(times, default=None)

    exclude_unbounded_latest = closed_bars_only and compare_to_ms is None
    latest_tv_time = (
        latest_time(tv_rows, tv_time_column) if exclude_unbounded_latest else None
    )
    latest_op_time = (
        latest_time(op_rows, op_time_column) if exclude_unbounded_latest else None
    )
    shared_latest_time = (
        latest_tv_time
        if latest_tv_time is not None and latest_tv_time == latest_op_time
        else None
    )
    excluded_latest_tv_time = shared_latest_time
    excluded_latest_op_time = shared_latest_time
    tv_by_time = rows_by_time(
        tv_rows,
        tv_time_column,
        drop_blank_rows=drop_blank_tv_rows,
        duplicate_times=duplicate_tv_times,
        invalid_rows=invalid_tv_rows,
    )
    op_by_time = rows_by_time(
        op_rows,
        op_time_column,
        duplicate_times=duplicate_op_times,
        invalid_rows=invalid_op_rows,
    )
    for ts in blank_tv_times:
        op_by_time.pop(ts, None)
    if excluded_latest_tv_time is not None:
        tv_by_time.pop(excluded_latest_tv_time, None)
    if excluded_latest_op_time is not None:
        op_by_time.pop(excluded_latest_op_time, None)
    common_times = sorted(set(tv_by_time) & set(op_by_time))

    total = 0
    mismatches = 0
    nan_mismatches = 0
    max_abs_delta = 0.0
    worst: dict[str, object] | None = None
    column_rows: list[dict[str, object]] = []

    for column in common_columns:
        col_total = 0
        col_mismatches = 0
        col_nan = 0
        col_max = 0.0
        deltas: list[float] = []
        first_bad: dict[str, object] | None = None
        for ts in common_times:
            tv_raw = tv_by_time[ts].get(column)
            op_raw = op_by_time[ts].get(column)
            tv_text = "" if tv_raw is None else str(tv_raw).strip()
            op_text = "" if op_raw is None else str(op_raw).strip()
            tv_blank = tv_text.lower() in {"", "na", "nan", "none", "null"}
            op_blank = op_text.lower() in {"", "na", "nan", "none", "null"}
            tv_num = _compare_csv_float(tv_raw)
            op_num = _compare_csv_float(op_raw)
            col_total += 1
            total += 1
            if tv_blank or op_blank:
                if tv_blank != op_blank:
                    col_nan += 1
                    nan_mismatches += 1
                equal = tv_blank and op_blank
                delta = _math.nan
                tv_value: object = None if tv_blank else tv_text
                op_value: object = None if op_blank else op_text
            elif not (_math.isnan(tv_num) or _math.isnan(op_num)):
                delta = abs(op_num - tv_num)
                equal = _math.isclose(tv_num, op_num, abs_tol=abs_tol, rel_tol=rel_tol)
                deltas.append(delta)
                col_max = max(col_max, delta)
                max_abs_delta = max(max_abs_delta, delta)
                tv_value = tv_num
                op_value = op_num
            else:
                delta = _math.nan
                tv_value = tv_text
                op_value = op_text
                equal = tv_text == op_text
            if not equal:
                col_mismatches += 1
                mismatches += 1
                bad = {
                    "time_ms": ts,
                    "column": column,
                    "tv": tv_value,
                    "openpine": op_value,
                    "abs_delta": None if _math.isnan(delta) else delta,
                }
                if first_bad is None:
                    first_bad = bad
                if worst is None or (
                    bad["abs_delta"] is not None
                    and bad["abs_delta"] > (worst.get("abs_delta") or -1)
                ):
                    worst = bad
        column_rows.append(
            {
                "column": column,
                "total": col_total,
                "mismatches": col_mismatches,
                "nan_mismatches": col_nan,
                "max_abs_delta": col_max,
                "mean_abs_delta": _statistics.fmean(deltas) if deltas else 0.0,
                "first_bad": first_bad,
            }
        )

    missing_times = set(tv_by_time) - set(op_by_time)
    extra_times = set(op_by_time) - set(tv_by_time)
    status = (
        "match"
        if total
        and mismatches == 0
        and not missing_times
        and not extra_times
        and not missing_column_names
        and (not require_exact_columns or not extra_column_names)
        and not duplicate_tv_times
        and not duplicate_op_times
        and not invalid_tv_rows
        and not invalid_op_rows
        else "mismatch"
    )
    classification: list[str] = []
    if not common_columns:
        classification.append("no_common_columns")
    if missing_column_names or (require_exact_columns and extra_column_names):
        classification.append("column_set_mismatch")
    if missing_times or extra_times:
        classification.append("time_window_mismatch")
    if duplicate_tv_times or duplicate_op_times:
        classification.append("duplicate_timestamps")
    if invalid_tv_rows or invalid_op_rows:
        classification.append("invalid_timestamps")
    if total == 0:
        classification.append("no_comparable_cells")
    elif mismatches:
        classification.append("value_mismatch")
    summary = {
        "status": status,
        "classification": "+".join(classification) if classification else "match",
        "tv_file": str(tv_path),
        "openpine_file": str(op_path),
        "tv_sha256": _sha256_compare_file(tv_path),
        "openpine_sha256": _sha256_compare_file(op_path),
        "closed_bars_only": closed_bars_only,
        "require_exact_columns": require_exact_columns,
        "excluded_latest_tv_time_ms": excluded_latest_tv_time,
        "excluded_latest_openpine_time_ms": excluded_latest_op_time,
        "tv_rows": len(tv_by_time),
        "openpine_rows": len(op_by_time),
        "duplicate_times_in_tv": sorted(duplicate_tv_times),
        "duplicate_times_in_openpine": sorted(duplicate_op_times),
        "invalid_time_rows_in_tv": len(invalid_tv_rows),
        "invalid_time_rows_in_openpine": len(invalid_op_rows),
        "ignored_blank_tv_rows": len(blank_tv_times),
        "common_times": len(common_times),
        "missing_times_in_openpine": len(missing_times),
        "extra_times_in_openpine": len(extra_times),
        "common_columns": len(common_columns),
        "missing_columns_in_openpine": len(missing_column_names),
        "extra_columns_in_openpine": len(extra_column_names),
        "missing_column_names_in_openpine": missing_column_names,
        "extra_column_names_in_openpine": extra_column_names,
        "total_cells": total,
        "mismatch_cells": mismatches,
        "mismatch_ratio": (mismatches / total) if total else None,
        "nan_mismatches": nan_mismatches,
        "max_abs_delta": max_abs_delta,
        "worst_column": (worst or {}).get("column"),
        "worst_time_ms": (worst or {}).get("time_ms"),
    }
    top_columns = sorted(
        [row for row in column_rows if row["mismatches"]],
        key=lambda row: (row["mismatches"], row["max_abs_delta"]),
        reverse=True,
    )[:20]
    return summary, top_columns


def _canonical_trade_order_rows(
    fields: list[str], rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    required = {"entry_time_ms", "exit_time_ms", "direction", "qty"}
    if not required.issubset(set(fields)):
        return rows

    def _num(row: dict[str, str], column: str, default: float = 0.0) -> float:
        value = _compare_csv_float(row.get(column))
        return default if math.isnan(value) else value

    def _ts(row: dict[str, str], column: str) -> int:
        return _compare_csv_time_ms(row.get(column)) or -1

    def _key(item: tuple[int, dict[str, str]]) -> tuple[object, ...]:
        index, row = item
        qty = abs(_num(row, "qty"))
        return (
            _ts(row, "entry_time_ms"),
            _ts(row, "exit_time_ms"),
            str(row.get("status") or ""),
            str(row.get("direction") or ""),
            _num(row, "entry_price"),
            -qty,
            _num(row, "exit_price"),
            _num(row, "net_profit"),
            index,
        )

    return [row for _index, row in sorted(enumerate(rows), key=_key)]


def _compare_rows_by_order(
    *,
    tv_path: Path,
    op_path: Path,
    exclude_columns: set[str],
    abs_tol: float,
    rel_tol: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    import math as _math
    import statistics as _statistics

    tv_fields, tv_rows = _read_compare_csv(tv_path)
    op_fields, op_rows = _read_compare_csv(op_path)
    tv_rows = _canonical_trade_order_rows(tv_fields, tv_rows)
    op_rows = _canonical_trade_order_rows(op_fields, op_rows)
    tv_value_fields = set(tv_fields) - exclude_columns
    op_value_fields = set(op_fields) - exclude_columns
    common_columns = sorted(tv_value_fields & op_value_fields)
    missing_column_names = sorted(tv_value_fields - op_value_fields)
    extra_column_names = sorted(op_value_fields - tv_value_fields)
    common_count = min(len(tv_rows), len(op_rows))
    total = 0
    mismatches = 0
    ignored_blank_tv_cells = 0
    max_abs_delta = 0.0
    worst: dict[str, object] | None = None
    column_rows: list[dict[str, object]] = []

    def _is_blank_tv_cell(value: object) -> bool:
        if value is None:
            return True
        text = str(value).strip().lower()
        return text in {"", "nan", "none", "null"}

    for column in common_columns:
        col_total = 0
        col_mismatches = 0
        col_max = 0.0
        deltas: list[float] = []
        first_bad: dict[str, object] | None = None
        for row_index in range(common_count):
            tv_raw = tv_rows[row_index].get(column)
            op_raw = op_rows[row_index].get(column)
            tv_blank = _is_blank_tv_cell(tv_raw)
            op_blank = _is_blank_tv_cell(op_raw)
            col_total += 1
            total += 1
            if tv_blank or op_blank:
                if tv_blank:
                    ignored_blank_tv_cells += 1
                tv_value = None if tv_blank else str(tv_raw).strip().lower()
                op_value = None if op_blank else str(op_raw).strip().lower()
                delta = None
                equal = tv_blank and op_blank
            else:
                tv_num = _compare_csv_float(tv_raw)
                op_num = _compare_csv_float(op_raw)
                both_numeric = not (_math.isnan(tv_num) or _math.isnan(op_num))
                if both_numeric:
                    delta = abs(op_num - tv_num)
                    equal = _math.isclose(tv_num, op_num, abs_tol=abs_tol, rel_tol=rel_tol)
                    deltas.append(delta)
                    col_max = max(col_max, delta)
                    max_abs_delta = max(max_abs_delta, delta)
                    tv_value = tv_num
                    op_value = op_num
                else:
                    tv_value = str(tv_raw).strip().lower()
                    op_value = str(op_raw).strip().lower()
                    delta = None
                    equal = tv_value == op_value
            if not equal:
                col_mismatches += 1
                mismatches += 1
                bad = {
                    "row": row_index,
                    "column": column,
                    "tv": tv_value,
                    "openpine": op_value,
                    "abs_delta": delta,
                }
                if first_bad is None:
                    first_bad = bad
                if worst is None or (
                    delta is not None and delta > (worst.get("abs_delta") or -1)
                ):
                    worst = bad
        column_rows.append(
            {
                "column": column,
                "total": col_total,
                "mismatches": col_mismatches,
                "max_abs_delta": col_max,
                "mean_abs_delta": _statistics.fmean(deltas) if deltas else 0.0,
                "first_bad": first_bad,
            }
        )

    classification: list[str] = []
    if not common_columns:
        classification.append("no_common_columns")
    if missing_column_names or extra_column_names:
        classification.append("column_set_mismatch")
    if len(tv_rows) != len(op_rows):
        classification.append("row_count_mismatch")
    if total == 0:
        classification.append("no_comparable_cells")
    elif mismatches:
        classification.append("value_mismatch")
    status = (
        "match"
        if total
        and mismatches == 0
        and len(tv_rows) == len(op_rows)
        and not missing_column_names
        and not extra_column_names
        else "mismatch"
    )
    summary = {
        "status": status,
        "classification": "+".join(classification) if classification else "match",
        "tv_file": str(tv_path),
        "openpine_file": str(op_path),
        "tv_sha256": _sha256_compare_file(tv_path),
        "openpine_sha256": _sha256_compare_file(op_path),
        "tv_rows": len(tv_rows),
        "openpine_rows": len(op_rows),
        "common_times": common_count,
        "missing_times_in_openpine": max(0, len(tv_rows) - len(op_rows)),
        "extra_times_in_openpine": max(0, len(op_rows) - len(tv_rows)),
        "common_columns": len(common_columns),
        "missing_columns_in_openpine": len(missing_column_names),
        "extra_columns_in_openpine": len(extra_column_names),
        "missing_column_names_in_openpine": missing_column_names,
        "extra_column_names_in_openpine": extra_column_names,
        "total_cells": total,
        "mismatch_cells": mismatches,
        "mismatch_ratio": (mismatches / total) if total else None,
        "nan_mismatches": 0,
        "ignored_blank_tv_cells": 0,
        "blank_tv_cells_compared": ignored_blank_tv_cells,
        "max_abs_delta": max_abs_delta,
        "worst_column": (worst or {}).get("column"),
        "worst_time_ms": (worst or {}).get("row"),
    }
    top_columns = sorted(
        [row for row in column_rows if row["mismatches"]],
        key=lambda row: (row["mismatches"], row["max_abs_delta"]),
        reverse=True,
    )[:20]
    return summary, top_columns


def _is_skippable_strategy_plot_summary(summary: dict[str, object]) -> bool:
    def _as_int(value: object) -> int:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    classification = str(summary.get("classification") or "")
    return (
        _as_int(summary.get("total_cells")) == 0
        and _as_int(summary.get("common_columns")) == 0
        and "no_common_columns" in classification
        and "no_comparable_cells" in classification
    )


def _write_strategy_tv_compare_report(
    output_path: Path, result: dict[str, object]
) -> None:
    import csv as _csv
    import json as _json

    output_path.mkdir(parents=True, exist_ok=True)
    comparisons = result["comparisons"]
    summary_csv = output_path / "comparison_summary.csv"
    fields = [
        "type",
        "status",
        "classification",
        "tv_rows",
        "openpine_rows",
        "common_times",
        "common_columns",
        "total_cells",
        "mismatch_cells",
        "mismatch_ratio",
        "max_abs_delta",
        "worst_column",
        "worst_time_ms",
        "tv_file",
        "openpine_file",
    ]
    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(comparisons)

    (output_path / "comparison_summary.json").write_text(
        _json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# OpenPine vs TradingView Run Comparison",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Strategy: `{result['strategy_id']}`",
        f"- Run: `{result['run_id']}`",
        f"- Abs tolerance: `{result['abs_tol']}`",
        f"- Rel tolerance: `{result['rel_tol']}`",
        f"- Status: `{result.get('status')}`",
        f"- Evidence complete: `{result.get('evidence_complete')}`",
        f"- Deterministic artifact hash: `{result.get('artifact_hash')}`",
        "",
        "## Summary",
        "",
    ]
    for row in comparisons:
        lines.append(
            f"- `{row['type']}` {row['status']} {row['classification']} "
            f"mismatch={row.get('mismatch_cells')}/{row.get('total_cells')} "
            f"max_delta={row.get('max_abs_delta')} worst_col=`{row.get('worst_column')}`"
        )
    (output_path / "comparison_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _comparison_artifact_hash(
    *,
    strategy_id: str,
    abs_tol: float,
    rel_tol: float,
    comparisons: list[dict[str, object]],
) -> str:
    stable_comparisons = [
        {
            key: value
            for key, value in sorted(summary.items())
            if key not in {"tv_file", "openpine_file"}
        }
        for summary in comparisons
    ]
    payload = {
        "strategy_id": strategy_id,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "comparisons": stable_comparisons,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _compare_strategy_run_with_tv_exports(
    *,
    strategy_id: str,
    run,
    exported: dict[str, str],
    output_path: Path,
    tv_chart: str | None,
    tv_trades: str | None,
    tv_equity: str | None,
    abs_tol: float,
    rel_tol: float,
    include_base_columns: bool,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
) -> dict[str, object]:
    exclude = (
        set()
        if include_base_columns
        else {
            "time",
            "bar_time",
            "bar_index",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "Volume",
        }
    )
    comparisons: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    if tv_chart:
        summary, top_columns = _compare_rows_by_time(
            tv_path=Path(tv_chart),
            op_path=Path(exported["plots"]),
            tv_time_column="time",
            op_time_column="bar_time",
            exclude_columns=exclude,
            abs_tol=abs_tol,
            rel_tol=rel_tol,
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
            drop_blank_tv_rows=True,
            closed_bars_only=True,
        )
        summary["type"] = "plots"
        if summary["status"] != "match" and _is_skippable_strategy_plot_summary(summary):
            summary["status"] = "skipped"
            summary["classification"] = "no_comparable_plot_cells"
        comparisons.append(summary)
        if summary["status"] != "match" and summary["status"] != "skipped":
            failures.append(
                {"type": "plots", "summary": summary, "top_columns": top_columns}
            )
    op_equity_path = exported.get("equity") or exported.get("equity_curve")
    if tv_equity and op_equity_path:
        summary, top_columns = _compare_rows_by_time(
            tv_path=Path(tv_equity),
            op_path=Path(op_equity_path),
            tv_time_column="time",
            op_time_column="bar_time_ms",
            exclude_columns=set(),
            abs_tol=abs_tol,
            rel_tol=rel_tol,
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
            closed_bars_only=True,
            require_exact_columns=False,
        )
        summary["type"] = "equity"
        comparisons.append(summary)
        if summary["status"] != "match":
            failures.append(
                {"type": "equity", "summary": summary, "top_columns": top_columns}
            )
    if "trades" in exported and (tv_chart or tv_trades):
        normalized_tv_trades = None
        if tv_chart and Path(tv_chart).exists():
            normalized_tv_trades = _write_chart_diagnostic_tv_trades(
                tv_chart_path=Path(tv_chart),
                output_path=output_path / "tradingview_trades_normalized.csv",
                compare_from_ms=compare_from_ms,
                compare_to_ms=compare_to_ms,
            )
        if normalized_tv_trades is None and tv_trades:
            normalized_tv_trades = _write_normalized_tv_trades(
                tv_path=Path(tv_trades),
                output_path=output_path / "tradingview_trades_normalized.csv",
                compare_from_ms=compare_from_ms,
                compare_to_ms=compare_to_ms,
            )
        if normalized_tv_trades is not None:
            summary, top_columns = _compare_rows_by_order(
                tv_path=normalized_tv_trades,
                op_path=Path(exported["trades"]),
                exclude_columns={
                    "trade_id",
                    "entry_signal",
                    "exit_signal",
                    "gross_profit",
                    "commission",
                },
                abs_tol=abs_tol,
                rel_tol=rel_tol,
            )
            summary["type"] = "trades"
            comparisons.append(summary)
            if summary["status"] != "match":
                failures.append(
                    {"type": "trades", "summary": summary, "top_columns": top_columns}
                )
    comparison_types = sorted(str(summary.get("type")) for summary in comparisons)
    required_types = {"plots", "trades", "equity"}
    evidence_complete = set(comparison_types) == required_types
    all_matched = bool(comparisons) and all(
        summary.get("status") == "match" for summary in comparisons
    )
    result = {
        "strategy_id": strategy_id,
        "run_id": run.run_id,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "status": (
            "match"
            if evidence_complete and all_matched and not failures
            else ("mismatch" if failures else "incomplete")
        ),
        "evidence_complete": evidence_complete,
        "comparison_types": comparison_types,
        "comparisons": comparisons,
        "failures": failures,
    }
    result["artifact_hash"] = _comparison_artifact_hash(
        strategy_id=strategy_id,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
        comparisons=comparisons,
    )
    _write_strategy_tv_compare_report(output_path, result)
    return result


__all__ = [
    "_compare_csv_float",
    "_compare_csv_time_ms",
    "_read_compare_csv",
    "_compare_normalized_header",
    "_find_compare_column",
    "_trade_action_and_direction",
    "_write_normalized_tv_trades",
    "_compare_rows_by_order",
    "_compare_rows_by_time",
    "_write_strategy_tv_compare_report",
    "_compare_strategy_run_with_tv_exports",
]
