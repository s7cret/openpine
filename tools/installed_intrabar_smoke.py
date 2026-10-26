"""Installed source header, explicit v1 mode, preview, resume and CLI/HTTP equality."""
import json
from tools.specification import ROOT
from tools.intrabar_fixtures import expected

def verify(http,cli,work):
    example=ROOT/'examples/intrabar';context=json.loads((example/'contexts.json').read_text());completed=[]
    for v in range(1,7):
        family='commands';path=example/f'v{v}-{family}.pine';store=work/f'intrabar-{v}';oracle=expected(v,family)
        rid=cli('replay','create','--source',path,'--contexts',example/'contexts.json','--calculation-profile','observed','--state-dir',store)['replay_id']
        cli('replay','append','--id',rid,'--events',example/(family+'.ndjson'),'--state-dir',store)
        prefix=cli('replay','advance','--id',rid,'--max-events','3','--checkpoint-every','1','--state-dir',store)
        assert prefix['confirmed_bars']==1 and prefix['calculation']['passes']==3 and prefix['preview'] is not None
        final=cli('replay','advance','--id',rid,'--checkpoint-every','2','--state-dir',store)
        compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        other=http('/api/replays',{'input_ref':compiled['input_ref'],**context,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{other}/events',{'events':[json.loads(line) for line in (example/(family+'.ndjson')).read_text().splitlines()]})
        before=http(f'/api/replays/{other}/advance',{'max_events':3,'checkpoint_every':1});assert before['preview']==prefix['preview']
        result=http(f'/api/replays/{other}/advance',{'max_events':100,'checkpoint_every':2})
        a=cli('replay','ledger','--id',rid,'--limit','500','--state-dir',store)['events'];b=http(f'/api/replays/{other}/ledger?limit=500')['events'];assert a==b
        fills=[e['value'] for e in a if e['kind']=='fill']
        assert [e['price'] for e in fills]==oracle['prices'] and result['simulation']['statistics']['equity']==oracle['equity']
        assert final['calculation']['passes']==result['calculation']['passes']==5
        completed.append({'version':v,'prices':[e['price'] for e in fills],'equity':oracle['equity']})
    for v,family in ((1,'counter'),(4,'arrays'),(5,'counter'),(6,'arrays')):
        path=example/f'v{v}-{family}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        rid=http('/api/replays',{'input_ref':compiled['input_ref'],**context,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{rid}/events',{'events':[json.loads(line) for line in (example/(family+'.ndjson')).read_text().splitlines()]})
        http(f'/api/replays/{rid}/advance',{'max_events':3,'checkpoint_every':1});result=http(f'/api/replays/{rid}/advance',{'max_events':100,'checkpoint_every':1})
        assert result['outputs'][0]['last']==expected(v,family)['outputs'][0][-1]
    return {'intrabar_versions':list(range(1,7)),'intrabar_cli_http_exact_journal':True,'intrabar_active_resume':True,'intrabar_varip_arrays':True,'intrabar_cases':completed}
