"""Long-lived per-lot trailing and pending OCA through real worker recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Trailing worker recovery",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1,slippage=2)
distance=input.int(12)
higher=request.security("TEST:LOAD","4",close)
if bar_index==3 and higher>0
    strategy.order("A",strategy.long,qty=2,limit=100)
    strategy.exit("PA","A",trail_points=1,trail_offset=distance*syminfo.mintick,oca_name="protect")
if bar_index==4
    strategy.order("B",strategy.long,qty=3,limit=100)
    strategy.exit("PB","B",loss=25,trail_price=101,trail_offset=distance,oca_name="protect")
if bar_index==5
    strategy.order("C",strategy.long,qty=4,stop=101,limit=95,oca_name="entries",oca_type=strategy.oca.cancel)
    strategy.order("D",strategy.long,qty=7,limit=90,oca_name="entries",oca_type=strategy.oca.cancel)
    strategy.exit("PC1","C",profit=12,qty=2,oca_name="profit")
    strategy.exit("PC2","C",trail_points=1,trail_offset=distance,qty_percent=100,oca_name="profit")
    strategy.exit("PD","D",profit=20,loss=30)
if barstate.islast
    strategy.cancel_all()
    strategy.close_all()
plot(strategy.position_size,title="Position")
plot(strategy.equity,title="Equity")
plot(strategy.position_avg_price,title="Entry price")
plot(ta.cum(volume),title="Volume")
plot(bar_index,title="Index")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2)
    stats=result['million']['statistics']
    for name,want in {'position_size':'2','cash_exact':'786.06','equity_exact':'986.06','commission':'7.94',
                      'gross_realized_exact':'-6','unrealized_exact':'0','entry_commission':'5','exit_commission':'2.94',
                      'allocated_entry_commission':'3','orders':10,'fills':4,'trades':2,'open_lots':1,'closed_lots':1,
                      'closed_positions':0,'entries':[],'protections':[],'exit_states':[],'order_groups':[]}.items():
        if stats[name]!=want:raise AssertionError((name,stats[name],want))
    result.update(schema_version='openpine.trailing-soak.1',trailing={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'per_lot_activation_and_pending_targets':True,'open_ratchet_reduce_and_cancel':True,
        'final_position':'2','final_cash':'786.06','final_equity':'986.06','final_commission':'7.94'})
    atomic_json(Path(directory)/'evidence.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))
if __name__=='__main__':main()
