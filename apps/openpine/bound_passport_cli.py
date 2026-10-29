"""Explain/check a saved launch using its existing offline catalogue ownership."""
import argparse,sys
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from apps.openpine.launches import Launches
from packages.contracts.wire import atomic_json,canonical
from packages.contracts import OpenPineError
from packages.compiler.semantic.specification import SpecError
from apps.openpine.passport import rejected

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir',type=Path,required=True)
    target=p.add_mutually_exclusive_group(required=True);target.add_argument('--launch-id');target.add_argument('--replay-id')
    choice=p.add_mutually_exclusive_group();choice.add_argument('--check',action='store_true');choice.add_argument('--execution',action='store_true')
    p.add_argument('--binding-sha256');p.add_argument('--output',type=Path);a=p.parse_args(argv)
    if a.binding_sha256 and not a.check:p.error('--binding-sha256 requires --check')
    if a.output and a.output.resolve().is_relative_to(a.state_dir.resolve()):p.error('Store exported reports outside the catalogue.')
    try:
        if not (a.state_dir/'catalog.sqlite3').is_file():raise ValueError('State directory must contain an existing catalogue.')
        with local_manager(a.state_dir,autostart=False) as manager:
            if a.replay_id:
                from apps.openpine.replay import Replays
                value=Replays(manager).passport(a.replay_id,check=a.check,expected=a.binding_sha256)['passport']
            else:
                service=Launches(manager,None)
                value=(service.validation(a.launch_id,a.binding_sha256) if a.check else service.execution(a.launch_id) if a.execution else service.inspection(a.launch_id))['passport']
    except (OpenPineError,SpecError,ValueError,OSError,KeyError,TypeError) as error:value=rejected(error,'saved_launch')
    if a.output:atomic_json(a.output,value)
    else:sys.stdout.write(canonical(value).decode('utf-8')+'\n')
    return 2 if value['status']=='rejected' or value.get('validation',{}).get('status')=='blocked' or value.get('execution',{}).get('status')=='invalid_result' else 0

if __name__=='__main__':raise SystemExit(main())
