"""Bounded array heap through the full worker and real recovery boundaries."""

import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Array worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
[main,signal,histogram]=request.security("TEST:LOAD","4",ta.macd(close,2,4,3))
volatility=ta.atr(length)
var window = array.new_float(0)
var alias = window
array.push(alias, close)
if array.size(window) > length
    array.shift(window)
copy = array.copy(window)
array.set(copy, 0, 999.0)
fresh = array.from(close)
previous = fresh[1]
value = for item in window
    ta.sma(item, 3)
if bar_index==3
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2,limit=close+volatility*5)
plot(array.avg(window))
plot(histogram)
plot(strategy.equity)
plot(value)
plot(na(previous) ? na : array.get(previous, 0))
'''


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE)
    result.update(schema_version='openpine.array-soak.1',arrays={
        'after_warmup_live_objects':2,'after_warmup_live_cells':4,'loop_updates_after_warmup':3,
        'allocations_per_bar_after_first':2,'identity_aliases':True,'fresh_history_offset':1})
    atomic_json(Path(directory)/'evidence.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=1000000);args=parser.parse_args();print(run(args.directory,args.bars))


if __name__=='__main__':main()
