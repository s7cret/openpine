"""Installed historical CLI/HTTP, pause/resume and both ordering oracles, v1-v6."""
import json,time,uuid
from tools.specification import ROOT
from tools.magnifier_fixtures import configuration

def verify(http,cli,work):
    root=ROOT/'examples/bar-magnifier';completed=[]
    primary=http('/api/snapshots',{'provider':'upload','raw':(root/'parent.json').read_text()})['sha256']
    for reverse,name,prices,equity in ((False,'high',['100','120'],'1020'),(True,'low',['100','80'],'980')):
        lower_path=root/f'lower-{name}-first.json'
        lower=http('/api/snapshots',{'provider':'upload','raw':lower_path.read_text()})['sha256']
        for v in range(1,7):
            source=root/f'v{v}.pine';store=work/f'magnifier-{v}-{name}'
            prefix=cli('job','--source',source,'--bars',root/'parent.json','--magnifier',root/'configuration.json','--lower-bars',lower_path,
                '--pause-after','1','--state-dir',store)
            assert prefix['job']['status']=='paused' and prefix['job']['completed']==1
            final=cli('job','--resume',prefix['job']['job_id'],'--state-dir',store)['response']['result']
            compiled=http('/api/compile',{'source':source.read_text(),'source_name':source.name})
            prepared=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
                'primary':{'kind':'snapshot','snapshot_id':primary},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail',
                'magnifier':configuration(lower)})
            assert prepared['status']=='ready',prepared
            key=http('/api/launches/'+prepared['launch_id']+'/start',{})['job_id'];deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                state=http('/api/jobs/'+key)
                if state['status']=='succeeded':break
                assert state['status'] in ('running','queued'),state
                time.sleep(.05)
            else:raise TimeoutError(state)
            other=http('/api/jobs/'+key+'/result')['result']
            assert final['simulation']['events']==other['simulation']['events']
            fills=[e['value']['price'] for e in other['simulation']['events'] if e['kind']=='fill']
            assert fills==prices and other['simulation']['statistics']['equity']==equity
            assert other['simulation']['bar_magnifier']['model_points']==16
            assert other['simulation']['calculation_cycle']['phase_counts']=={'regular':2,'after_fill':2,'after_close_fill':0}
            saved=http('/api/jobs/'+key+'/configuration');assert saved['plan']['magnifier']==configuration(lower)
            assert len(saved['datasets'])==2
            completed.append({'version':v,'lower_path':name,'prices':fills,'equity':equity,'exact_journal':True})
    return {'magnifier_versions':list(range(1,7)),'magnifier_cli_http_cases':completed,'magnifier_paused_resume':True}
