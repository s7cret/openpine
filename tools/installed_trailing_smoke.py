"""Installed relative/trailing examples through saved IR, workers and HTTP."""
import json,sys,time,uuid
from tools.specification import ROOT
from tools.trailing_oracle import expected,cycle_totals,text
from packages.contracts.wire import digest


def verify(http,cli,work):
    root=ROOT/'examples/trailing';families=('relative','points','price');jobs=[];base=None
    snapshots={f:http('/api/snapshots',{'provider':'upload','raw':(root/f'{f}-input.json').read_text()})['sha256'] for f in families}
    def finish(path,target='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            value=http(path)
            if value['status']==target:return value
            if value['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(value)
            time.sleep(.1)
        raise TimeoutError(value)
    for version in range(1,7):
        for family in families:
            print(f'Installed trailing: {family} v{version}',file=sys.stderr,flush=True)
            path=root/f'{family}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
            cut=4 if family=='points' else 2
            launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
                'primary':{'kind':'snapshot','snapshot_id':snapshots[family]},'contexts':[],'inputs':{'distance':3},'timeout_seconds':120,
                'gap_policy':'fail','checkpoint_interval_bars':8,'pause_after_bars':cut})
            if launch['status']!='ready':raise AssertionError(launch)
            key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];jobs.append(key)
            finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
            result=http('/api/jobs/'+key+'/result')['result'];totals=cycle_totals(family)
            if state['resumed_from_bar']!=cut or [o['values'] for o in result['outputs']]!=[list(s) for s in expected(family)]:raise AssertionError('Trailing outputs differ from closed-form oracle.')
            stats=result['simulation']['statistics']
            if result['simulation']['schema_version']!='openpine.simulation.7' or stats['exit_states']!=[]:raise AssertionError('Final trailing state differs.')
            for name in ('orders','fills','trades','closed_lots','closed_positions'):
                if stats[name]!=60*totals[name]:raise AssertionError((name,stats[name]))
            if stats['entry_commission']!=text(60*totals['entry_fee']) or stats['exit_commission']!=text(60*totals['exit_fee']):raise AssertionError('Trailing fee residues differ.')
            artifact=work/f'trailing-ir-{family}-{version}.json';override=work/f'trailing-inputs-{family}-{version}.json';override.write_text('{"distance":3}')
            cli('compile','--source',path,'--profile','historical','--output',artifact)
            direct=cli('job','--artifact',artifact,'--bars',root/f'{family}-input.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'trailing-cli-{family}-{version}')
            if direct['response']['result']!=result:raise AssertionError('Saved trailing CLI/HTTP differ.')
            chart=http(f'/api/jobs/{key}/chart?start=0&limit=8')
            if chart['schema_version']!='openpine.chart-window.7' or chart['protection']['event_bytes']>chart['protection']['event_byte_budget']:raise AssertionError('Trailing chart budget/profile differs.')
            if family=='points':
                mark=next(e for e in chart['protection']['events'] if e['kind']=='mark' and e['bar_index']==3)
                t=mark['value']['exit_states'][0]['targets'][0]
                if (t['activation'],t['extreme'],t['effective_stop'],t['trail_stop'],t['stop_eligible_bar'])!=('105','112','107','109',4):raise AssertionError('Current/next trailing stop differs.')
            configuration=http(f'/api/jobs/{key}/configuration')['plan']
            if configuration['settings']['values']['input:distance']!=3:raise AssertionError('Trailing distance is not pinned.')
            if version==6 and family=='points':base=launch['base']
    reports={}
    for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed trailing: '+route,file=sys.stderr,flush=True)
        made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];finish(path)
        report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Trailing research report differs.')
        reports[name]=report['sha256']
    return {'trailing_cli_http_versions':list(range(1,7)),'trailing_families':list(families),'trailing_prepared_jobs':jobs,
        'trailing_saved_ir':True,'trailing_independent_price_fee_oracle':True,'trailing_current_and_next_stop_resume':True,
        'trailing_bounded_chart':True,'trailing_frozen_settings':True,'trailing_experiment':True,'trailing_search_holdout':True,'trailing_walkforward':True,
        'trailing_report_hashes':reports}
