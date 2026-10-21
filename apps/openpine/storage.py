"""Local storage administration, reusable market history and verified backups."""

import argparse
import csv
import apsw
from pathlib import Path

from packages.contracts import HistoricalBar
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import canonical, read_json
from packages.storage.lease import Lease
from packages.marketdata.binance import BinanceProvider
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup, restore
from packages.storage.candles import CandleStore
from packages.storage.database import Database
from packages.storage.objects import ObjectStore
from packages.storage.lifecycle import Lifecycle, quota


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "backup", "restore", "fetch", "import", "gc", "prune", "quota"):
        command = sub.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=Path(".runtime/jobs"))
        if name == "gc":
            command.add_argument("--apply", action="store_true")
            command.add_argument("--grace-seconds", type=float, default=3600)
        elif name == "prune":
            command.add_argument("--before", type=float, required=True, help="UTC Unix timestamp in seconds")
            command.add_argument("--expire-inputs", action="store_true")
            command.add_argument("--expire-coverage", action="store_true")
            command.add_argument("--apply", action="store_true")
        elif name == "quota":
            command.add_argument("--max-mb", type=int, required=True, help="published objects; 0 is unlimited")
        elif name == "backup":
            command.add_argument("--destination", type=Path, required=True)
        elif name == "restore":
            command.add_argument("--source", type=Path, required=True)
        elif name == "fetch":
            command.add_argument("--symbol", required=True)
            command.add_argument("--timeframe", required=True)
            command.add_argument("--start-ms", type=int, required=True)
            command.add_argument("--end-ms", type=int, required=True)
            command.add_argument("--refresh", action="store_true")
        elif name == "import":
            files = command.add_mutually_exclusive_group(required=True)
            files.add_argument("--bars", type=Path)
            files.add_argument("--csv", type=Path)
            command.add_argument("--symbol")
            command.add_argument("--timeframe")
            command.add_argument("--context-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            value = {"restored": str(restore(args.source, args.state_dir))}
        else:
            args.state_dir.mkdir(parents=True, exist_ok=True)
            lease = Lease(args.state_dir / ".manager.lock")
            db = None
            try:
                db = Database(args.state_dir / "catalog.sqlite3")
                objects = ObjectStore(args.state_dir / "objects", quota_bytes=quota(db))
                store = CandleStore(args.state_dir, db, objects=objects)
                lifecycle = Lifecycle(db,args.state_dir,objects)
                if args.command == "gc":
                    value = lifecycle.collect(apply=args.apply,grace_seconds=args.grace_seconds)
                elif args.command == "prune":
                    value = lifecycle.prune(args.before,expire_inputs=args.expire_inputs,
                        expire_coverage=args.expire_coverage,apply=args.apply)
                elif args.command == "quota":
                    lifecycle.set_quota(args.max_mb * 1024**2)
                    value = {"object_quota_bytes":objects.quota_bytes}
                elif args.command == "backup":
                    value = {"backup": str(backup(db, args.state_dir, args.destination))}
                elif args.command == "inspect":
                    db.check()
                    value = {"integrity": "ok", "sqlite": db.execute("SELECT sqlite_version()")[0][0],
                        "jobs": db.execute("SELECT count(*) FROM jobs")[0][0],
                        "datasets": db.execute("SELECT count(*) FROM datasets")[0][0],
                        "usage": lifecycle.inspect(),
                        "import_errors": db.execute("SELECT path,error FROM imports WHERE error IS NOT NULL")}
                elif args.command == "fetch":
                    snapshot_id = store.ensure(BinanceProvider(), DataQuery(args.symbol, args.timeframe,
                        args.start_ms, args.end_ms), refresh=args.refresh)
                    value = store.manifest(snapshot_id)
                elif args.bars:
                    value = store.manifest(store.save(input_snapshot(read_json(args.bars))))
                else:
                    if not all((args.symbol, args.timeframe, args.context_id)):
                        raise ValueError("CSV import requires symbol, timeframe and context-id.")

                    def bars():
                        with args.csv.open(encoding="utf-8", newline="") as stream:
                            reader = csv.DictReader(stream)
                            if len(reader.fieldnames or ()) != 6 or set(reader.fieldnames) != {"time", "open", "high", "low", "close", "volume"}:
                                raise ValueError("CSV requires exactly time,open,high,low,close,volume.")
                            for row in reader:
                                yield HistoricalBar(int(row["time"]), **{s: None if row[s] == "" else float(row[s])
                                    for s in ("open", "high", "low", "close", "volume")})
                    snapshot_id = store.ingest({"context_id": args.context_id, "symbol": args.symbol,
                        "timeframe": args.timeframe, "bar_schema": "openpine.historical-bars.2"}, bars(),
                        {"provider": "csv.1", "gap_policy": "preserve"})
                    value = store.manifest(snapshot_id)
            finally:
                if db:
                    db.close()
                lease.close()
        print(canonical(value).decode())
        return 0
    except (OSError, ValueError, apsw.Error) as error:
        print(canonical({"status": "rejected", "diagnostics": [{"code": "STORAGE_ERROR", "message": str(error)}]}).decode())
        return 2
