"""Large bounded trading ledger with exact backup/resume equality and independent accounting."""

import argparse
import platform
import time
from dataclasses import asdict
from decimal import Decimal,localcontext,ROUND_HALF_EVEN
from pathlib import Path
import apsw
import duckdb
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,canonical
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.backup import backup,restore
from tools.checkpoint_benchmark import await_status

SOURCE='''//@version=6
strategy("Trading scale",default_qty_type=strategy.fixed,initial_capital=1000000,commission_value=0.1)
higher=request.security("TEST:TRADING","4",close)
if bar_index%2==0
    strategy.entry("L",strategy.long)
else
    strategy.close("L")
plot(strategy.equity,title="Equity")
plot(higher,title="Higher")
'''


def independent(count):
    with localcontext() as context:
        context.prec=80;fees=Decimal(0);gross=Decimal(0)
        for i in range(1,count):
            price=Decimal(100+i%13);fees+=(price/1000).quantize(Decimal('.00000001'),rounding=ROUND_HALF_EVEN)
            if i%2==0:gross+=price-Decimal(100+(i-1)%13)
        quantity=Decimal(1) if count%2==0 else Decimal(0)
        entry=Decimal(100+(count-1)%13) if quantity else Decimal(0)
        cash=Decimal(1000000)+gross-fees-entry*quantity
        equity=cash+quantity*(entry+Decimal('.5'))
        def text(n):return format(n.normalize(),'f') if n else '0'
        return {'cash':text(cash),'equity':text(equity),'position_size':text(quantity),
            'position_avg_price':text(entry) if quantity else None,'gross_realized':text(gross),
            'unrealized':'0.5' if quantity else '0','commission':text(fees),'orders':count,'fills':count-1,'trades':(count-1)//2}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=1000000);parser.add_argument('--pause-after',type=int,default=123457)
    args=parser.parse_args(argv)
    if platform.python_version()!='3.14.8' or args.directory.exists() or not 4<=args.bars<=10000000 or not 0<args.pause_after<args.bars:
        parser.error('Use Python 3.14.8, a fresh directory, 4..10000000 bars and an interior pause cursor.')
    root=args.directory;root.mkdir(parents=True);policy=ResourcePolicy(workers=1,total_memory_mb=512)
    report={'schema_version':'openpine.trading-acceptance.1','python':platform.python_version(),'sqlite':apsw.sqlitelibversion(),
        'duckdb':duckdb.__version__,'platform':platform.platform(),'execution_sha256':implementation_hash(),'build':build_identity(),
        'primary_bars':args.bars,'requested_bars':(args.bars+3)//4,'pause_after':args.pause_after}
    with local_manager(root/'live',policy=policy) as manager:
        started=time.monotonic()
        primary=manager.market.ingest({'context_id':'trading.primary','symbol':'TEST:TRADING','timeframe':'1','bar_schema':'openpine.historical-bars.2'},
            (HistoricalBar(i*60000,float(100+i%13)+.5,float(100+i%13),float(100+i%13)+.5,float(100+i%13),1.) for i in range(args.bars)),{'provider':'uploaded-file.1'})
        secondary=manager.market.ingest({'context_id':'trading.higher','symbol':'TEST:TRADING','timeframe':'4','bar_schema':'openpine.historical-bars.1'},
            (HistoricalBar(i*240000,float(100+i%17)) for i in range(report['requested_bars'])),{'provider':'uploaded-file.1'})
        report['ingest_seconds']=round(time.monotonic()-started,3)
        request={'schema_version':'openpine.job-request.4','input_ref':manager.freeze_input({'source':SOURCE,'source_name':'scale.pine'}),
            'snapshot_id':primary,'timeout_seconds':3600,'primary_context':asdict(MarketContext('trading.primary','TEST:TRADING','1','uploaded-file.1')),
            'contexts':[asdict(ContextBinding(MarketContext('trading.higher','TEST:TRADING','4','uploaded-file.1'),secondary))]}
        started=time.monotonic();baseline=manager.submit(request,checkpoint_interval_bars=0)['job_id']
        base=await_status(manager,baseline,'succeeded');expected=manager.summary(baseline)['result'];report['baseline_seconds']=round(time.monotonic()-started,3)
        assert expected['simulation']['statistics']==independent(args.bars)
        started=time.monotonic();job=manager.submit(request,checkpoint_interval_bars=0,pause_after=args.pause_after)['job_id']
        held=await_status(manager,job,'paused');report['pause_seconds']=round(time.monotonic()-started,3)
        checkpoint=manager.services.checkpoints.manifest(held['checkpoint_id'],plan=manager.plan(job))
        state=manager.objects.get(checkpoint['runtime_ref']);prefix=manager.objects.get(checkpoint['output_ref'])
        assert state['simulation']['broker']['count']==args.pause_after
        group=next(iter(state['runtime']['groups'].values()));assert group['cursor']==args.pause_after//4
        report.update(snapshot_ids=[primary,secondary],checkpoint_id=held['checkpoint_id'],job_id=job,
            requested_cursor_at_pause=group['cursor'],pending_orders_at_pause=len(state['simulation']['broker']['pending']),
            request_bytes=len(canonical(manager.request(job))),runtime_state_bytes=manager.objects.path(checkpoint['runtime_ref']).stat().st_size,
            launch_request_bytes=(manager.directory/'attempts'/job/held['attempt_id']/'request.json').stat().st_size,
            sealed_journal_blocks_at_pause=len(prefix['simulation']['files']))
        started=time.monotonic();backup(manager.db,manager.directory,root/'backup');report['backup_seconds']=round(time.monotonic()-started,3)
    started=time.monotonic();restore(root/'backup',root/'restored');report['restore_seconds']=round(time.monotonic()-started,3)
    with local_manager(root/'restored',policy=policy) as manager:
        started=time.monotonic();manager.resume(job);finished=await_status(manager,job,'succeeded');report['resume_seconds']=round(time.monotonic()-started,3)
        actual=manager.summary(job)['result'];assert actual==expected
        assert finished['resumed_from_bar']==args.pause_after and finished['attempt_count']==2
        assert actual['simulation']['statistics']==independent(args.bars)
        journal=actual['simulation']['journal'];last=manager.ledger(job,journal['records']-1,1)['events'][0]
        assert last['kind']=='mark' and last['value']==independent(args.bars) and last['bar_index']==args.bars-1
        assert manager.chart(job,args.bars-1,1)['outputs'][0]['values']==[float(last['value']['equity'])]
        manager.db.check()
        report.update(result_id=finished['result_id'],resumed_from_bar=finished['resumed_from_bar'],statistics=last['value'],
            output_rows=sum(p['rows'] for p in actual['series']),output_blocks=len(actual['series']),journal_records=journal['records'],journal_blocks=len(journal['parts']),
            peak_worker_rss_bytes=max(s.get('peak_resident_bytes',0) for s in (base,held,finished)),
            identical_summary_and_parquet=True,independent_accounting=True,joint_backup_resume=True)
    atomic_json(root/'report.json',report);print(report,flush=True);return 0


if __name__=='__main__':raise SystemExit(main())
