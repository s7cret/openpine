"""Installed eighteen multi-lot strategies through prepared jobs, CLI, HTTP and research."""
import json
import sys
import time
import uuid
from tools.specification import ROOT
from tools.lot_oracle import expected,cycle_totals
from packages.contracts.wire import digest

def verify(http,cli,work):
    root=ROOT/'examples/lots';jobs=[];base=None;families=('fifo','any','conditional');timings=[];research_timings=[]
    snapshots={family:http('/api/snapshots',{'provider':'upload','raw':(root/f'{family}-bars.json').read_text()})['sha256'] for family in families}
    def finish(path,target='succeeded'):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            state=http(path)
            if state['status']==target:return state
            if state['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)
    for version in range(1,7):
        for family in families:
            print(f'Installed lots: {family} v{version}',file=sys.stderr,flush=True)
            started=time.perf_counter();phases={}
            path=root/f'{family}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()});cut=5 if family=='conditional' else 3
            ready=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
                'primary':{'kind':'snapshot','snapshot_id':snapshots[family]},'contexts':[],'inputs':{'part':1},'timeout_seconds':120,
                'gap_policy':'fail','checkpoint_interval_bars':1,'pause_after_bars':cut})
            if ready['status']!='ready':raise AssertionError(ready)
            phases['compile_and_prepare']=time.perf_counter()-started;started=time.perf_counter()
            key=http('/api/launches/'+ready['launch_id']+'/start',{})['job_id'];jobs.append(key)
            finish('/api/jobs/'+key,'paused');phases['start_to_pause']=time.perf_counter()-started;started=time.perf_counter()
            http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
            phases['resume_to_success']=time.perf_counter()-started;started=time.perf_counter()
            result=http('/api/jobs/'+key+'/result')['result']
            if state['resumed_from_bar']!=cut or [o['values'] for o in result['outputs']]!=[list(v) for v in expected(family,1)]:raise AssertionError('Lot outputs differ from independent phase/quantity/fee oracle.')
            stats=result['simulation']['statistics']
            cycles=50 if family=='conditional' else 60;totals=cycle_totals(family,1)
            if (stats['trades'],stats['closed_positions'],stats['closed_lots'],stats['entries'],stats['lots'])!=(cycles*totals['trades'],cycles,cycles*2,[],[]):raise AssertionError(stats)
            artifact=work/f'lot-ir-{family}-{version}.json';override=work/f'lot-inputs-{family}-{version}.json';override.write_text('{"part":1}')
            cli('compile','--source',path,'--profile','historical','--output',artifact)
            direct=cli('job','--artifact',artifact,'--bars',root/f'{family}-bars.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'lot-cli-{family}-{version}')
            if direct['response']['result']!=result:raise AssertionError('Lot saved CLI/HTTP differs.')
            phases['result_and_saved_ir_cli']=time.perf_counter()-started;started=time.perf_counter()
            chart=http(f'/api/jobs/{key}/chart?start=1&limit=6');events=chart['protection']['events']
            if chart['schema_version']!='openpine.chart-window.5' or not any(e['kind']=='entry_trigger' for e in events):raise AssertionError('Entry triggers absent from range chart.')
            if family=='conditional' and not any(e['kind']=='entry_activated' and e['value']['limit_eligible_bar']==3 for e in events):raise AssertionError('Stop-limit phase/deferral absent from chart.')
            if not any(e['kind']=='trade' and e['value'].get('lot_id') and 'position_remaining_quantity' in e['value'] for e in events):raise AssertionError('Exact lot slice missing from chart.')
            if not any(e['kind']=='mark' and len(e['value']['lots'])==2 for e in events):raise AssertionError('Multiple lots missing from range chart.')
            if http(f'/api/jobs/{key}/configuration')['plan']['settings']['values']['input:part']!=1:raise AssertionError('Lot settings not pinned.')
            if version==6 and family=='conditional':base=ready['base']
            phases['chart_and_configuration']=time.perf_counter()-started
            timings.append({'family':family,'version':version,'bars':400 if family=='conditional' else 360,'checkpoint_interval_bars':1,
                'pause_after_bars':cut,'worker_timeout_seconds':120,'wall_seconds':{k:round(v,6) for k,v in phases.items()}})
    for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed lots: '+route,file=sys.stderr,flush=True)
        started=time.perf_counter()
        made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];finish(path);report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Lot research report differs.')
        research_timings.append({'route':route,'wall_seconds':round(time.perf_counter()-started,6)})
    return {'lot_cli_http_versions':list(range(1,7)),'lot_families':list(families),'lot_prepared_jobs':jobs,
        'lot_saved_ir':True,'lot_independent_quantity_fee_oracle':True,'lot_activation_and_partial_resume':True,
        'lot_range_chart':True,'lot_frozen_settings':True,'lot_experiment':True,'lot_search_holdout':True,'lot_walkforward':True,
        'lot_phase_timings':timings,'lot_research_timings':research_timings,
        'lot_timing_scope':'Coordinator wall time including worker startup, persistence and polling; not isolated CPU time.'}
