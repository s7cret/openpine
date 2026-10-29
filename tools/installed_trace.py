"""The owned corpus reruns with bounded observations through installed HTTP workers."""
import time,uuid
from apps.openpine.corpus_compare import historical,tagged
from packages.contracts.wire import digest

def verify_cli(http,cli,base):
    from tools.specification import ROOT
    versions=[]
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/pipeline/bars.json').read_text()})['sha256']
    for v in range(1,7):
        ref=http('/api/compile',{'source':(ROOT/f'examples/pipeline/v{v}.pine').read_text(),'source_name':f'installed-trace-v{v}.pine'})['input_ref'];key=uuid.uuid4().hex
        http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':key,'input_ref':ref,'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'})
        response=cli('trace','run','--server',base,'--launch-id',key,'--end-bar','2','--wait')
        assert response['job']['status']=='succeeded';trace=response['trace_id']
        page=cli('trace','events','--server',base,'--id',trace,'--limit','100')
        assert page==http(f'/api/traces/{trace}/events?start=0&limit=100') and page['events']
        comparison=cli('compare','run','--server',base,'--left','job:'+trace,'--right','job:'+trace,'--wait')
        assert comparison['state']['status']=='equal' and comparison==http('/api/comparisons/'+comparison['comparison_id'])
        versions.append(v)
    return {'trace_cli_versions':versions,'trace_cli_http_page_equal':True,'comparison_cli_versions':versions,'comparison_cli_http_report_equal':True}

def wait(http,key):
    until=time.monotonic()+240
    while time.monotonic()<until:
        state=http('/api/traces/'+key)
        if state['job']['status'] not in ('queued','running'):break
        time.sleep(.1)
    assert state['job']['status'] in ('succeeded','completed'),state
    return state

def verify(http,launch,binding,bars,expected,replay):
    key=uuid.uuid4().hex
    http('/api/traces',{'request_id':key,'launch_id':launch,'binding_sha256':binding,
        'options':{'end_bar':min(4,len(bars)),'max_events':3000,'max_bytes':2*1024*1024}})
    state=wait(http,key);page=http('/api/traces/'+key+'/events?limit=100')
    assert page['events'] and all(0<=r['primary_bar']<min(4,len(bars)) for r in page['events'])
    semantic=digest(tagged(historical(http('/api/jobs/'+key+'/result'),bars)));assert semantic==expected
    original=http('/api/replays/'+replay+'/passport')['passport']['replay_binding']
    source=http('/api/replays/'+replay)
    other=uuid.uuid4().hex
    http('/api/traces',{'request_id':other,'replay_id':replay,'binding_sha256':original['sha256'],
        'options':{'end_bar':min(4,len(bars)),'max_events':3000,'max_bytes':2*1024*1024}})
    traced=wait(http,other)
    assert traced['replay']['pending_events']==0 and traced['replay']['confirmed_bars']==source['confirmed_bars']
    assert traced['whole_program_execution_verified'] is False and http('/api/replays/'+replay)==source
    clone=traced['configuration']['replay_id']
    assert http('/api/replays/'+clone+'/chart?start=0&limit=5000')['rows']==http('/api/replays/'+replay+'/chart?start=0&limit=5000')['rows']
    a,b=traced['replay']['simulation'],source['simulation']
    assert ({k:v for k,v in a.items() if k!='journal'} if a else None)==({k:v for k,v in b.items() if k!='journal'} if b else None)
    start=0
    while True:
        left=http(f'/api/replays/{clone}/ledger?start={start}&limit=5000');right=http(f'/api/replays/{replay}/ledger?start={start}&limit=5000')
        assert left==right
        if left['next']>=left['total']:break
        assert left['next']>start and left['next']<=100000;start=left['next']
    assert http('/api/traces/'+other+'/events?limit=100')['events']
    from tools.installed_comparison import verify as compare_verify
    comparison=compare_verify(http,launch,key,replay,clone)
    return {**comparison,'trace_history_equal':True,'trace_replay_equal':True,'trace_source_preserved':True,
        'trace_semantic_sha256':semantic,'trace_capture_available':True,'trace_truncated':state['trace']['truncated']}
