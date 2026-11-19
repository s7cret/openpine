"""Exercise explicit symbol resolution through CLI, HTTP and resumed workers."""
from uuid import uuid4
from packages.contracts.wire import canonical
from tools.specification import ROOT
from tools.installed_input_smoke import wait_job


def verify(http,cli,work):
    folder = ROOT/'conformance/optional-symbols'
    def upload(name):
        return http('/api/snapshots',{'provider':'upload','raw':(folder/name).read_text()})['sha256']
    main, second = upload('market.json'), upload('secondary.json')
    for sample in ('indicator','strategy'):
        source = ROOT/'examples/optional-symbols'/(sample+'.pine')
        compiled = http('/api/compile',dict(source=source.read_text(),source_name=source.name))
        artifact = work/f'optional-{sample}.ir.json'
        artifact.write_bytes(canonical(http('/api/artifacts/'+compiled['artifact_ref'])))
        args = ('--bars',folder/'market.json','--contexts',folder/'contexts.json','--state-dir',work/'symbol-cli')
        preview = cli('job','--artifact',artifact,*args,'--plan-only')['plan']
        launch = http('/api/launches',dict(schema_version='openpine.launch-request.1',request_id=uuid4().hex,
            input_ref=compiled['input_ref'],primary=dict(kind='snapshot',snapshot_id=main),
            contexts=[dict(kind='snapshot',snapshot_id=second)],inputs={},timeout_seconds=120,
            gap_policy='allow_with_metadata',checkpoint_interval_bars=1,pause_after_bars=1))
        assert launch['status']=='ready',launch
        sites = preview['symbol_resolutions']
        assert sites == launch['prepared']['plan']['symbol_resolutions']
        assert [r['resolution'] for r in sites] == ['bound','unknown_symbol']
        assert [r['snapshot_id'] for r in sites] == [second,None]
        path = '/api/jobs/'+http('/api/launches/'+launch['launch_id']+'/start',{})['job_id']
        wait_job(http,path,'paused')
        http(path+'/resume',{})
        assert wait_job(http,path,'succeeded')['resumed_from_bar']==1
        result = http(path+'/result')['result']
        native = cli('job','--artifact',artifact,*args)['response']['result']
        assert result['outputs'] == native['outputs']
        assert [o['values'] for o in result['outputs']] == [[None,10.,10.,10.,10.,30.],[None]*6]
        if sample=='strategy':
            assert result['simulation']==native['simulation']
            ledger = http(path+'/ledger?limit=100')['events']
            assert [r['bar_index'] for r in ledger if r['kind']=='order']==[1]
            assert [(r['bar_index'],r['value']['price']) for r in ledger if r['kind']=='fill']==[(2,'3')]
