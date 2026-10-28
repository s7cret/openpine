"""Changing windows through saved IR, worker, restart and offline replay."""
import argparse,json
from pathlib import Path
from tools.preparation_flow import run as preparation_run,source as base_source
from packages.contracts.wire import atomic_json

def source(v,lower=False):
    p='' if v<5 else 'ta.'
    return base_source(v,lower).replace('filter(close)',f'filter(close+{p}highestbars(close,bar_index%3+1))')

def run(directory):
    report=preparation_run(directory,source_factory=source)
    report.update(schema_version='openpine.dynamic-signals-flow.1',changing_length=True)
    atomic_json(Path(directory)/'report.json',report);return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);a=p.parse_args(argv)
    print(json.dumps(run(a.directory)));return 0
if __name__=='__main__':raise SystemExit(main())
