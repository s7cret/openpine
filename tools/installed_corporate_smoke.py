"""Prove corporate actions across shared-wheel HTTP, saved IR and isolated jobs."""
import json
import uuid

from packages.contracts.wire import canonical
from tools.specification import ROOT


def verify(http, cli, work):
    from tools.installed_input_smoke import wait_job
    corpus = ROOT/'conformance/corporate-actions'
    dataset = json.loads((corpus/'events.json').read_bytes())
    ref = http('/api/corporate-actions',dataset)['sha256']
    assert http('/api/corporate-actions',dataset)['sha256'] == ref
    bars = corpus/'bars.json'
    snapshot = http('/api/snapshots',{'provider':'upload','raw':bars.read_text()})['sha256']
    expected = json.loads((corpus/'cases.json').read_bytes())['cases'][5]['expected']['outputs']
    for example in ('indicator','strategy'):
        path = ROOT/'examples/corporate-actions'/(example+'.pine')
        compiled = http('/api/compile',{'source':path.read_text(),'source_name':path.name})
        assert compiled['status']=='compiled', compiled
        requirements = http('/api/launches/requirements?input_ref='+compiled['input_ref'])
        assert {row['family'] for row in requirements['corporate_actions']} == ({'splits','dividends'} if example=='indicator' else {'splits'})
        artifact = work/(example+'-actions.json')
        artifact.write_bytes(canonical(http('/api/artifacts/'+compiled['artifact_ref'])))
        ready = http('/api/launches',{'schema_version':'openpine.launch-request.1','input_ref':compiled['input_ref'],
                        'request_id':uuid.uuid4().hex,'primary':{'kind':'snapshot','snapshot_id':snapshot},
                        'corporate_snapshot_id':ref,'contexts':[],'inputs':{},'timeout_seconds':180,
                        'gap_policy':'fail','checkpoint_interval_bars':2,'pause_after_bars':2})
        assert ready['status']=='ready',ready
        job = http('/api/launches/'+ready['launch_id']+'/start',{})['job_id']
        route = '/api/jobs/'+job
        wait_job(http,route,'paused')
        assert http(route+'/plan')['corporate_snapshot_id']==ref
        http(route+'/resume',{})
        assert wait_job(http,route,'succeeded')['resumed_from_bar']==2
        result = http(route+'/result')['result']
        preview = cli('job','--source',path,'--bars',bars,'--corporate-actions',corpus/'events.json',
                      '--plan-only','--state-dir',work/('corporate-'+example))
        assert preview['corporate_actions']==ready['prepared']['requirements']['corporate_actions']
        cli_result = cli('job','--artifact',artifact,'--bars',bars,'--corporate-actions',corpus/'events.json',
                         '--state-dir',work/('corporate-'+example))
        assert cli_result['response']['result']['outputs']==result['outputs']
        manual = expected if example=='indicator' else expected[-1:]
        assert [row['values'] for row in result['outputs']]==[row['values'] for row in manual]
        if example=='strategy':
            fills = [row['value'] for row in http(route+'/ledger?limit=100')['events'] if row['kind']=='fill']
            assert [(f['bar_index'],f['price']) for f in fills]==[(7,'107')],fills
        else:
            from dataclasses import asdict
            from packages.contracts.contexts import MarketContext
            from packages.contracts.realtime import MarketEvent
            candles = json.loads(bars.read_bytes())['bars']
            context = MarketContext('primary','TEST:B','1','offline')
            replay = http('/api/replays',{'input_ref':compiled['input_ref'],'primary_id':'primary',
                         'contexts':[asdict(context)],'broker_settings':None,'timeout_seconds':120,
                         'corporate_snapshot_id':ref})['replay_id']
            base = '/api/replays/'+replay
            events = [MarketEvent(i,'primary','close',b['time'],b['time']+60000,0,b['open'],b['high'],
                                  b['low'],b['close'],b['volume']).payload() for i,b in enumerate(candles)]
            http(base+'/events',{'events':events})
            http(base+'/events',{'events':events[-1:]})
            prefix = http(base+'/advance',{'max_events':3,'checkpoint_every':1})
            assert prefix['confirmed_bars']==3
            resumed = http(base+'/advance',{'max_events':20,'checkpoint_every':2})
            chart = http(base+'/chart')['rows']
            assert resumed['confirmed_bars']==8
            assert [[row[-1] for row in chart if row[2]==site['output_id']] for site in resumed['outputs']]==[r['values'] for r in manual]
