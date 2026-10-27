"""Million-event bounded-state verification with selected computed lower targets."""
import argparse,json
from pathlib import Path
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source
from packages.contracts.wire import atomic_json

def run(directory,count):
    text=source(count).replace('accumulate(x) =>','prefix=input.string("TEST:")\nchoose=input.bool(true)\ninterval=input.int(2)\nsymbol=prefix+(choose ? "UNSELECTED" : "LOWER")\ntf=interval>1 ? "2" : "1"\naccumulate(x) =>')
    text=text.replace('request.security_lower_tf("TEST:LOWER","1",','request.security_lower_tf(symbol,tf,')
    result=event_run(directory,count,program_source=text,inputs={'choose':False,'interval':1})
    node=result['context_graph']['requests'][0]
    assert (node['symbol'],node['timeframe'])==('TEST:LOWER','1')
    assert node['dependencies']=={'symbol':['input:choose','input:prefix'],'timeframe':['input:interval']}
    result.update(schema_version='openpine.computed-targets-soak.1',non_default_target=True)
    atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--lower-candles',type=int,default=800000);args=parser.parse_args(argv)
    print(json.dumps(run(args.directory,args.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
