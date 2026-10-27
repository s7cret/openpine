"""Million events with numeric target construction and real requested math."""
import argparse,json
from pathlib import Path
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source
from packages.contracts.wire import atomic_json

def run(directory,count):
    text=source(count).replace('accumulate(x) =>','minutes=input.float(1.0)\nchoose(value) => str.tostring(math.max(1,math.round(value)))\ntf=choose(minutes)\naccumulate(x) =>')
    text=text.replace('request.security_lower_tf("TEST:LOWER","1",','request.security_lower_tf("TEST:LOWER",tf,')
    text=text.replace('[close,accumulate(close),close>16]','[math.sqrt(math.pow(close,2)),accumulate(math.sqrt(math.pow(close,2))),close>16]')
    result=event_run(directory,count,program_source=text,inputs={'minutes':1.0});g=result['context_graph'];n=g['requests'][0]
    assert g['schema_version']=='openpine.numeric-targets.1' and (n['symbol'],n['timeframe'])==('TEST:LOWER','1')
    assert n['scalar_dependencies']['timeframe']==['numeric.max','numeric.round','scalar.tostring']
    result.update(schema_version='openpine.numeric-targets-soak.1',numeric_target=True,requested_math=True)
    atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
