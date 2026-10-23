"""Full worker, repeated TA in loops and real durable failure recovery."""

import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Loop worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
[main,signal,histogram]=request.security("TEST:LOAD","4",ta.macd(close,2,4,3))
volatility=ta.atr(length)
value = for i = 0 to 2
    var n = 0
    n := n + 1
    ta.sma(close+i,3)
j = 0
while j < 2
    j := j + 1
if bar_index==3
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2,limit=close+volatility*5)
plot(value)
plot(histogram)
plot(strategy.equity)
'''


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE)
    result.update(schema_version='openpine.loop-soak.1',
        loops={'for_iterations_per_bar':3,'while_iterations_per_bar':2,'ta_updates_per_bar':3,
            'first_output_expected':101.0,'local_var_final':bars*3})
    atomic_json(Path(directory)/'evidence.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=1000000);args=parser.parse_args()
    print(run(args.directory,args.bars))


if __name__=='__main__':main()
