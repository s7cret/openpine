"""Reuse real crash/backup acceptance with composite and primitive states together."""
import argparse
from pathlib import Path
from tools.technical_soak import SOURCE,run

COMPOSITE_SOURCE=SOURCE.replace('plot(strategy.equity)', '''[main,signal,histogram]=ta.macd(close,2,4,3)
[middle,upper,lower]=ta.bb(close,3,2)
plot(main)
plot(signal)
plot(histogram)
plot(middle)
plot(upper)
plot(lower)
plot(ta.variance(close,3))
plot(ta.stdev(close,3))
plot(strategy.equity)''')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=100000);args=parser.parse_args()
    print(run(args.directory,args.bars,source=COMPOSITE_SOURCE,
        required_kinds={'rma','atr','rsi','highest','lowest','macd','bb','moments'},schema_version='openpine.composite-recovery.1'))


if __name__=='__main__':main()
