"""Offline CLI uses the same saved preparation after releasing its catalogue owner."""
import json,subprocess,sys,uuid
from pathlib import Path
from tools.specification import ROOT

def seed(directory):
    from apps.openpine.bootstrap import local_manager
    from apps.openpine.launches import Launches
    from packages.execution.session import Session
    from packages.execution.artifacts import export_program
    from packages.contracts import HISTORICAL_PROFILE_ID
    from packages.contracts.wire import read_json
    from packages.marketdata.providers import input_snapshot
    records=[]
    with local_manager(directory,autostart=False) as manager:
        service=Launches(manager,None);session=Session(profile_id=HISTORICAL_PROFILE_ID)
        snapshot=manager.market.save(input_snapshot(read_json(ROOT/'examples/pipeline/bars.json')))
        for v in range(1,7):
            program=session.compile((ROOT/f'examples/pipeline/v{v}.pine').read_text(),f'offline-v{v}.pine')
            ref=manager.freeze_input({'artifact':export_program(program)});key=uuid.uuid4().hex
            service.create({'schema_version':'openpine.launch-request.1','request_id':key,'input_ref':ref,
                'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'})
            records.append({'version':v,'key':key,'binding':service.inspection(key)['passport']['binding']['sha256']})
        assert manager.repository.list()==[]
    return records

def verify(cli,work):
    directory=work/'offline-bound-passports'
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('tools.installed_bound_passport',run_name='__main__')"
    process=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),str(directory)],cwd=work,capture_output=True,timeout=180)
    assert process.returncode==0,process.stderr[-2000:]
    records=json.loads(process.stdout)
    for row in records:
        value=cli('launch-passport','--state-dir',directory,'--launch-id',row['key'],'--check','--binding-sha256',row['binding'])
        assert value['validation']['status']=='validated' and value['validation']['binding']['sha256']==row['binding']
        assert value['program']['language_version']==row['version'] and value['readiness']['jobs_submitted'] is False
        receipt=cli('launch-passport','--state-dir',directory,'--launch-id',row['key'],'--execution')
        assert receipt['execution']['status']=='not_submitted' and receipt['evidence']['whole_program_execution_verified'] is False
    return {'bound_cli_versions':[r['version'] for r in records],'bound_cli_catalogue_ownership':True,'bound_cli_no_jobs':True}

if __name__=='__main__':print(json.dumps(seed(Path(sys.argv[1]))))
