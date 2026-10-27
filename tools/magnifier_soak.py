"""Native Parquet-to-worker growth check with independent fees and pass counts."""
import argparse,json,time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.wire import atomic_json
from packages.execution.identity import implementation_hash,build_identity
from tools.magnifier_fixtures import configuration

SOURCE='''//@version=6
strategy("Magnifier growth",default_qty_type=strategy.fixed,initial_capital=1000000000,commission_value=1,use_bar_magnifier=true,calc_on_order_fills=true)
if bar_index % 2 == 0
    strategy.entry("L",strategy.long)
else
    strategy.close("L")
plot(strategy.equity)
'''
def candles(n,lower=False):
    for i in range(n*(2 if lower else 1)):
        parent=i//2 if lower else i;p=100.+10.*(parent%2)
        yield HistoricalBar(i*(60000 if lower else 120000),p,p,p,p,1. if lower else 2.)
def run(directory,model_points):
    if type(model_points) is not int or model_points<16 or model_points%16:raise ValueError('Use a positive multiple of sixteen model points.')
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a fresh proof directory.')
    n=model_points//8;started=time.monotonic();frozen=implementation_hash();build=build_identity()
    with local_manager(root/'jobs') as manager:
        meta={'symbol':'TEST:GROWTH','bar_schema':'openpine.historical-bars.2'}
        parent=manager.market.ingest({**meta,'context_id':'primary','timeframe':'2'},candles(n),{'provider':'original-growth.1'})
        lower=manager.market.ingest({**meta,'context_id':'lower','timeframe':'1'},candles(n,True),{'provider':'original-growth.1'})
        request={'schema_version':'openpine.job-request.8','input_ref':manager.freeze_input({'source':SOURCE,'source_name':'magnifier-growth.pine'}),
            'snapshot_id':parent,'inputs':{},'timeout_seconds':3600,'magnifier':{**configuration(lower),'checkpoint_every_children':10000}}
        key=manager.submit(request,checkpoint_interval_bars=10000,pause_after=n//2)['job_id']
        def wait(wanted):
            deadline=time.monotonic()+3600
            while time.monotonic()<deadline:
                state=manager.get(key)
                if state['status']==wanted:return state
                if state['status'] in ('failed','timed_out','cancelled','succeeded','paused'):raise AssertionError(state)
                time.sleep(.1)
            raise TimeoutError(state)
        prefix=wait('paused');manager.resume(key);final=wait('succeeded');result=manager.summary(key)['result'];simulation=result['simulation']
        stats=simulation['statistics'];expected=Decimal('1000000000')-Decimal('1.1')*n
        assert stats['equity']==format(expected.normalize(),'f') and stats['position_size']=='0'
        assert stats['fills']==n and stats['trades']==n//2 and result['observation_count']==n
        assert simulation['calculation_cycle']['phase_counts']=={'regular':n,'after_fill':n,'after_close_fill':0}
        assert simulation['bar_magnifier']['model_points']==model_points and simulation['bar_magnifier']['covered_bars']==n
        assert simulation['bar_magnifier']['fallback_bars']==0 and simulation['bar_magnifier']['lower_bars']==n*2
        assert final['peak_resident_bytes']<330*1024**2,final
        assert implementation_hash()==frozen and build_identity()==build
        report={'status':'passed','model_points':model_points,'parent_bars':n,'lower_bars':n*2,'closed_trades':n//2,
            'expected_equity':format(expected.normalize(),'f'),'fee_per_closed_position':'2.2','strategy_passes':n*2,
            'peak_resident_bytes':final['peak_resident_bytes'],'resumed_from_bar':final['resumed_from_bar'],
            'journal_records':simulation['journal']['records'],'journal_parts':len(simulation['journal']['parts']),
            'bar_magnifier':simulation['bar_magnifier'],'statistics':stats,'seconds':round(time.monotonic()-started,3),
            'implementation_sha256':frozen,'build':build}
    atomic_json(root/'report.json',report);return report
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--model-points',type=int,default=100000);args=p.parse_args(argv)
    value=run(args.directory,args.model_points);print(json.dumps({k:v for k,v in value.items() if k not in ('statistics','bar_magnifier')}));return 0
if __name__=='__main__':raise SystemExit(main())
