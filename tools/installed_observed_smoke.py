"""Installed sampled-price paper strategies through CLI, HTTP and fresh workers."""
import json
from tools.specification import ROOT
from tools.observed_broker_fixtures import EXPECTED,FAMILIES

def verify(http,cli,work):
    example=ROOT/'examples/observed-broker';context=json.loads((example/'contexts.json').read_text());completed=[]
    def check(state,family):
        value=state['simulation']['statistics'];expected=EXPECTED[family]
        assert value['equity']==expected['equity'] and value['position_size']=='0'
    for v in range(1,7):
        family=FAMILIES[v-1];path=example/f'v{v}-{family}.pine';store=work/f'observed-{v}'
        created=cli('replay','create','--source',path,'--contexts',example/'contexts.json','--broker-profile','observed','--state-dir',store)
        identifier=created['replay_id'];cli('replay','append','--id',identifier,'--events',example/(family+'.ndjson'),'--state-dir',store)
        cli('replay','advance','--id',identifier,'--max-events','3','--checkpoint-every','1','--state-dir',store)
        final=cli('replay','advance','--id',identifier,'--checkpoint-every','2','--state-dir',store);check(final,family)
        compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        rid=http('/api/replays',{'input_ref':compiled['input_ref'],**context,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{rid}/events',{'events':[json.loads(line) for line in (example/(family+'.ndjson')).read_text().splitlines()]})
        http(f'/api/replays/{rid}/advance',{'max_events':3,'checkpoint_every':1})
        result=http(f'/api/replays/{rid}/advance',{'max_events':100,'checkpoint_every':2});check(result,family)
        a=cli('replay','ledger','--id',identifier,'--limit','500','--state-dir',store)['events']
        b=http(f'/api/replays/{rid}/ledger?limit=500')['events'];assert a==b
        assert [e['value']['price'] for e in b if e['kind']=='fill']==EXPECTED[family]['prices']
        completed.append({'version':v,'family':family,'equity':result['simulation']['statistics']['equity']})
    return {'observed_cli_http_versions':list(range(1,7)),'observed_families':completed,'observed_exact_journal_equal':True,'observed_active_candle_resume':True}
