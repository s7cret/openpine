"""Installed CLI/HTTP phase settings, checkpoint resume and journal equality."""
import json
from tools.specification import ROOT
from tools.strategy_cycle_fixtures import expected,context

def verify(http,cli,work):
    example=ROOT/'examples/strategy-cycle';completed=[]
    for v in range(1,7):
        family='protection';path=example/f'v{v}-{family}.pine';store=work/f'cycle-{v}';oracle=expected(v,family)
        rid=cli('replay','create','--source',path,'--contexts',example/f'contexts-{family}.json','--calculation-profile','cycle',
            '--calc-on-order-fills','--process-orders-on-close','--state-dir',store)['replay_id']
        cli('replay','append','--id',rid,'--events',example/'events.ndjson','--state-dir',store)
        prefix=cli('replay','advance','--id',rid,'--max-events','1','--checkpoint-every','1','--state-dir',store)
        assert prefix['calculation']['passes']==2 and prefix['simulation']['statistics']['fills']==1 and len(prefix['simulation']['pending_protections'])==1
        final=cli('replay','advance','--id',rid,'--checkpoint-every','2','--state-dir',store)
        compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        other=http('/api/replays',{'input_ref':compiled['input_ref'],**context(family),'timeout_seconds':180})['replay_id']
        http(f'/api/replays/{other}/events',{'events':[json.loads(line) for line in (example/'events.ndjson').read_text().splitlines()]})
        before=http(f'/api/replays/{other}/advance',{'max_events':1,'checkpoint_every':1});assert before['calculation']==prefix['calculation']
        result=http(f'/api/replays/{other}/advance',{'max_events':100,'checkpoint_every':2})
        a=cli('replay','ledger','--id',rid,'--limit','500','--state-dir',store)['events'];b=http(f'/api/replays/{other}/ledger?limit=500')['events'];assert a==b
        fills=[x['value'] for x in a if x['kind']=='fill'];assert [x['price'] for x in fills]==oracle['prices']
        assert final['calculation']==result['calculation'] and result['calculation']['passes']==4 and result['simulation']['statistics']['equity']==oracle['equity']
        completed.append({'version':v,'prices':[x['price'] for x in fills],'passes':4,'equity':oracle['equity']})
    for v,family in ((1,'counter'),(4,'arrays'),(6,'counter')):
        path=example/f'v{v}-{family}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        rid=http('/api/replays',{'input_ref':compiled['input_ref'],**context(family),'timeout_seconds':180})['replay_id']
        http(f'/api/replays/{rid}/events',{'events':[json.loads(line) for line in (example/'events.ndjson').read_text().splitlines()]})
        result=http(f'/api/replays/{rid}/advance',{'max_events':100,'checkpoint_every':1})
        assert result['outputs'][-1]['last']==expected(v,family)['outputs'][-1][-1]
    return {'cycle_versions':list(range(1,7)),'cycle_cli_http_exact_journal':True,'cycle_close_fill_resume':True,'cycle_cases':completed}
