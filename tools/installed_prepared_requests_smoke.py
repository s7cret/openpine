"""One installed saved IR per version; distinct selected datasets through CLI and HTTP."""
import json,time,uuid
from tools.specification import ROOT

def verify(http,cli,work):
    root=ROOT/'examples/prepared-requests';expected=json.loads((root/'expected.json').read_text())
    def finish(key,status='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            row=http('/api/jobs/'+key)
            if row['status']==status:return row
            if row['status'] in ('failed','cancelled','timed_out'):raise AssertionError(row)
            time.sleep(.05)
        raise TimeoutError(row)
    for v in range(1,7):
        artifact=work/f'prepared-{v}.json';cli('compile','--source',root/f'v{v}.pine','--profile','historical','--output',artifact)
        for selected in (False,True):
            args=['job','--artifact',artifact,'--bars',root/'parent.json','--contexts',root/'contexts.json','--state-dir',work/f'prepared-{v}-{selected}']
            if selected:args+=['--inputs',root/'overrides.json']
            out=cli(*args)['response']['result']['outputs'][0]['values']
            assert out==expected['selected_b1' if selected else 'default_on' if v<3 else 'default_off'],(v,selected,out)
    snapshots={name:http('/api/snapshots',{'provider':'upload','raw':(root/(name+'.json')).read_text()})['sha256'] for name in ('parent','lower-parent','a1','a2','b1','b2')}
    for v in (1,6):
        compiled=http('/api/compile',{'source':(root/f'v{v}.pine').read_text(),'source_name':f'v{v}.pine'})
        requirements=http('/api/launches/requirements',{'input_ref':compiled['input_ref'],'inputs':{'sym':'TEST:B','tf':'1'}})
        assert [(r['symbol'],r['timeframe']) for r in requirements['contexts']]==[('TEST:B','1')]
        state=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshots['parent']},'contexts':[{'kind':'snapshot','snapshot_id':snapshots['b1']}],
            'inputs':{'sym':'TEST:B','tf':'1'},'timeout_seconds':120,'gap_policy':'fail','checkpoint_interval_bars':1,'pause_after_bars':3})
        assert state['status']=='ready',state
        base=http(f"/api/launches/{state['launch_id']}/research-base",{'snapshots':[snapshots['a2'],snapshots['b2']]})
        assert len(base['contexts'])==3
        key=http(f"/api/launches/{state['launch_id']}/start",{})['job_id'];finish(key,'paused');http('/api/jobs/'+key+'/resume',{});finish(key)
        result=http('/api/jobs/'+key+'/result')['result'];assert result['outputs'][0]['values']==expected['selected_b1']
        cfg=http('/api/jobs/'+key+'/configuration');assert cfg['context_graph']['sha256']==requirements['context_graph']['sha256']
    for v in (5,6):
        artifact=work/f'prepared-lower-{v}.json';cli('compile','--source',root/f'v{v}-lower.pine','--profile','historical','--output',artifact)
        values=cli('job','--artifact',artifact,'--bars',root/'lower-parent.json','--contexts',root/'lower-contexts.json',
            '--inputs',root/'overrides.json','--state-dir',work/f'prepared-lower-{v}')['response']['result']['outputs']
        assert [o['values'] for o in values]==[expected['lower_b1_sum'],expected['lower_count']]
    return {'prepared_requests_cli_versions':list(range(1,7)),'prepared_requests_http_versions':[1,6],
        'prepared_requests_saved_ir':True,'prepared_requests_distinct_graphs':True,'prepared_requests_lower_versions':[5,6],
        'prepared_requests_checkpoint_resume':True,'prepared_requests_explicit_research_catalogue':True}
