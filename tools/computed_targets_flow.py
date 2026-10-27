"""Exercise computed targets through the shared file/worker/recovery/offline path."""
import argparse,json
from pathlib import Path
from tools.preparation_flow import run as preparation_run
from tools.computed_targets_spec import source as base_source
from packages.contracts.wire import atomic_json,read_json
from packages.contracts.prepared_requests import graph
from packages.contracts.inputs import settings_for
from packages.execution.artifacts import import_program

def source(v,lower=False):
    integer='input' if v<5 else 'input.int';sma='sma' if v<5 else 'ta.sma'
    text=base_source(v,lower=lower)
    position=text.index('a=request.' if lower else 'plot(')
    text=text[:position]+f'gain={integer}(1)\nfilter(x) =>\n    var float total = 0\n    total := total + x\n    average = {sma}(x,2)\n    total + nz(average)\n'+text[position:]
    text=text.replace(',close)',',filter(close))')
    if lower:return text.replace('plot(array.sum(a))','plot(array.sum(a)*gain)')
    return text.rstrip()[:-1]+'*gain)\n'

def run(directory):
    result=preparation_run(directory,source_factory=source,input_values={'choose':False,'gain':2})
    for row in result['versions']:
        root=Path(directory)/f"v{row['version']}";p=import_program(read_json(root/'program.json'))
        selected=graph(p,settings_for(p,{'choose':False,'gain':2}));node=selected['requests'][0]
        assert (node['symbol'],node['timeframe'])==('TEST:B','1')
        assert node['dependencies']=={'symbol':['input:choose','input:prefix'],'timeframe':['input:choose','input:interval']}
        row['context_graph']=selected
    result['schema_version']='openpine.computed-targets-flow.1';atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args(argv)
    print(json.dumps(run(args.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
