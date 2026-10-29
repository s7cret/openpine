"""Research reports and temporal folds from an installed wheel outside checkout."""
import json
import time
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import digest


def verify(http, cli, work, base):
    payload=json.loads((ROOT/'examples/walkforward/request.json').read_text(encoding='utf-8'))
    payload.update(budget=1,space={'n':[1]})
    payload['walkforward'].update(folds=1,max_jobs=2)
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/inputs/bars.json').read_text(encoding='utf-8')})['sha256']
    for version in (1,6):
        source=ROOT/f'examples/search/v{version}.pine'
        compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.name})
        created=http('/api/walkforwards',{**payload,'input_ref':compiled['input_ref'],'snapshot_id':snapshot})
        identifier=created['walkforward_id'];deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            state=http('/api/walkforwards/'+identifier)
            if state['status']!='running':break
            time.sleep(.1)
        if state['status']!='succeeded' or state['folds'][0]['receipt']['holdout']['assessment']['score']!='13.0':raise AssertionError(state)
        report=http('/api/walkforwards/'+identifier+'/report')
        if report['sha256']!=digest(report['report']):raise AssertionError('Installed report digest differs.')
        if cli('walkforward','report','--id',identifier,'--server',base)!=report:raise AssertionError('CLI client opened different research.')
        preview=cli('walkforward','delete-preview','--id',identifier,'--server',base)
        cli('walkforward','delete-apply','--id',identifier,'--server',base,'--preview-id',preview['preview_id'])
    request=work/'walkforward-request.json';request.write_text(json.dumps(payload),encoding='utf-8')
    state=cli('walkforward','run','--request',request,'--source',ROOT/'examples/search/v6.pine',
              '--bars',ROOT/'examples/inputs/bars.json','--state-dir',work/'walkforward-cli')
    if state['status']!='succeeded':raise AssertionError(state)
    from tools.installed_trace import verify_cli as verify_trace_cli
    tracing=verify_trace_cli(http,cli,base)
    return {**tracing,'walkforward_http_versions':[1,6],'walkforward_cli_version':6,'research_report_hash':True,
            'server_owned_cli':True,'research_preview_delete':True}
