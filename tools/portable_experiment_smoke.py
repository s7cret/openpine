"""Exercise the public offline experiment commands on either side of a transfer."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from packages.contracts.wire import atomic_json
from tools.specification import ROOT


def cli(work, *args, code=0):
    cmd = [sys.executable,'-I','-m','apps.openpine',*map(str,args)]
    run = subprocess.run(cmd,cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=240)
    if run.returncode != code:
        raise RuntimeError(f'Unexpected exit {run.returncode}: {run.stdout} {run.stderr}')
    return json.loads(run.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export','repeat'])
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        if args.action == 'export':
            fixture = ROOT/'examples/account-ledger'
            strategy = (fixture/'partial.pine').read_text().replace('if bar_index==0',
                'part=input.int(2)\nif bar_index==0').replace('qty=2)','qty=part)')
            (work/'strategy.pine').write_text(strategy,encoding='utf-8')
            atomic_json(work/'grid.json',dict(name='Settlement grid',timeout_seconds=60,grid={'part':[1,2]}))
            origin = cli(work,'experiment','run','--source',work/'strategy.pine','--bars',fixture/'bars.json',
                         '--request',work/'grid.json','--state-dir',work/'source')
            assert origin['status']=='succeeded' and origin['total']==2
            report = cli(work,'portable-experiment','export','--id',origin['experiment_id'],
                         '--state-dir',work/'source','--bundle',bundle)
            assert all(v['bars']==4 and v['events']>0 for v in report['expected'])
            assert len({v['journal_sha256'] for v in report['expected']})==2
        else:
            report = cli(work,'portable-experiment','repeat','--bundle',bundle,'--state-dir',work/'restored')
            assert report['status']=='verified' and len(report['variants'])==2
            damaged = work/'damaged'
            shutil.copytree(bundle,damaged)
            part = next((damaged/'objects').glob('*/*'))
            with part.open('ab') as stream:
                stream.write(b'x')
            failed = cli(work,'portable-experiment','repeat','--bundle',damaged,'--state-dir',work/'invalid',code=2)
            assert part.name in failed['message'] and not (work/'invalid').exists(), failed
            report['corruption_diagnostic'] = failed['message']
        atomic_json(args.receipt,dict(platform=platform.system(),python=platform.python_version(),report=report))


if __name__ == '__main__':
    main()
