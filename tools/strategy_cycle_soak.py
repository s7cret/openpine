"""Bounded durable multi-pass strategy stream with independent quantity/fee arithmetic."""
import argparse,json,time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts.contexts import MarketContext
from packages.contracts.strategy_cycle import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import atomic_json
from packages.execution.identity import implementation_hash,build_identity

SOURCE='''//@version=6
strategy("Intrabar stream",default_qty_type=strategy.fixed,initial_capital=10000000,commission_value=1,calc_on_every_tick=true,calc_on_order_fills=true,process_orders_on_close=true)
varip passes=0
passes:=passes+1
if close==101 and strategy.position_size==0
    strategy.entry("L",strategy.long)
if strategy.position_size>0
    strategy.close("L")
plot(passes)
plot(strategy.netprofit)
plot(strategy.closedtrades)
'''

def stream(size):
    prices=(100.,94.,112.,101.)
    for seq in range(size):
        index,revision=divmod(seq,4);t=index*60000;price=prices[revision]
        kind=('open','update','update','close')[revision]
        yield MarketEvent(seq,'primary',kind,t,t+(60000 if revision==3 else revision*10000),revision,100.,max(prices[:revision+1]),min(prices[:revision+1]),price,float(revision+1))

def run(directory,events):
    if events<8 or events%4:raise ValueError('Use a multiple of four events, at least eight.')
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a new soak directory.')
    started=time.monotonic();frozen=implementation_hash();build=build_identity()
    with local_manager(root/'jobs') as manager:
        service=Replays(manager);context=MarketContext('primary','TEST:SAMPLED','1','original-observations.1')
        state=service.create({'input_ref':manager.freeze_input({'source':SOURCE,'source_name':'cycle-soak.pine'}),'contexts':[asdict(context)],
            'primary_id':'primary','broker_settings':asdict(BrokerSettings(tick_size='1')),'timeout_seconds':3600,'calculation_profile':PROFILE})
        identifier=state['replay_id'];batch=[]
        for e in stream(events):
            batch.append(e.payload())
            if len(batch)==10000:service.append(identifier,batch);batch=[]
        if batch:service.append(identifier,batch)
        # Cut inside a candle, then rebuild the disk index in a fresh worker.
        cut=events//2+2
        service.advance(identifier,max_events=cut,checkpoint_every=10000);prefix=service.status(identifier)
        assert prefix['simulation']['observed_execution']['bar_confirmed'] is False
        first_metrics=dict(service.last_metrics)
        final=service.advance(identifier,checkpoint_every=10000);last_metrics=dict(service.last_metrics)
        stats=final['simulation']['statistics'];bars=events//4;trades=bars-1
        expected=Decimal('10000000')-Decimal('3.01')*trades-Decimal('1.01')
        assert stats['equity']==format(expected.normalize(),'f') and stats['position_size']=='1'
        assert stats['fills']==trades*2+1 and stats['trades']==trades and final['confirmed_bars']==bars
        assert final['simulation']['journal']['closed_records']==trades and final['outputs'][2]['last']==trades and final['outputs'][0]['last']==events*3//2-1
        assert final['calculation']['observations']==events and final['calculation']['phase_counts']=={'regular':events,'after_fill':trades,'after_close_fill':bars}
        assert implementation_hash()==frozen and build_identity()==build
        report={'status':'passed','events':events,'confirmed_bars':bars,'closed_trades':trades,'expected_equity':str(expected),
            'statistics':stats,'observed_cursor':final['simulation']['observed_execution'],'journal_records':final['simulation']['journal']['records'],
            'journal_parts':len(final['simulation']['journal']['parts']),'independent_profit_per_cycle':'-1','independent_fee_per_cycle':'2.01','open_entry_commission':'1.01',
            'resumed_inside_candle':True,'prefix_calculation':prefix['calculation'],'final_calculation':final['calculation'],'attempts':[first_metrics,last_metrics],'seconds':round(time.monotonic()-started,3),
            'implementation_sha256':frozen,'build':build}
    atomic_json(root/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--events',type=int,default=100000);args=p.parse_args(argv)
    v=run(args.directory,args.events);print(json.dumps({k:x for k,x in v.items() if k not in ('statistics','observed_cursor')}));return 0
if __name__=='__main__':raise SystemExit(main())
