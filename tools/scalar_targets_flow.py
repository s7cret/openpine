"""Shared scalar targets through saved IR, workers, replay restart and offline."""
import argparse,json
from pathlib import Path
from tools.preparation_flow import run as preparation_run
from tools.function_targets_flow import source as function_source
from packages.contracts.wire import atomic_json,read_json
from packages.contracts.prepared_requests import graph
from packages.contracts.inputs import settings_for
from packages.execution.artifacts import import_program
from tools.prepared_requests_spec import datasets,context

def source(v,lower=False):
    name='tostring' if v<5 else 'str.tostring'
    text=function_source(v,lower).replace('interval(value) => value',f'interval(value) => value+{name}(0,"#")=="never" ? "2" : value')
    if v>=5:text=text.replace('interval(value) => value+', 'interval(value) => str.replace_all(value,"never","unused")+')
    return text

def run(directory):
    result=preparation_run(directory,source_factory=source)
    for row in result['versions']:
        root=Path(directory)/f"v{row['version']}";p=import_program(read_json(root/'program.json'))
        g=graph(p,settings_for(p,{'sym':'TEST:B','tf':'1','gain':2}),primary=context(datasets()['parent']))
        assert g['schema_version']=='openpine.scalar-targets.1' and g['requests'][0]['timeframe']=='1'
        assert 'scalar.tostring.format' in g['requests'][0]['scalar_dependencies']['timeframe'];row['context_graph']=g
    result['schema_version']='openpine.scalar-targets-flow.1';atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);a=p.parse_args(argv)
    print(json.dumps(run(a.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
