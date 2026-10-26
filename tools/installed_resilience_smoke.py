"""Installed CLI persists a frozen request and recovers the same research identity."""
from pathlib import Path
import uuid
from apps.openpine.http_client import LocalClient,LocalAPIError
from packages.contracts.wire import atomic_json,read_json
from tools.specification import ROOT

def verify(http,cli,work,base):
    client=LocalClient(base);example=ROOT/'examples/search';search=read_json(example/'request.json');search.update(budget=1,space={'n':[1]})
    experiment={k:search[k] for k in ('name','timeout_seconds')};experiment['variants']=[{'n':1}]
    walk={k:v for k,v in search.items() if k!='windows'};walk['walkforward']={'mode':'expanding','start_ms':60000,'train_ms':120000,'test_ms':60000,'step_ms':60000,'warmup_ms':60000,'folds':2,'max_jobs':4}
    receipts={}
    for kind,payload in (('experiment',experiment),('search',search),('walkforward',walk)):
        request=work/(kind+'-durable-request.json');operation=work/(kind+'-client-operation.json');atomic_json(request,payload)
        args=[kind,'create','--server',base,'--request',str(request),'--source',str(example/'v6.pine'),'--bars',str(ROOT/'examples/inputs/bars.json'),'--operation-file',str(operation)]
        first=cli(*args);second=cli(*args);assert first==second and first['status']=='accepted'
        assert client.call('/api/operations/'+kind+'/'+first['request_id'])==first
        saved=read_json(operation);assert saved['status']=='accepted' and saved['resource_id']==first['resource_id']
        changed={**saved['payload'],'name':'Changed request'}
        try:client.call('/api/'+{'experiment':'experiments','search':'searches','walkforward':'walkforwards'}[kind],changed,request_id=first['request_id'])
        except LocalAPIError as error:assert error.status==409 and error.response['diagnostics'][0]['code']=='IDEMPOTENCY_CONFLICT'
        else:raise AssertionError('A reused key accepted different settings.')
        path='/api/'+{'experiment':'experiments','search':'searches','walkforward':'walkforwards'}[kind]+'/'+first['resource_id']
        state=client.call(path+'/progress');assert state[kind+'_id']==first['resource_id']
        client.call(path+'/cancel',{});receipts[kind]={'request_id':first['request_id'],'resource_id':first['resource_id']}
    return {'resilience_frozen_cli_requests':True,'resilience_receipt_lookup':True,'resilience_changed_settings_conflict':True,'resilience_catalog_progress_and_cancel':True,'resilience_research_kinds':receipts}
