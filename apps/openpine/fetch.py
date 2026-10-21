"""Fetch a reusable immutable snapshot of historical public market data."""

import argparse
import sys
from pathlib import Path

from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import atomic_json, canonical
from packages.execution.service import rejection
from packages.marketdata.binance import BinanceProvider
from packages.marketdata.providers import FileProvider, SnapshotStore, snapshot_payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("file", "binance"), required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--store", type=Path, default=Path(".runtime/snapshots"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if (args.provider == "file") != (args.file is not None):
        parser.error("--file is required only for the file provider")
    if args.output and args.file and args.output.resolve() == args.file.resolve():
        parser.error("Output must not overwrite the source file")
    try:
        provider = FileProvider(args.file) if args.file else BinanceProvider()
        snapshot = provider.fetch(DataQuery(args.symbol, args.timeframe, args.start_ms, args.end_ms))
        path = SnapshotStore(args.store).save(snapshot)
        if args.output:
            atomic_json(args.output, snapshot_payload(snapshot))
        sys.stdout.write(canonical({"status": "fetched", "sha256": snapshot.sha256,
            "bars": len(snapshot.data.bars), "path": str(path)}).decode("utf-8") + "\n")
        return 0
    except (ValueError, OSError, UnicodeError) as error:
        try:
            payload = rejection(error)
        except ValueError:
            payload = {"status": "rejected", "diagnostics": [{"code": "INPUT_INVALID", "message": str(error)}]}
        sys.stdout.write(canonical(payload).decode("utf-8") + "\n")
        return 2
