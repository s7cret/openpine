"""Million-event changing extrema/WMA windows, bounded history and restart."""
import argparse,json
from pathlib import Path
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source
from packages.contracts.wire import atomic_json

def expected(index):
    k=index%8
    return (float(16*k+10),float(4*k+2),4,1 if k>=4 else 0)

def run(directory,count):
    text=source(count).replace('[close,accumulate(close),close>16]',
        '[ta.highest(close,bar_index%4+1),ta.wma(close,bar_index%4+1),close>16]')
    report=event_run(directory,count,program_source=text,expected_factory=expected)
    report.update(schema_version='openpine.dynamic-windows-soak.1',changing_length=True,
        oracle='Independent cycles: within each group of four increasing prices n=1..4, highest=current and WMA=current-(n-1)/3. Four WMA values average to 4*k+2; no execution kernel computes expectations.')
    atomic_json(Path(directory)/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
