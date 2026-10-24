"""Million-bar UTC environment on the shared worker/recovery acceptance path."""

import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}
SOURCE='''//@version=6
strategy("Environment worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
higherEnd=request.security("TEST:LOAD","4",time_close)
volatility=ta.atr(length)
if bar_index==3
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2*syminfo.mintick,limit=close+volatility*5*syminfo.mintick)
plot(time,title="Time")
plot(time_close,title="End")
plot(higherEnd,title="Higher end")
plot(barstate.islast ? 1:0,title="Last")
plot(strategy.equity,title="Equity")
'''


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT)
    result.update(schema_version='openpine.environment-soak.1',environment={
        'instrument':INSTRUMENT,'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'last_flag':'full_frozen_range','recorded_time_not_host_time':True})
    atomic_json(Path(directory)/'evidence.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--bars',type=int,default=1000000)
    args=parser.parse_args();print(run(args.directory,args.bars))


if __name__=='__main__':main()
