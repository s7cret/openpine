"""Resolve non-default target inputs once, then stream and restore a million events."""
import argparse,json
from pathlib import Path
from packages.contracts.wire import atomic_json
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source

def run(directory,count):
    text=source(count).replace('accumulate(x) =>','symbol=input.string("TEST:UNSELECTED")\ninterval=input.string("2")\naccumulate(x) =>')
    text=text.replace('request.security_lower_tf("TEST:LOWER","1",','request.security_lower_tf(symbol,interval,')
    report=event_run(directory,count,program_source=text,inputs={'symbol':'TEST:LOWER','interval':'1'})
    node=report['context_graph']['requests'][0]
    assert (node['symbol'],node['timeframe'])==('TEST:LOWER','1') and node['dependencies']=={'symbol':['input:symbol'],'timeframe':['input:interval']}
    report.update(schema_version='openpine.prepared-requests-soak.1',non_default_target=True)
    atomic_json(Path(directory)/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
