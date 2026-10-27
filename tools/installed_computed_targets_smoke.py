"""One installed IR per version, selected computed targets over CLI/HTTP and recovery."""
import json,subprocess,sys,uuid,time
from tools.specification import ROOT

def verify(http,cli,work):
    root=ROOT/'examples/computed-targets';manual=list(map(float,range(100,108)))
    for v in range(1,7):
        artifact=work/f'computed-{v}.json';cli('compile','--source',root/f'v{v}.pine','--profile','historical','--output',artifact)
        result=cli('job','--artifact',artifact,'--bars',root/'parent.json','--contexts',root/'contexts.json','--inputs',root/'overrides.json','--state-dir',work/f'computed-cli-{v}')
        assert result['response']['result']['outputs'][0]['values']==manual
    snapshots={n:http('/api/snapshots',{'provider':'upload','raw':(root/(n+'.json')).read_text()})['sha256'] for n in ('parent','a2','b1')}
    for v in (1,6):
        compiled=http('/api/compile',{'source':(root/f'v{v}.pine').read_text(),'source_name':f'computed-{v}.pine'})
        requirements=http('/api/launches/requirements',{'input_ref':compiled['input_ref'],'inputs':{'choose':False}})
        assert [(r['symbol'],r['timeframe']) for r in requirements['contexts']]==[('TEST:B','1')]
        state=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshots['parent']},'contexts':[{'kind':'snapshot','snapshot_id':snapshots['b1']}],
            'inputs':{'choose':False},'timeout_seconds':120,'gap_policy':'fail'})
        assert state['status']=='ready';key=http('/api/launches/'+state['launch_id']+'/start',{})['job_id'];deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            row=http('/api/jobs/'+key)
            if row['status']=='succeeded':break
            if row['status'] in ('failed','timed_out','cancelled'):raise AssertionError(row)
            time.sleep(.05)
        assert row['status']=='succeeded' and http('/api/jobs/'+key+'/result')['result']['outputs'][0]['values']==manual
        assert http('/api/jobs/'+key+'/configuration')['context_graph']['sha256']==requirements['context_graph']['sha256']
    command=[sys.executable,'-X','utf8','-c',"import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('tools.computed_targets_flow',run_name='__main__')",str(ROOT),'--directory',str(work/'computed-flow')]
    result=subprocess.run(command,cwd=work,text=True,capture_output=True,encoding='utf-8',timeout=300)
    if result.returncode:raise AssertionError(result.stderr[-4000:]+result.stdout[-2000:])
    report=json.loads(result.stdout)
    assert report['status']=='passed' and [r['version'] for r in report['versions']]==list(range(1,7))
    return {'computed_targets_cli_versions':list(range(1,7)),'computed_targets_http_versions':[1,6],
        'computed_targets_recovery_versions':[r['version'] for r in report['versions'] if r['replay_restart']],
        'computed_targets_offline_versions':[r['version'] for r in report['versions'] if r['offline']],
        'computed_targets_lower_versions':[r['version'] for r in report['versions'] if r['lower_tf']]}
