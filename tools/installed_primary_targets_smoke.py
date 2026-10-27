"""One installed primary-relative IR reused for different markets, v1-v6."""
import json,subprocess,sys,time,uuid
from tools.specification import ROOT

def verify(http,cli,work):
    root=ROOT/'examples/primary-targets'
    for v in range(1,7):
        artifact=work/f'primary-{v}.json';cli('compile','--source',root/f'v{v}-references.pine','--profile','historical','--output',artifact)
        assert cli('requirements','--artifact',artifact)['status']=='needs_primary'
        metadata=work/f'primary-{v}-metadata.json';metadata.write_text(json.dumps(json.loads((root/'self-contexts.json').read_text())['primary_context']),encoding='utf-8')
        assert cli('requirements','--artifact',artifact,'--primary-context',metadata)['status']=='resolved'
        for name,values in (('a1',list(map(float,range(10,18)))),('b2',[100.,200.,300.,400.])):
            result=cli('job','--artifact',artifact,'--bars',root/(name+'.json'),'--state-dir',work/f'primary-cli-{v}-{name}')
            assert result['response']['result']['outputs'][0]['values']==values
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(root/'b1.json').read_text()})['sha256']
    context=json.loads((root/'self-contexts.json').read_text())['primary_context']
    for v in (1,6):
        compiled=http('/api/compile',{'source':(root/f'v{v}-inputs.pine').read_text(),'source_name':f'primary-{v}.pine'})
        pending=http('/api/launches/requirements',{'input_ref':compiled['input_ref'],'inputs':{}})
        assert pending['status']=='needs_primary' and pending['contexts']==[]
        ready=http('/api/launches/requirements',{'input_ref':compiled['input_ref'],'inputs':{},'primary_context':context})
        assert ready['status']=='resolved'
        state=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'})
        assert state['status']=='ready';key=http('/api/launches/'+state['launch_id']+'/start',{})['job_id'];deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            row=http('/api/jobs/'+key)
            if row['status']=='succeeded':break
            if row['status'] in ('failed','timed_out','cancelled'):raise AssertionError(row)
            time.sleep(.05)
        assert row['status']=='succeeded' and http('/api/jobs/'+key+'/result')['result']['outputs'][0]['values']==list(map(float,range(100,108)))
        cfg=http('/api/jobs/'+key+'/configuration')
        assert cfg['context_graph']['sha256']==ready['context_graph']['sha256']
    command=[sys.executable,'-X','utf8','-c',"import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('tools.primary_targets_flow',run_name='__main__')",str(ROOT),'--directory',str(work/'primary-flow')]
    process=subprocess.run(command,cwd=work,text=True,capture_output=True,encoding='utf-8',timeout=300)
    if process.returncode:raise AssertionError(process.stderr[-4000:]+process.stdout[-2000:])
    report=json.loads(process.stdout);assert report['status']=='passed' and len(report['versions'])==6
    return {'primary_targets_cli_versions':list(range(1,7)),'primary_targets_cli_markets':['TEST:A/1','TEST:B/2'],
        'primary_targets_http_versions':[1,6],'primary_targets_recovery_versions':[r['version'] for r in report['versions'] if r['replay_restart']],
        'primary_targets_offline_versions':[r['version'] for r in report['versions'] if r['offline']],
        'primary_targets_lower_versions':[r['version'] for r in report['versions'] if r['lower_tf']]}
