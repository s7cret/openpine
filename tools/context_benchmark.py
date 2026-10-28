from packages.storage.checkpoint_records import get as checkpoint_get
"""Large multi-timeframe acceptance with exact output equality after backup/resume."""

import argparse
import platform
import time
from dataclasses import asdict
from pathlib import Path

import apsw
import duckdb
from apps.openpine.bootstrap import local_manager
from packages.compiler.semantic.specification import ROOT
from packages.contracts import HistoricalBar
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,canonical
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.backup import backup,restore
from tools.checkpoint_benchmark import await_status


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,required=True);parser.add_argument("--bars",type=int,default=1000000)
    parser.add_argument("--pause-after",type=int,default=123457);args=parser.parse_args(argv)
    if (platform.python_version()!="3.14.8" or args.directory.exists() or not 12<=args.bars<=10000000
            or not 1<=args.pause_after<args.bars):parser.error("Use Python 3.14.8, a new directory and 1 <= pause-after < bars <= 10000000 (bars >= 12).")
    root=args.directory;root.mkdir(parents=True);policy=ResourcePolicy(workers=1,total_memory_mb=512)
    report={"schema_version":"openpine.context-acceptance.1","python":platform.python_version(),"sqlite":apsw.sqlitelibversion(),
        "duckdb":duckdb.__version__,"platform":platform.platform(),"execution_sha256":implementation_hash(),"build":build_identity(),
        "primary_bars":args.bars,"requested_bars":(args.bars+11)//12,"pause_after":args.pause_after}
    with local_manager(root/"live",policy=policy) as manager:
        snapshots=[];started=time.monotonic()
        for cid,tf,count,duration in (("mtf.primary","5",args.bars,300000),("mtf.hourly","60",report["requested_bars"],3600000)):
            snapshots.append(manager.market.ingest({"context_id":cid,"symbol":"TEST:MTF","timeframe":tf,"bar_schema":"openpine.historical-bars.1"},
                (HistoricalBar(i*duration,float(i%97)) for i in range(count)),{"provider":"uploaded-file.1"}))
        report["ingest_seconds"]=round(time.monotonic()-started,3)
        ref=manager.freeze_input({"source":(ROOT/"examples/contexts/v6.pine").read_text(encoding="utf-8"),"source_name":"v6.pine"})
        request={"schema_version":"openpine.job-request.4","input_ref":ref,"snapshot_id":snapshots[0],"timeout_seconds":3600,
            "primary_context":asdict(MarketContext("mtf.primary","TEST:MTF","5","uploaded-file.1")),
            "contexts":[asdict(ContextBinding(MarketContext("mtf.hourly","TEST:MTF","60","uploaded-file.1"),snapshots[1]))]}
        started=time.monotonic();baseline=manager.submit(request,checkpoint_interval_bars=0)["job_id"]
        base=await_status(manager,baseline,"succeeded");expected=manager.summary(baseline)["result"]
        report["baseline_seconds"]=round(time.monotonic()-started,3)
        started=time.monotonic();job=manager.submit(request,checkpoint_interval_bars=0,pause_after=args.pause_after)["job_id"]
        held=await_status(manager,job,"paused");report["pause_seconds"]=round(time.monotonic()-started,3)
        checkpoint=manager.services.checkpoints.manifest(held["checkpoint_id"],plan=manager.plan(job))
        state=checkpoint_get(manager.objects,checkpoint["runtime_ref"]);prefix=manager.objects.get(checkpoint["output_ref"])
        group=next(iter(state["groups"].values()));assert group["cursor"]==args.pause_after//12
        report.update(snapshot_ids=snapshots,checkpoint_id=held["checkpoint_id"],job_id=job,requested_cursor_at_pause=group["cursor"],
            request_bytes=len(canonical(manager.request(job))),
            launch_request_bytes=(manager.directory/"attempts"/job/held["attempt_id"]/"request.json").stat().st_size,
            runtime_state_bytes=manager.objects.path(checkpoint["runtime_ref"]).stat().st_size,
            sealed_prefix_blocks=len(prefix["files"]),pending_prefix_rows=prefix["pending"]["rows"] if prefix["pending"] else 0)
        started=time.monotonic();backup(manager.db,manager.directory,root/"backup");report["backup_seconds"]=round(time.monotonic()-started,3)
    started=time.monotonic();restore(root/"backup",root/"restored");report["restore_seconds"]=round(time.monotonic()-started,3)
    with local_manager(root/"restored",policy=policy) as manager:
        started=time.monotonic();manager.resume(job);finished=await_status(manager,job,"succeeded")
        report["resume_seconds"]=round(time.monotonic()-started,3)
        actual=manager.summary(job)["result"];assert actual==expected
        assert finished["resumed_from_bar"]==args.pause_after and finished["attempt_count"]==2
        count=args.bars//12;ema=None
        for i in range(count):ema=float(i%97) if ema is None else .5*float(i%97)+.5*ema
        last=float((count-1)%97)
        assert [o["last"] for o in actual["outputs"]]==[last,ema,last if args.bars%12==0 else None]
        assert sum(p["rows"] for p in actual["series"])==3*args.bars
        assert manager.chart(job,args.bars-2,2)["outputs"][0]["values"][-1]==last
        manager.db.check()
        report.update(result_id=finished["result_id"],resumed_from_bar=finished["resumed_from_bar"],
            output_rows=sum(p["rows"] for p in actual["series"]),output_blocks=len(actual["series"]),
            peak_worker_rss_bytes=max(s.get("peak_resident_bytes",0) for s in (base,held,finished)),
            identical_summary_and_parquet=True,independent_last_values=True,joint_backup_resume=True)
    atomic_json(root/"report.json",report);print(report,flush=True)
    return 0


if __name__=="__main__":raise SystemExit(main())
