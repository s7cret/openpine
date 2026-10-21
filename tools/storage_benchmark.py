"""Explicit scale acceptance: streamed ingestion and real isolated Pine execution."""

import argparse
import platform
import time
from pathlib import Path

import apsw
import duckdb

from packages.contracts import HistoricalBar
from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import ResourcePolicy
from packages.contracts.marketdata import DataQuery
from packages.contracts.wire import atomic_json
from apps.openpine.bootstrap import local_manager as JobManager
from packages.execution.session import implementation_hash
from packages.storage.backup import backup, restore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=int, default=[100000, 1000000, 10000000])
    args = parser.parse_args(argv)
    if platform.python_version() != "3.14.8" or args.directory.exists():
        parser.error("Use Python 3.14.8 and a new output directory.")
    args.directory.mkdir(parents=True)
    report = {"python": platform.python_version(), "sqlite": apsw.sqlitelibversion(),
        "duckdb": duckdb.__version__, "platform": platform.platform(),
        "implementation_sha256": implementation_hash(), "cases": []}
    metadata = {"context_id": "scale.fixed", "symbol": "TEST:SCALE", "timeframe": "1",
        "bar_schema": "openpine.historical-bars.2"}
    for size in args.sizes:
        print(f"Ingesting {size:,} bars", flush=True)
        root = args.directory / str(size)
        workers = 2 if size == min(args.sizes) else 1
        policy = ResourcePolicy(workers=workers, total_memory_mb=workers*512)
        with JobManager(root, policy=policy) as manager:
            def bars():
                for index in range(size):
                    value = float(index % 100 + 1)
                    yield HistoricalBar(index*60000, value, value, value+1, value-1, 1.)

            started = time.monotonic()
            snapshot = manager.market.ingest(metadata, bars(), {"provider": "scale.fixed.1"})
            ingest_seconds = time.monotonic() - started
            content = manager.objects.put({"source": '//@version=6\nindicator("Storage scale")\nplot(close)\n',
                "source_name": "scale.pine"})
            request = {"schema_version": "openpine.job-request.2", "input_ref": content,
                "snapshot_id": snapshot, "timeout_seconds": 3600}
            jobs = [manager.submit(request)["job_id"] for _ in range(workers)]
            started, last_update, concurrency = time.monotonic(), 0, 0
            while True:
                states = [manager.get(job) for job in jobs]
                concurrency = max(concurrency, len(manager.children))
                if all(state["status"] in TERMINAL_STATUSES for state in states):
                    break
                if time.monotonic() - last_update >= 10:
                    print(f"{size:,}: " + ", ".join(f"{s['status']} {s['completed']:,}/{s['total']:,}" for s in states), flush=True)
                    last_update = time.monotonic()
                time.sleep(.1)
            execution_seconds = time.monotonic() - started
            for state in states:
                if state["status"] != "succeeded":
                    raise AssertionError(state)
                summary = manager.summary(state["job_id"])["result"]
                assert summary["observation_count"] == size
                assert summary["outputs"][0]["count"] == size
                assert summary["outputs"][0]["last"] == float((size-1) % 100 + 1)
                assert sum(p["rows"] for p in summary["series"]) == size
            started = time.monotonic()
            chart = manager.chart(jobs[0], max(0, size-2000), 2000)
            chart_seconds = time.monotonic() - started
            assert len(chart["bars"]) == min(2000, size)
            assert chart["bars"][-1]["time"] == (size-1)*60000
            assert chart["outputs"][0]["values"][-1] == float((size-1) % 100 + 1)
            case = {"bars": size, "jobs": jobs, "ingest_seconds": round(ingest_seconds,3),
                "execution_seconds": round(execution_seconds,3), "last_window_seconds": round(chart_seconds,3),
                "concurrent_processes": concurrency, "peak_worker_rss_bytes": max(s.get("peak_resident_bytes",0) for s in states),
                "snapshot_id": snapshot, "result_ids": [s["result_id"] for s in states],
                "parquet_blocks": len(manager.market.manifest(snapshot)["parts"]),
                "request_bytes": len(__import__('json').dumps(manager.request(jobs[0])).encode())}
            query = DataQuery(metadata["symbol"],metadata["timeframe"],0,size*60000)
            manager.market.cover("scale.fixed.1",query,query.start_ms,query.end_ms,snapshot)
            started=time.monotonic()
            selected=manager.market.select(query,namespace="scale.fixed.1")
            case["selection_first_seconds"]=round(time.monotonic()-started,4)
            started=time.monotonic()
            assert manager.market.select(query,namespace="scale.fixed.1") == selected
            case["selection_cached_seconds"]=round(time.monotonic()-started,4)
            if size == max(args.sizes):
                started = time.monotonic()
                backup(manager.db, root, args.directory / "backup")
                restore(args.directory / "backup", args.directory / "restored")
                with JobManager(args.directory / "restored", max_workers=1) as recovered:
                    assert recovered.summary(jobs[0]) == manager.summary(jobs[0])
                    assert recovered.chart(jobs[0], size-2, 2)["outputs"][0]["values"] == [99.,100.]
                case["backup_restore_seconds"] = round(time.monotonic()-started,3)
            manager.db.check()
            report["cases"].append(case)
            atomic_json(args.directory / "report.json", report)
            print(case, flush=True)
    print("Scale acceptance passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
