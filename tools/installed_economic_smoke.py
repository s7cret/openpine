"""Economic releases from the shared wheel through the public CLI and HTTP API."""
from uuid import uuid4

from packages.contracts.wire import canonical, read_json
from tools.installed_input_smoke import wait_job
from tools.specification import ROOT


def verify(http, cli, work):
    folder = ROOT/'conformance/economic-data'
    bars = read_json(folder/'bars.json')
    env = read_json(ROOT/'examples/environment/input.json')['environment']
    original = http('/api/corporate-actions',read_json(folder/'events.json'))['sha256']
    assert http('/api/corporate-actions',read_json(folder/'events.json'))['sha256'] == original
    first, end = bars['bars'][0]['time'],bars['bars'][-1]['time']+86400000
    for currency in ('USD','EUR'):
        market = dict(schema_version='openpine.market-input.1',data=bars,
                      environment={**env,'instrument':{**env['instrument'],'currency':currency}})
        path = work/f'economic-{currency}.json'
        path.write_bytes(canonical(market))
        snapshot = http('/api/snapshots',dict(provider='upload',raw=path.read_text()))['sha256']
        samples = ('indicator','strategy','mixed') if currency=='USD' else ('indicator','strategy')
        for sample in samples:
            source = folder/'v6-mixed.pine' if sample=='mixed' else ROOT/'examples/economic-data'/(sample+'.pine')
            compiled = http('/api/compile',dict(source=source.read_text(),source_name=source.name))
            artifact = work/f'economic-{sample}.ir.json'
            artifact.write_bytes(canonical(http('/api/artifacts/'+compiled['artifact_ref'])))
            args = ('--bars',path,'--state-dir',work/'economic-jobs')
            config = dict(schema_version='openpine.launch-request.1',input_ref=compiled['input_ref'],
                          primary=dict(kind='snapshot',snapshot_id=snapshot),contexts=[],inputs={},
                          timeout_seconds=180,gap_policy='allow_with_metadata',checkpoint_interval_bars=1)
            if sample == 'mixed':
                config['fx_snapshot_id'] = http('/api/fx-rates',read_json(folder/'rates.json'))['sha256']
                args += ('--fx-rates',folder/'rates.json')

            def start(ref, **options):
                ready = http('/api/launches',dict(config,corporate_snapshot_id=ref,request_id=uuid4().hex,**options))
                assert ready['status']=='ready',ready
                job_id = http('/api/launches/'+ready['launch_id']+'/start',{})['job_id']
                return '/api/jobs/'+job_id, ready['prepared']['requirements']

            paused,plan = start(original,pause_after_bars=1)
            wait_job(http,paused,'paused')
            revised = http('/api/corporate-actions',read_json(folder/'revised.json'))['sha256']
            assert original != revised
            preview = cli('job','--source',source,*args,'--corporate-actions',folder/'events.json','--plan-only')
            assert preview['corporate_actions'] == plan['corporate_actions']
            requests = [r for r in plan['corporate_actions'] if r['family']=='economic']
            assert [r['field'] for r in requests] == (['GDP','GDPQQ'] if sample=='indicator' else ['GDPQQ'])
            assert all((r['country_code'],r['snapshot_id'],r['start_ms'],r['end_ms'])==
                       ('US',original,first,end) for r in requests)
            assert preview.get('fx_rates',[]) == plan.get('fx_rates',[])
            http(paused+'/resume',{})
            assert wait_job(http,paused,'succeeded')['resumed_from_bar']==1
            fresh,_ = start(revised)
            wait_job(http,fresh,'succeeded')
            for route, updated in ((paused,False),(fresh,True)):
                name = 'revised.json' if updated else 'events.json'
                result = http(route+'/result')['result']
                native = cli('job','--artifact',artifact,*args,'--corporate-actions',folder/name)['response']['result']
                assert native['outputs']==result['outputs']
                growth = [None]+[-.4]*5+([.2,.2] if updated else [-.4,-.4])
                expected = {'indicator':[[None]*4+[26000.]*4,growth],'strategy':[growth],
                            'mixed':[growth,[None]*4+[100.]*4,[.5,.5]+[.8]*6]}[sample]
                assert [o['values'] for o in result['outputs']] == expected
                if sample=='strategy':
                    assert native['simulation']==result['simulation']
                    ledger = http(route+'/ledger?limit=100')['events']
                    assert [r['bar_index'] for r in ledger if r['kind']=='order'] == ([6] if updated else [])
                    assert [(r['bar_index'],r['value']['price']) for r in ledger if r['kind']=='fill'] == (
                        [(7,'107')] if updated else [])
