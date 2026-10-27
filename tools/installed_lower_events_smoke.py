"""Installed saved IR, finite tapes, partial-worker resume and HTTP parity."""
import json
from tools.specification import ROOT

def verify(http,cli,work):
    root=ROOT/'examples/lower-events';config=json.loads((root/'contexts.json').read_text())
    events=[json.loads(line) for line in (root/'events.ndjson').read_text().splitlines()];expected=json.loads((root/'expected.json').read_text())
    for v in (5,6):
        source=root/f'v{v}.pine';store=work/f'lower-events-{v}';artifact=work/f'lower-events-{v}-ir.json'
        cli('compile','--source',source,'--profile','historical','--output',artifact)
        rid=cli('replay','create','--artifact',artifact,'--contexts',root/'contexts.json','--state-dir',store)['replay_id']
        cli('replay','append','--id',rid,'--events',root/'events.ndjson','--state-dir',store)
        prefix=cli('replay','advance','--id',rid,'--max-events','13','--checkpoint-every','2','--state-dir',store)
        assert prefix['confirmed_bars']==2 and prefix['lower_tf']['pending_cells']==3
        final=cli('replay','advance','--id',rid,'--checkpoint-every','3','--state-dir',store)
        compiled=http('/api/compile',{'source':source.read_text(),'source_name':source.name})
        other=http('/api/replays',{'input_ref':compiled['input_ref'],**config,'broker_settings':None,'timeout_seconds':120})['replay_id']
        http(f'/api/replays/{other}/events',{'events':events});http(f'/api/replays/{other}/advance',{'max_events':13,'checkpoint_every':2})
        result=http(f'/api/replays/{other}/advance',{'max_events':100,'checkpoint_every':3})
        a=cli('replay','chart','--id',rid,'--state-dir',store)['rows'];b=http(f'/api/replays/{other}/chart')['rows']
        assert a==b and final['lower_tf']==result['lower_tf']
        assert [[row[-1] for row in a if row[2]==site['output_id']] for site in result['outputs']]==[expected['counts'],expected['sums']]
        assert result['lower_tf']['pending_cells']==0 and result['confirmed_bars']==4
    return {'lower_events_installed_versions':[5,6],'lower_events_saved_ir_cli_http':True,'lower_events_pending_window_resume':True}
