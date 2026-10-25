"""Long-held percentage sizing, fixed per-order fees and OCA through recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from tools.trailing_soak import SOURCE as TRAILING,INSTRUMENT
from tools.economics_fixtures import RULES
from packages.contracts.wire import atomic_json
SOURCE=TRAILING.replace('Trailing worker recovery','Economic worker recovery').replace('default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1','default_qty_type=strategy.percent_of_equity,default_qty_value=20,initial_capital=1000,commission_type=strategy.commission.cash_per_order,commission_value=2').replace('strategy.order("A",strategy.long,qty=2,limit=100)','strategy.order("A",strategy.long,limit=100)')
def run(directory,bars=1000000):
 result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2,trading_rules=RULES)
 stats=result['million']['statistics']
 for key,want in {'position_size':'2','cash_exact':'786','equity_exact':'986','commission':'8','gross_realized_exact':'-6','unrealized_exact':'0',
  'entry_commission':'4','exit_commission':'4','allocated_entry_commission':'2.66666667','orders':10,'fills':4,'trades':2,'open_lots':1,'closed_lots':1,'closed_positions':0,
  'entries':[],'protections':[],'exit_states':[],'order_groups':[]}.items():
  if stats[key]!=want:raise AssertionError((key,stats[key],want))
 if stats['economics']['charged_count']!=4 or stats['economics']['retired_count']!=4 or stats['economics']['paid_order_ids']!=[]:raise AssertionError('Flat fee state differs.')
 result.update(schema_version='openpine.economics-soak.1',economics={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,'percentage_quantity_and_explicit_override':True,'pinned_trading_rules':RULES,
  'flat_fees_once_per_actual_order':True,'partial_fee_residue':'1.33333333','final_position':'2','final_cash':'786','final_equity':'986','final_commission':'8'})
 atomic_json(Path(directory)/'evidence.json',result);return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);a=p.parse_args();print(run(a.directory,a.bars))
if __name__=='__main__':main()
