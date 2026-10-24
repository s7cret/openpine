"""Derived context histories, mixed tuple and private UDF through recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Context graph recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
scale=input.float(1.0)
average=ta.wma(close,length)
amount=volume*scale
accumulate(x) =>
    var float total=0
    total:=total+x
    total
[previous,event,total]=request.security("TEST:LOAD","4",[average[1],close>=open,accumulate(amount)])
score=request.security("TEST:LOAD","4",close>=open ? average : -average)
volatility=ta.atr(length)
if bar_index==3 and event
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2*syminfo.mintick,limit=close+volatility*5*syminfo.mintick)
plot(previous,title="Higher previous WMA")
plot(event ? 1 : 0,title="Higher event")
plot(total,title="Higher cumulative")
plot(score,title="Higher conditional")
plot(strategy.equity,title="Equity")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}

def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT)
    result.update(schema_version='openpine.context-graph-soak.1',context_graph={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'transitive_dependencies':True,'expression_history':True,'mixed_tuple':True,'private_persistent_udf':True,'conditional':True})
    atomic_json(Path(directory)/'evidence.json',result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))

if __name__=='__main__':main()
