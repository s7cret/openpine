#!/usr/bin/env python3
"""CLI-only TradingView export batch runner for OpenPine.

This script intentionally orchestrates only `openpine ...` subprocess commands.
It does not import OpenPine runtime/compiler libraries.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import time
from pathlib import Path


RUN_ID_RE = re.compile(r"Backtest saved:\s*(run_[A-Za-z0-9_]+)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="TV batch root containing exported/")
    parser.add_argument("--output", required=True, help="Normalized OpenPine output directory")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="spot")
    parser.add_argument("--from", dest="from_date", default="2017-01-01")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--skip-backfill", action="store_true")
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    exported = root / "exported"
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "batch_summary.jsonl"

    folders = sorted(p for p in exported.iterdir() if p.is_dir())
    if args.case:
        wanted = set(args.case)
        folders = [p for p in folders if p.name in wanted]
    if args.limit is not None:
        folders = folders[: args.limit]

    if not args.skip_backfill:
        run_command(
            [
                "openpine",
                "data",
                "backfill",
                args.symbol,
                args.timeframe,
                "--exchange",
                args.exchange,
                "--market",
                args.market_type,
                "--from",
                args.from_date,
                "--wait",
            ],
            output / "_backfill.log",
            dry_run=args.dry_run,
        )

    with summary_path.open("a", encoding="utf-8") as summary:
        for folder in folders:
            record = process_case(folder, output, args)
            summary.write(json.dumps(record, default=str) + "\n")
            summary.flush()
            print(json.dumps(record, default=str), flush=True)

    return 0


def process_case(folder: Path, output_root: Path, args: argparse.Namespace) -> dict:
    case_t0 = time.perf_counter()
    case = folder.name
    case_output = output_root / case
    case_output.mkdir(parents=True, exist_ok=True)
    log_path = case_output / "command_log.txt"
    log_path.write_text("", encoding="utf-8")
    timings: dict[str, float] = {}
    record: dict = {"case": case, "status": "ok", "output": str(case_output)}

    try:
        pine_path = find_pine(folder)
        source = pine_path.read_text(encoding="utf-8", errors="ignore")
        kind = "strategy" if re.search(r"\bstrategy\s*\(", source) else "indicator"
        compare_from, compare_to = infer_tv_window(folder)
        pine_name = f"tvbatch_{case}"

        record.update(
            {
                "kind": kind,
                "pine_path": str(pine_path),
                "pine_name": pine_name,
                "compare_from": compare_from,
                "compare_to": compare_to,
            }
        )

        timed(
            timings,
            "pine_add_sec",
            run_command,
            ["openpine", "pine", "show", pine_name],
            log_path,
            dry_run=args.dry_run,
            allow_fail=True,
        )
        show_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
        if "Pine source:" not in show_text:
            timed(
                timings,
                "pine_add_sec",
                run_command,
                ["openpine", "pine", "pine-add", pine_name, str(pine_path)],
                log_path,
                dry_run=args.dry_run,
            )

        timed(
            timings,
            "compile_sec",
            run_command,
            ["openpine", "pine", "pine-compile", pine_name, "--force"],
            log_path,
            dry_run=args.dry_run,
        )

        if kind == "indicator":
            command = [
                "openpine",
                "pine",
                "run-plots",
                pine_name,
                "--symbol",
                args.symbol,
                "--timeframe",
                args.timeframe,
                "--exchange",
                args.exchange,
                "--market-type",
                args.market_type,
                "--from",
                args.from_date,
                "--output",
                str(case_output),
                "--progress-every",
                str(args.progress_every),
            ]
            if compare_from is not None:
                command += ["--compare-from", str(compare_from)]
            if compare_to is not None:
                command += ["--compare-to", str(compare_to)]
            timed(timings, "run_plots_sec", run_command, command, log_path, dry_run=args.dry_run)
        else:
            strategy_name = f"{pine_name}_{args.symbol}_{args.timeframe}"
            timed(
                timings,
                "strategy_create_sec",
                run_command,
                [
                    "openpine",
                    "strategy",
                    "create",
                    strategy_name,
                    "--pine",
                    pine_name,
                    "--symbol",
                    args.symbol,
                    "--timeframe",
                    args.timeframe,
                    "--exchange",
                    args.exchange,
                    "--market-type",
                    args.market_type,
                    "--mode",
                    "backtest",
                ],
                log_path,
                dry_run=args.dry_run,
                allow_fail=True,
            )
            strategy_id = find_strategy_id(log_path)
            if not strategy_id:
                strategy_id = find_strategy_by_name(strategy_name, log_path, dry_run=args.dry_run)
            if not strategy_id:
                raise RuntimeError(f"Cannot resolve strategy id for {strategy_name}")

            text = timed(
                timings,
                "backtest_sec",
                run_command,
                [
                    "openpine",
                    "strategy",
                    "backtest",
                    strategy_id,
                    "--from",
                    args.from_date,
                    "--capture-plots",
                ],
                log_path,
                dry_run=args.dry_run,
            )
            match = RUN_ID_RE.search(text)
            if args.dry_run:
                run_id = "dry_run"
            elif not match:
                raise RuntimeError("Cannot parse run_id from strategy backtest output")
            else:
                run_id = match.group(1)
            export_command = [
                "openpine",
                "strategy",
                "export-run",
                strategy_id,
                "--run-id",
                run_id,
                "--output",
                str(case_output),
            ]
            if compare_from is not None:
                export_command += ["--compare-from", str(compare_from)]
            if compare_to is not None:
                export_command += ["--compare-to", str(compare_to)]
            timed(timings, "export_sec", run_command, export_command, log_path, dry_run=args.dry_run)
            record["strategy_id"] = strategy_id
            record["run_id"] = run_id

        timings["total_sec"] = time.perf_counter() - case_t0
        record["timings"] = timings
        return record
    except Exception as exc:
        timings["total_sec"] = time.perf_counter() - case_t0
        record.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "timings": timings})
        return record


def run_command(
    command: list[str],
    log_path: Path,
    *,
    dry_run: bool = False,
    allow_fail: bool = False,
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + " ".join(command) + "\n")
        if dry_run:
            log.write("[dry-run]\n")
            return ""
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log.write(proc.stdout)
        if proc.returncode and not allow_fail:
            raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(command)}")
        return proc.stdout


def timed(timings: dict[str, float], key: str, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    timings[key] = timings.get(key, 0.0) + (time.perf_counter() - t0)
    return result


def find_pine(folder: Path) -> Path:
    source = folder / "source.pine"
    if source.exists():
        return source
    matches = sorted(folder.glob("*.pine"))
    if not matches:
        raise FileNotFoundError(f"No .pine file in {folder}")
    return matches[0]


def infer_tv_window(folder: Path) -> tuple[int | None, int | None]:
    charts = sorted(folder.glob("tv_*_chart.csv"))
    if not charts:
        return None, None
    first_time = None
    last_time = None
    with charts[0].open(newline="", encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            value = row.get("time")
            if not value:
                continue
            ts = int(float(value))
            if ts < 10_000_000_000:
                ts *= 1000
            if first_time is None:
                first_time = ts
            last_time = ts
    return first_time, last_time


def find_strategy_id(log_path: Path) -> str | None:
    text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    match = re.search(r"Strategy created:\s*(strat_[A-Za-z0-9_]+)", text)
    return match.group(1) if match else None


def find_strategy_by_name(name: str, log_path: Path, *, dry_run: bool) -> str | None:
    text = run_command(["openpine", "strategy", "list", "--json"], log_path, dry_run=dry_run)
    if dry_run:
        return "dry_run_strategy"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    for item in payload:
        if item.get("name") == name:
            return item.get("strategy_id")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
