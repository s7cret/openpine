"""Fetch a reusable immutable snapshot of historical public market data."""

import argparse
import sys
from pathlib import Path

from packages.contracts.marketdata import DataQuery
from packages.contracts.market import HistoryRequest, InstrumentKey
from packages.contracts.wire import atomic_json, canonical
from packages.execution.service import rejection
from packages.marketdata.providers import input_snapshot
from packages.contracts.wire import read_json
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("file", "binance"), required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--state-dir", "--store", dest="store", type=Path, default=Path(".runtime/jobs"))
    parser.add_argument("--gap-policy", choices=('fail','allow_with_metadata'), default='fail')
    parser.add_argument("--source-kind", choices=('rest','archive'), default='rest')
    parser.add_argument("--refresh", action='store_true')
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if (args.provider == "file") != (args.file is not None):
        parser.error("--file is required only for the file provider")
    if args.output and args.file and args.output.resolve() == args.file.resolve():
        parser.error("Output must not overwrite the source file")
    if args.output and args.output.resolve().is_relative_to(args.store.resolve()):
        parser.error('Export outside the owned state directory; catalog and objects must remain immutable.')
    try:
        with local_manager(args.store) as manager, HistoryJobs(manager) as service:
            query=DataQuery(args.symbol,args.timeframe,args.start_ms,args.end_ms)
            task=service.submit_file(input_snapshot(read_json(args.file,max_bytes=16*1024*1024)),query) if args.file else service.submit(
                HistoryRequest(InstrumentKey.from_pine(args.symbol),args.timeframe,args.start_ms,args.end_ms,
                    args.gap_policy,args.refresh,args.source_kind).payload())
            state=service.wait(task['task_id'])
            if state['status']!='succeeded':
                sys.stdout.write(canonical(state).decode('utf-8')+'\n');return 2
            snapshot_id=state['snapshot_id']
            if args.output: atomic_json(args.output,manager.market.export(snapshot_id))
            sys.stdout.write(canonical({'status':'fetched','sha256':snapshot_id,
                'bars':manager.market.manifest(snapshot_id)['identity']['count'],'task_id':task['task_id'],
                'coverage':state['coverage'],'path':str(args.output or manager.directory/'catalog.sqlite3')}).decode('utf-8')+'\n')
            return 0
    except (ValueError, OSError, UnicodeError) as error:
        try:
            payload = rejection(error)
        except ValueError:
            payload = {"status": "rejected", "diagnostics": [{"code": "INPUT_INVALID", "message": str(error)}]}
        sys.stdout.write(canonical(payload).decode("utf-8") + "\n")
        return 2
