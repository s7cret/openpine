"""All grouped-order examples through installed saved IR, workers, HTTP and research."""
import json
import sys
import time
import uuid
from tools.specification import ROOT
from tools.order_group_oracle import expected,cycle_totals,text
from packages.contracts.wire import digest


def verify(http,cli,work):
    root=ROOT/'examples/order-groups';families=('netting','cancel','reduce');jobs=[];base=None
    snapshots={family:http('/api/snapshots',{'provider':'upload','raw':(root/f'{family}-bars.json').read_text()})['sha256'] for family in families}
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
            print(f'Installed order groups: {family} v{version}',file=sys.stderr,flush=True)
            path=root/f'{family}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
            cut=4 if family=='reduce' else 2 if family=='cancel' else 3
            launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
                'primary':{'kind':'snapshot','snapshot_id':snapshots[family]},'contexts':[],'inputs':{'part':1},'timeout_seconds':120,
                'gap_policy':'fail','checkpoint_interval_bars':1,'pause_after_bars':cut})
            if launch['status']!='ready':raise AssertionError(launch)
            key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];jobs.append(key)
            finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
            result=http('/api/jobs/'+key+'/result')['result'];totals=cycle_totals(family,1);cycles=50 if family=='reduce' else 60
            if state['resumed_from_bar']!=cut or [o['values'] for o in result['outputs']]!=[list(s) for s in expected(family,1)]:raise AssertionError('Group outputs differ from the independent cash/quantity/fee oracle.')
            stats=result['simulation']['statistics']
            if result['simulation']['schema_version']!='openpine.simulation.6' or stats['order_groups']!=[]:raise AssertionError('Final group state differs.')
            for name in ('orders','fills','trades','closed_lots','closed_positions'):
                if stats[name]!=cycles*totals[name]:raise AssertionError((name,stats[name]))
            if stats['entry_commission']!=text(cycles*totals['entry_fee']) or stats['exit_commission']!=text(cycles*totals['exit_fee']):raise AssertionError('Group fee residues differ.')
            artifact=work/f'group-ir-{family}-{version}.json';override=work/f'group-inputs-{family}-{version}.json';override.write_text('{"part":1}')
            cli('compile','--source',path,'--profile','historical','--output',artifact)
            direct=cli('job','--artifact',artifact,'--bars',root/f'{family}-bars.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'group-cli-{family}-{version}')
            if direct['response']['result']!=result:raise AssertionError('Saved group CLI/HTTP differ.')
            chart=http(f'/api/jobs/{key}/chart?start=0&limit=8')
            if chart['schema_version']!='openpine.chart-window.6':raise AssertionError('Group chart profile differs.')
            events=chart['protection']['events']
            if family=='reduce':
                marks=[e for e in events if e['kind']=='mark' and e['bar_index']==3]
                if marks[0]['value']['order_groups'][0]['members'][0]['remaining_quantity']!='3' or marks[0]['value']['lots'][0]['quantity']!='4':raise AssertionError('Exact OCA member/lot quantity differs.')
                if not any(e['kind']=='entry_activated' and e['value']['activated_bar']==1 for e in events):raise AssertionError('Stop-limit activation absent.')
            if family!='netting' and not any(e['kind']=='oca_filled' for e in events):raise AssertionError('OCA lifecycle absent from chart.')
            if http(f'/api/jobs/{key}/configuration')['plan']['settings']['values']['input:part']!=1:raise AssertionError('Group inputs are not pinned.')
            if version==6 and family=='reduce':base=launch['base']
    reports={}
    for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
        print('Installed order groups: '+route,file=sys.stderr,flush=True)
        made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];finish(path)
        report=http(path+'/report')
        if report['sha256']!=digest(report['report']) or not report['report']['variants']:raise AssertionError('Group research report differs.')
        reports[name]=report['sha256']
    return {'group_cli_http_versions':list(range(1,7)),'group_families':list(families),'group_prepared_jobs':jobs,
        'group_saved_ir':True,'group_independent_netting_fee_oracle':True,'group_activation_reduce_and_close_all_resume':True,
        'group_bounded_chart':True,'group_frozen_settings':True,'group_experiment':True,'group_search_holdout':True,'group_walkforward':True,
        'group_report_hashes':reports}
