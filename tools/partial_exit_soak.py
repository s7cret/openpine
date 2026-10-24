"""Partial market/protective slices on the shared million-bar recovery path."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Partial exit worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
part=input.int(5)
higher=request.security("TEST:LOAD","4",close)
if bar_index==3 and higher>0
    strategy.entry("L",strategy.long,qty=10)
    strategy.exit("A","L",stop=100-5*syminfo.mintick,limit=100+10*syminfo.mintick,qty=part)
    strategy.exit("B","L",stop=100-5*syminfo.mintick,limit=100+20*syminfo.mintick,qty_percent=100)
if bar_index==17
    strategy.close("L",qty=part)
plot(strategy.position_size,title="Position")
plot(strategy.equity,title="Equity")
plot(strategy.position_avg_price,title="Entry price")
plot(ta.cum(volume),title="Volume")
plot(bar_index,title="Index")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}
def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=2)
    result.update(schema_version='openpine.partial-exit-soak.1',partial_exits={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'market_partial':True,'protective_partial':True,'reservation_reduction':True,'closed_slices':2,'closed_positions':1})
    atomic_json(Path(directory)/'evidence.json',result);return result
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))
if __name__=='__main__':main()
