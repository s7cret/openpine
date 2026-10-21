"""Explicit acceptance: pause, back up, restore and resume a large stateful run."""

import argparse
import platform
import time
from pathlib import Path

import apsw
import duckdb

from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.application import TERMINAL_STATUSES
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,canonical
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.backup import backup,restore


SOURCE='''//@version=6
indicator("Checkpoint scale")
var float total=0
total:=total+nz(close)
plot(total,"total")
plot(ta.ema(close,3),"ema")
plot(ta.sma(close,3),"sma")
plot(close[1],"previous")
'''


def await_status(manager,job,status):
    started=last_update=time.monotonic()
    while time.monotonic()-started<3600:
        state=manager.get(job)
        if state["status"]==status:return state
        if state["status"] in TERMINAL_STATUSES or state["status"]=="paused":raise AssertionError(state)
        if manager.fatal:raise AssertionError(manager.fatal)
        if time.monotonic()-last_update>=10:
            print(f"{status}: {state['phase']} {state['completed']:,}/{state['total']:,}",flush=True)
            last_update=time.monotonic()
        time.sleep(.05)
    raise AssertionError("Checkpoint scale acceptance timed out.")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,required=True)
    parser.add_argument("--bars",type=int,default=1_000_000)
    parser.add_argument("--pause-after",type=int,default=123_457)
    args=parser.parse_args(argv)
    if (platform.python_version()!="3.14.8" or args.directory.exists()
            or not 3<=args.bars<=10_000_000 or not 1<=args.pause_after<args.bars):
        parser.error("Use Python 3.14.8, a new directory and 1 <= pause-after < bars <= 10000000 (bars >= 3).")
    args.directory.mkdir(parents=True)
    root=args.directory
    policy=ResourcePolicy(workers=1,total_memory_mb=512)
    report={"schema_version":"openpine.checkpoint-acceptance.1","python":platform.python_version(),
        "sqlite":apsw.sqlitelibversion(),"duckdb":duckdb.__version__,"platform":platform.platform(),
        "execution_sha256":implementation_hash(),"build":build_identity(),
        "bars":args.bars,"pause_after":args.pause_after,"outputs":4}
    with local_manager(root/"live",policy=policy) as manager:
        started=time.monotonic()
        snapshot=manager.market.ingest({"context_id":"checkpoint.scale","symbol":"TEST:CP","timeframe":"1",
            "bar_schema":"openpine.historical-bars.2"},
            (HistoricalBar(i*60000,float(i%97)) for i in range(args.bars)),{"provider":"checkpoint.fixed.1"})
        report["ingest_seconds"]=round(time.monotonic()-started,3)
        ref=manager.freeze_input({"source":SOURCE,"source_name":"checkpoint-scale.pine"})
        request={"schema_version":"openpine.job-request.2","input_ref":ref,"snapshot_id":snapshot,"timeout_seconds":3600}
        started=time.monotonic()
        baseline=manager.submit(request,checkpoint_interval_bars=0)["job_id"]
        base=await_status(manager,baseline,"succeeded")
        report["baseline_seconds"]=round(time.monotonic()-started,3)
        expected=manager.summary(baseline)["result"]
        started=time.monotonic()
        job=manager.submit(request,checkpoint_interval_bars=0,pause_after=args.pause_after)["job_id"]
        state=await_status(manager,job,"paused")
        report["pause_seconds"]=round(time.monotonic()-started,3)
        checkpoint=manager.services.checkpoints.manifest(state["checkpoint_id"],plan=manager.plan(job))
        assert checkpoint["completed"]==args.pause_after
        prefix=manager.objects.get(checkpoint["output_ref"])
        report.update(snapshot_id=snapshot,checkpoint_id=state["checkpoint_id"],job_id=job,
            request_bytes=len(canonical(manager.request(job))),
            launch_request_bytes=(manager.directory/"attempts"/job/state["attempt_id"]/"request.json").stat().st_size,
            runtime_state_bytes=manager.objects.path(checkpoint["runtime_ref"]).stat().st_size,
            output_prefix_bytes=manager.objects.path(checkpoint["output_ref"]).stat().st_size,
            sealed_prefix_blocks=len(prefix["files"]),pending_prefix_rows=prefix["pending"]["rows"] if prefix["pending"] else 0)
        started=time.monotonic()
        backup(manager.db,manager.directory,root/"backup")
        report["backup_seconds"]=round(time.monotonic()-started,3)
    started=time.monotonic();restore(root/"backup",root/"restored")
    report["restore_seconds"]=round(time.monotonic()-started,3)
    with local_manager(root/"restored",policy=policy) as manager:
        assert manager.get(job)["status"]=="paused"
        started=time.monotonic();manager.resume(job)
        resumed=await_status(manager,job,"succeeded")
        report["resume_seconds"]=round(time.monotonic()-started,3)
        actual=manager.summary(job)["result"]
        assert actual==expected, "Resumed summary/Parquet differs from uninterrupted run."
        assert resumed["resumed_from_bar"]==args.pause_after and resumed["attempt_count"]==2
        assert actual["observation_count"]==args.bars
        assert sum(p["rows"] for p in actual["series"])==args.bars*4
        total=float((args.bars//97)*sum(range(97))+sum(range(args.bars%97)))
        ema=None
        for i in range(args.bars):ema=float(i%97) if ema is None else .5*float(i%97)+.5*ema
        assert [o["last"] for o in actual["outputs"]]==[
            total,ema,sum(float(i%97) for i in range(args.bars-3,args.bars))/3,float((args.bars-2)%97)]
        chart=manager.chart(job,args.bars-2,2)
        assert chart["outputs"][0]["values"][-1]==total
        manager.db.check()
        report.update(resumed_from_bar=resumed["resumed_from_bar"],attempts=resumed["attempt_count"],
            peak_worker_rss_bytes=max(s.get("peak_resident_bytes",0) for s in (base,state,resumed)),
            observations=actual["observation_count"],output_rows=sum(p["rows"] for p in actual["series"]),
            output_blocks=len(actual["series"]),result_id=resumed["result_id"],
            identical_summary_and_parquet=True,independent_last_values=True,backup_resume=True)
    atomic_json(root/"report.json",report)
    print(report,flush=True)
    return 0


if __name__=="__main__":raise SystemExit(main())
