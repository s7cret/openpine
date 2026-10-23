"""Protective strategies through installed CLI, HTTP and research reports."""
import time
from packages.compiler.semantic.specification import ROOT
from packages.contracts.protection import PROFILE
from packages.contracts.wire import digest


def verify(http,cli,work):
    example=ROOT/'examples/protection'
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    for version in (1,6):
        source=example/f'v{version}.pine'
        compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.name})
        job=http('/api/jobs',{'schema_version':'openpine.job-request.3','input_ref':compiled['input_ref'],
            'snapshot_id':snapshot,'timeout_seconds':120,'checkpoint_interval_bars':1,'pause_after_bars':1})['job_id']
        def finish(path,status,timeout=120):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                state=http(path)
                if state['status']==status:return state
                if state['status'] in ('failed','cancelled','succeeded'):raise AssertionError(state)
                time.sleep(.1)
            raise TimeoutError(state)
        finish('/api/jobs/'+job,'paused')
        http(f'/api/jobs/{job}/resume',{})
        state=finish('/api/jobs/'+job,'succeeded')
        result=http(f'/api/jobs/{job}/result')['result']
        if state['resumed_from_bar']!=1 or result['simulation']['statistics']['equity']!='995':raise AssertionError(state)
        if result['simulation']['policy']['profile_id']!=PROFILE:raise AssertionError('Installed protection policy differs.')
        local=cli('job','--source',source,'--bars',example/'bars.json','--state-dir',work/f'protection-{version}')
        if local['response']['result']!=result:raise AssertionError('Installed protective CLI/HTTP results differ.')
        chart=http(f'/api/jobs/{job}/chart?start=1&limit=2')
        ledger=http(f'/api/jobs/{job}/ledger?start=0&limit=100')
        if chart['schema_version']!='openpine.chart-window.2' or sum(e['kind']=='fill' for e in ledger['events'])!=2:
            raise AssertionError('Installed protective range chart/ledger differs.')
        payload={'name':'Installed protective fold','input_ref':compiled['input_ref'],'snapshot_id':snapshot,
            'timeout_seconds':120,'space':{'stopPrice':[94.0,95.0]},'method':'grid','budget':2,'seed':0,
            'objective':{'metric':'net_profit','direction':'maximize'},
            'walkforward':{'mode':'rolling','start_ms':0,'train_ms':240000,'test_ms':240000,
                'step_ms':240000,'warmup_ms':0,'folds':1,'max_jobs':3}}
        identifier=http('/api/walkforwards',payload)['walkforward_id']
        state=finish('/api/walkforwards/'+identifier,'succeeded',180)
        receipt=state['folds'][0]['receipt']
        if receipt['decision']['winner_ordinal']!=1 or receipt['holdout']['assessment']['score']!='-5':raise AssertionError(state)
        report=http(f'/api/walkforwards/{identifier}/report')
        if report['sha256']!=digest(report['report']):raise AssertionError('Installed protection report hash differs.')
        for variant in report['report']['variants']:
            if variant['plan']['simulation_policy']['profile_id']!=PROFILE or variant['descriptive_metrics']['values']['closed_trades']!='1':
                raise AssertionError('Installed report lost protective policy/trades.')
    return {'protection_cli_http_versions':[1,6],'protection_pending_resume':True,'protection_range_chart':True,
        'protection_walkforward_versions':[1,6],'protection_report_hash':True}
