"""Saved pure-function targets through worker, replay restart and offline replay."""
import argparse,json
from pathlib import Path
from tools.preparation_flow import run as preparation_run,source as base_source
from tools.prepared_requests_spec import datasets,context
from packages.contracts.wire import atomic_json,read_json
from packages.contracts.prepared_requests import graph
from packages.contracts.inputs import settings_for
from packages.execution.artifacts import import_program

def source(v,lower=False):
    ref='tickerid' if v<4 else 'syminfo.tickerid'
    text=base_source(v,lower).replace('selected=sym',f'choose(value) => {ref}=="TEST:CHART" ? value : {ref}\ninterval(value) => value\nselected=choose(sym)')
    return text.replace('(selected,tf,','(selected,interval(tf),')

def run(directory):
    result=preparation_run(directory,source_factory=source)
    for row in result['versions']:
        root=Path(directory)/f"v{row['version']}";program=import_program(read_json(root/'program.json'))
        selected=graph(program,settings_for(program,{'sym':'TEST:B','tf':'1','gain':2}),primary=context(datasets()['parent']));node=selected['requests'][0]
        assert selected['schema_version']=='openpine.function-targets.1' and (node['symbol'],node['timeframe'])==('TEST:B','1')
        assert node['dependencies']=={'symbol':['input:sym','primary:symbol'],'timeframe':['input:tf']}
        assert [f['name'] for f in node['function_dependencies']['symbol']]==['choose']
        assert [f['name'] for f in node['function_dependencies']['timeframe']]==['interval'];row['context_graph']=selected
    result['schema_version']='openpine.function-targets-flow.1';atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);a=p.parse_args(argv)
    print(json.dumps(run(a.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
