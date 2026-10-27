"""Million-event recovery with a frozen pure-function lower target."""
import argparse,json
from pathlib import Path
from tools.lower_events_soak import run as event_run
from tools.lower_tf_soak import source
from packages.contracts.wire import atomic_json

def run(directory,count):
    text=source(count).replace('accumulate(x) =>','interval=input.timeframe("")\nchoose(value) => timeframe.period=="4" ? value : "2"\ntf=choose(interval)\naccumulate(x) =>')
    text=text.replace('request.security_lower_tf("TEST:LOWER","1",','request.security_lower_tf("TEST:LOWER",tf,')
    result=event_run(directory,count,program_source=text,inputs={'interval':'1'});node=result['context_graph']['requests'][0]
    assert (node['symbol'],node['timeframe'])==('TEST:LOWER','1')
    assert node['dependencies']=={'symbol':[],'timeframe':['input:interval','primary:timeframe']}
    assert [f['name'] for f in node['function_dependencies']['timeframe']]==['choose']
    result.update(schema_version='openpine.function-targets-soak.1',pure_function_target=True)
    atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--lower-candles',type=int,default=800000);a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.lower_candles)));return 0
if __name__=='__main__':raise SystemExit(main())
