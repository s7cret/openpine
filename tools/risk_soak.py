"""Long-lived risk controls with context scheduling, actual crash and backup recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from tools.economics_soak import SOURCE as ECONOMIC,INSTRUMENT,RULES
from packages.contracts.wire import atomic_json
DECLARATIONS='''strategy.risk.allow_entry_in(strategy.direction.long)
strategy.risk.max_position_size(2)
strategy.risk.max_drawdown(10,strategy.cash)
strategy.risk.max_cons_loss_days(1000000)
strategy.risk.max_intraday_loss(100,strategy.cash)
strategy.risk.max_intraday_filled_orders(100)
'''
SOURCE=ECONOMIC.replace('Economic worker recovery','Risk worker recovery').replace('distance=input.int(12)',DECLARATIONS+'distance=input.int(12)').replace('strategy.order("A",strategy.long,limit=100)','strategy.entry("A",strategy.long,limit=100)')
def run(directory,bars=1000000):
 result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2,trading_rules=RULES);stats=result['million']['statistics']
 for name,want in {'cash_exact':'982','equity_exact':'982','commission':'8','gross_realized_exact':'-10','unrealized_exact':'0','position_size':'0',
  'entry_commission':'4','exit_commission':'4','allocated_entry_commission':'4','orders':10,'fills':4,'trades':2,'open_lots':0,'closed_lots':2,'closed_positions':1,
  'lots':[],'entries':[],'protections':[],'order_groups':[],'exit_states':[]}.items():
  if stats[name]!=want:raise AssertionError((name,stats[name],want))
 r=stats['risk']
 for name,want in {'peak':'1056','period_peak':'1056','period_start_net':'-4','filled_orders':2,'retired_orders':2,'observed_fills':4,'seen_orders':[],'loss_days':0,'trigger_count':1,'needs_close':False,'blocked':True}.items():
  if r[name]!=want:raise AssertionError((name,r[name],want))
 if r['halt']['rule']!='max_drawdown' or r['halt']['bar_index']!=bars-1 or r['halt']['basis']!='fill' or not r['halt']['permanent']:raise AssertionError('Risk stop boundary differs.')
 result.update(schema_version='openpine.risk-soak.1',risk={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,'six_static_rules':True,
  'net_order_bypasses_entry_cap':True,'known_open_peak':'1056','fill_boundary_equity':'990','drawdown':'66','forced_close_reference':'100',
  'final_equity':'982','final_commission':'8','final_position':'0'})
 atomic_json(Path(directory)/'evidence.json',result);return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);a=p.parse_args();print(run(a.directory,a.bars))
if __name__=='__main__':main()
