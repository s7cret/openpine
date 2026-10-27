"""Numeric targets and requested formulas through saved IR and recovery, v1-v6."""
import argparse,json
from pathlib import Path
from tools.preparation_flow import run as preparation_run
from tools.scalar_targets_flow import source as scalar_source
from tools.prepared_requests_spec import datasets,context
from packages.contracts.wire import atomic_json,read_json
from packages.contracts.inputs import settings_for
from packages.contracts.prepared_requests import graph
from packages.execution.artifacts import import_program

def source(v,lower=False):
    text=scalar_source(v,lower);p='' if v<5 else 'math.';fmt='tostring' if v<5 else 'str.tostring'
    text=text.replace(f'{fmt}(0,"#")',f'{fmt}({p}round(0.0),"#")')
    return text.replace('filter(close)',f'filter({p}sqrt({p}pow(close,2)))')

def run(directory):
    result=preparation_run(directory,source_factory=source)
    for row in result['versions']:
        root=Path(directory)/f"v{row['version']}";program=import_program(read_json(root/'program.json'))
        g=graph(program,settings_for(program,{'sym':'TEST:B','tf':'1','gain':2}),primary=context(datasets()['parent']))
        assert g['schema_version']=='openpine.numeric-targets.1' and g['requests'][0]['timeframe']=='1'
        assert 'numeric.round' in g['requests'][0]['scalar_dependencies']['timeframe'];row['context_graph']=g
    result.update(schema_version='openpine.numeric-targets-flow.1',requested_math=True)
    atomic_json(Path(directory)/'report.json',result);return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);a=p.parse_args(argv)
    print(json.dumps(run(a.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
