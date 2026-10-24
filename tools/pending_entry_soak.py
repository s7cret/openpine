"""Long-lived activated stop-limit with exact pending protection through real recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Pending entry worker recovery",default_qty_type=strategy.fixed,initial_capital=1000,slippage=2)
part=input.int(5)
higher=request.security("TEST:LOAD","4",close)
if bar_index==3 and higher>0
    strategy.entry("S",strategy.short,qty=10,stop=100-syminfo.mintick,limit=100+5*syminfo.mintick)
    strategy.exit("A","S",stop=120,limit=100,qty=part)
    strategy.exit("B","S",stop=120,limit=101,qty_percent=100)
plot(strategy.position_size,title="Position")
plot(strategy.equity,title="Equity")
plot(strategy.position_avg_price,title="Entry price")
plot(ta.cum(volume),title="Volume")
plot(bar_index,title="Index")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}

def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2)
    result.update(schema_version='openpine.pending-entry-soak.1',pending_entries={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'long_lived_stop_limit':True,'activation_before_checkpoint':True,'pending_protection_targets':True,'limit_ignores_slippage':True,'closed_slices':2,'closed_positions':1})
    atomic_json(Path(directory)/'evidence.json',result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))
if __name__=='__main__':main()
