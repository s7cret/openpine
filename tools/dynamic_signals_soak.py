"""Changing VWMA/MOM/rising over a million events with an independent oracle."""
import argparse,json
from pathlib import Path
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source
from packages.contracts.wire import atomic_json

def expected(index):
 k=index%8
 return (float(16*k+7),0. if index==0 else -29.5 if k==0 else 2.5,4,1 if k else 0)
def run(directory,count):
 text=source(count).replace('[close,accumulate(close),close>16]',
 '[ta.vwma(close,bar_index%4+1),nz(ta.mom(close,bar_index%4+1)),ta.rising(close,bar_index%4+1)]')
 report=event_run(directory,count,program_source=text,expected_factory=expected)
 report.update(schema_version='openpine.dynamic-signals-soak.1',oracle='Constant volume: cumulative group means sum to 16*k+7. MOM averages 2.5, -29.5 at cycle wraps, zero during initial warmup. First rising is true except at cycle starts. Direct arithmetic only.')
 atomic_json(Path(directory)/'report.json',report);return report
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
 print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
