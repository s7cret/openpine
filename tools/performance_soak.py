"""Long histories with many closed trades, indexed reads, crash and backup recovery."""
import argparse
from decimal import Decimal
from pathlib import Path
from tools.launch_soak import run as prepared_run
from tools.performance_fixtures import INSTRUMENT
from tools.risk_fixtures import RULES
from packages.contracts.wire import atomic_json
SOURCE='''//@version=6
strategy("Trade history worker recovery",default_qty_type=strategy.fixed,default_qty_value=1,initial_capital=1000000,commission_type=strategy.commission.cash_per_order,commission_value=1)
if bar_index%4==0
    strategy.entry("L",strategy.long)
if bar_index%4==1
    strategy.close("L")
plot(strategy.closedtrades,title="Closed")
plot(strategy.netprofit,title="Net")
plot(strategy.closedtrades.profit(strategy.closedtrades-1),title="Last")
plot(strategy.closedtrades.profit(0),title="First")
plot(request.security("TEST:LOAD","4",close),title="Higher")
'''
def run(directory,bars=1000000):
 result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=bars//4,trading_rules=RULES);stats=result['million']['statistics'];closed=bars//4;net=-2*closed
 for name,want in {'cash_exact':str(1000000+net),'equity_exact':str(1000000+net),'commission':str(2*closed),'gross_realized_exact':'0','position_size':'0','orders':2*closed,'fills':2*closed,'trades':closed,'open_lots':0,'closed_lots':closed,'closed_positions':closed}.items():
  if stats[name]!=want:raise AssertionError((name,stats[name],want))
 performance=stats['performance']
 for name,want in {'wins':0,'losses':closed,'evens':0,'netprofit':str(net),'grossprofit':'0','grossloss':str(-net),'closed_commission':str(-net)}.items():
  if performance[name]!=want:raise AssertionError((name,performance[name],want))
 if performance['archive']['count']!=closed:raise AssertionError('Closed history count differs.')
 result.update(schema_version='openpine.performance-soak.1',performance={'primary_bars':bars,'higher_bars':bars//4,'closed_trades':closed,'outputs':5,'netprofit':str(net),'fee_per_trade':'2','oldest_and_latest_reads':True,'history_rebuilt_after_worker_crash_and_backup':True})
 atomic_json(Path(directory)/'evidence.json',result);return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);a=p.parse_args();print(run(a.directory,a.bars))
if __name__=='__main__':main()
