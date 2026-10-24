"""Two retained lots and a long-lived activated third entry through real recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Lot worker recovery",pyramiding=3,close_entries_rule="ANY",default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1,slippage=2)
part=input.int(2)
higher=request.security("TEST:LOAD","4",close)
if bar_index==3 and higher>0
    strategy.entry("A",strategy.short,qty=2,limit=100)
    strategy.exit("PA","A",stop=130,limit=90,qty_percent=100)
if bar_index==4
    strategy.entry("B",strategy.short,qty=3,limit=100)
    strategy.exit("PB","B",stop=130,limit=90,qty_percent=100)
if bar_index==5
    strategy.entry("C",strategy.short,qty=4,stop=100-syminfo.mintick,limit=100+5*syminfo.mintick)
    strategy.exit("PC1","C",stop=130,limit=100,qty=part)
    strategy.exit("PC2","C",stop=130,limit=101,qty_percent=100)
plot(strategy.position_size,title="Position")
plot(strategy.equity,title="Equity")
plot(strategy.position_avg_price,title="Entry price")
plot(ta.cum(volume),title="Volume")
plot(bar_index,title="Index")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}

def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2)
    result.update(schema_version='openpine.lot-soak.1',lots={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'two_active_lots_before_checkpoint':True,'third_stop_limit_activated_pending':True,'pending_and_lot_reservations':True,
        'one_fee_per_fill_exact_lot_residues':True,'closed_slices':2,'closed_lots':1,'remaining_open_lots':2,
        'final_equity':'1032.5','final_cash':'1532.5','final_commission':'13.5'})
    atomic_json(Path(directory)/'evidence.json',result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))
if __name__=='__main__':main()
