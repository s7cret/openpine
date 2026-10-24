"""All signal states on the shared million-bar worker/recovery path."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json
SOURCE='''//@version=6
strategy("Signal worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
higher=request.security("TEST:LOAD","4",ta.valuewhen(close>0,close,0))
remember=ta.valuewhen(close>0,close,1)
age=ta.barssince(close>0)
accumulated=ta.cum(volume)
window=math.sum(volume,length)
average=ta.wma(close,length)
weighted=ta.vwma(close,length)
momentum=ta.mom(close,length)
rate=ta.roc(close,length)
up=ta.rising(close,length)
down=ta.falling(close,length)
peak=ta.highestbars(high,length)
trough=ta.lowestbars(low,length)
volatility=ta.atr(length)
if bar_index==3 and higher>0
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2*syminfo.mintick,limit=close+volatility*5*syminfo.mintick)
plot(average,title="WMA")
plot(weighted,title="VWMA")
plot(accumulated,title="Volume")
plot(age,title="Age")
plot(strategy.equity,title="Equity")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT)
    result.update(schema_version='openpine.signal-soak.1',signals={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,'all_twelve_kernels':True,'requested_event_predicate':True})
    atomic_json(Path(directory)/'evidence.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))


if __name__=='__main__':main()
