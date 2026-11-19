"""Fiscal-year reports in the chart currency across CLI, HTTP and isolated workers."""
from uuid import uuid4

from packages.contracts.wire import canonical, read_json
from tools.installed_input_smoke import wait_job
from tools.specification import ROOT


def verify(http, cli, work):
    folder = ROOT/'conformance/financial'
    bars = read_json(folder/'fiscal-bars.json')
    start, end = bars['bars'][0]['time'], bars['bars'][-1]['time']+86400000
    instrument = read_json(ROOT/'examples/environment/input.json')['environment']
    data = read_json(folder/'fiscal-events.json')
    original = http('/api/corporate-actions', data)['sha256']
    rate_ref = http('/api/fx-rates', read_json(folder/'fiscal-rates.json'))['sha256']

    for currency in ('USD','EUR'):
        market = dict(schema_version='openpine.market-input.1',data=bars,
                      environment={**instrument,'instrument':{**instrument['instrument'],'currency':currency}})
        path = work/f'fiscal-{currency}.json'
        path.write_bytes(canonical(market))
        primary = http('/api/snapshots', dict(provider='upload',raw=path.read_text()))['sha256']
        for example in ('indicator','strategy'):
            source = ROOT/'examples/financial'/f'fiscal-{example}.pine'
            compiled = http('/api/compile',dict(source=source.read_text(),source_name=source.name))
            artifact = work/f'fiscal-{example}-ir.json'
            artifact.write_bytes(canonical(http('/api/artifacts/'+compiled['artifact_ref'])))
            args = ('--bars',path,'--state-dir',work/'fiscal-jobs')
            rates = ('--fx-rates',folder/'fiscal-rates.json') if currency=='EUR' else ()
            def launch(ref, *, pause=False):
                request = dict(schema_version='openpine.launch-request.1',input_ref=compiled['input_ref'],
                               request_id=uuid4().hex, primary=dict(kind='snapshot',snapshot_id=primary),
                               corporate_snapshot_id=ref, contexts=[], inputs={},timeout_seconds=180,
                               gap_policy='allow_with_metadata',checkpoint_interval_bars=1)
                if rates:
                    request['fx_snapshot_id'] = rate_ref
                if pause:
                    request['pause_after_bars'] = 1
                ready = http('/api/launches',request)
                assert ready['status']=='ready',ready
                route = '/api/jobs/'+http('/api/launches/'+ready['launch_id']+'/start',{})['job_id']
                return route,ready['prepared']['requirements']

            paused, planned = launch(original,pause=True)
            wait_job(http,paused,'paused')
            revision = http('/api/corporate-actions',read_json(folder/'fiscal-revised.json'))['sha256']
            assert revision != original
            preview = cli('job','--source',source,*args,*rates,'--corporate-actions',folder/'fiscal-events.json','--plan-only')
            assert preview['corporate_actions'] == planned['corporate_actions']
            assert all((r['symbol'],r['period'],r['start_ms'],r['end_ms'],r['snapshot_id']) ==
                       ('TEST:B','FY',start,end,original) for r in planned['corporate_actions'])
            metrics = {r['financial_id'] for r in planned['corporate_actions']}
            assert metrics == ({'TOTAL_REVENUE','GROSS_PROFIT','TOTAL_SHARES_OUTSTANDING','EARNINGS_PER_SHARE_BASIC'}
                               if example == 'indicator' else {'TOTAL_REVENUE'})
            assert preview.get('fx_rates',[]) == planned.get('fx_rates',[])
            pairs = planned.get('fx_rates',[])
            if rates:
                assert {(r['base'],r['quote'],r['start_ms'],r['end_ms'],r['snapshot_id']) for r in pairs} == {
                    ('USD','EUR',start,end,rate_ref)}
                assert len(pairs) == (3 if example == 'indicator' else 1)
            else:
                assert not pairs
            http(paused+'/resume',{})
            assert wait_job(http,paused,'succeeded')['resumed_from_bar']==1
            fresh,_ = launch(revision)
            wait_job(http,fresh,'succeeded')
            for route, revised in ((paused,False),(fresh,True)):
                result = http(route+'/result')['result']
                filename = 'fiscal-revised.json' if revised else 'fiscal-events.json'
                native = cli('job','--artifact',artifact,*args,*rates,'--corporate-actions',folder/filename)
                assert native['response']['result']['outputs'] == result['outputs']
                factor = .5 if rates else 1.
                revenue = 120. if revised else 100.
                if example == 'indicator':
                    values = [revenue*factor,25.*factor,25./revenue,12.,-3.*factor]
                    assert [o['values'] for o in result['outputs']] == [[None]+[v]*7 for v in values]
                else:
                    assert result['outputs'][0]['values'] == [None,revenue*factor]+[None]*6
                    ledger = http(route+'/ledger?limit=100')['events']
                    bought = revenue*factor > 55
                    assert [r['bar_index'] for r in ledger if r['kind']=='order'] == ([1] if bought else [])
                    assert [(r['bar_index'],r['value']['price']) for r in ledger if r['kind']=='fill'] == (
                        [(2,'102')] if bought else [])
