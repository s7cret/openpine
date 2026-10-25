"""Installed economic modes through frozen launches, saved IR and research."""
import json,sys,time,uuid
from decimal import Decimal
from tools.specification import ROOT
from tools.economics_oracle import expected,KINDS
from tools.economics_fixtures import SETTINGS
from packages.contracts.wire import digest
def verify(http,cli,work):
 root=ROOT/'examples/economics';snapshots={f:http('/api/snapshots',{'provider':'upload','raw':(root/f'{f}-input.json').read_text()})['sha256'] for f in KINDS};jobs=[];base=None
 def finish(path,target='succeeded'):
  deadline=time.monotonic()+240
  while time.monotonic()<deadline:
   v=http(path)
   if v['status']==target:return v
   if v['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(v)
   time.sleep(.1)
  raise TimeoutError(v)
 for version in range(1,7):
  for f in KINDS:
   print(f'Installed economics: {f} v{version}',file=sys.stderr,flush=True)
   path=root/f'{f}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()});cut={'cash':2,'equity':4,'flat':3}[f]
   launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
    'primary':{'kind':'snapshot','snapshot_id':snapshots[f]},'contexts':[],'inputs':{'manual':0.},'timeout_seconds':180,'gap_policy':'fail','checkpoint_interval_bars':8,'pause_after_bars':cut})
   if launch['status']!='ready':raise AssertionError(launch)
   key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];jobs.append(key);finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
   result=http('/api/jobs/'+key+'/result')['result'];values,stats=expected(f)
   if state['resumed_from_bar']!=cut or [o['values'] for o in result['outputs']]!=[list(s) for s in values]:raise AssertionError('Economic outputs differ from independent recurrence.')
   if result['simulation']['schema_version']!='openpine.simulation.8':raise AssertionError('Economic simulation profile differs.')
   for field in ('equity_exact','cash_exact','gross_realized_exact','entry_commission','exit_commission','allocated_entry_commission','commission','orders','fills','trades','closed_lots','closed_positions','economics'):
    if result['simulation']['statistics'][field]!=stats[field]:raise AssertionError((f,field))
   configuration=http(f'/api/jobs/{key}/configuration')['plan']
   if configuration['simulation_policy']['settings']!=SETTINGS or configuration['settings']['values']['input:manual']!=0.:raise AssertionError('Economic settings and rules are not pinned.')
   artifact=work/f'economic-ir-{f}-{version}.json';override=work/f'economic-inputs-{f}-{version}.json';override.write_text('{"manual":0.0}')
   cli('compile','--source',path,'--profile','historical','--output',artifact)
   direct=cli('job','--artifact',artifact,'--bars',root/f'{f}-input.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'economic-cli-{f}-{version}')
   if direct['response']['result']!=result:raise AssertionError('Saved economic CLI and prepared HTTP differ.')
   chart=http(f'/api/jobs/{key}/chart?start=0&limit=8');events=chart['protection']['events']
   if chart['schema_version']!='openpine.chart-window.8' or chart['protection']['event_bytes']>chart['protection']['event_byte_budget']:raise AssertionError('Economic chart profile/budget differs.')
   if not all(any(e['kind']==kind for e in events) for kind in ('quantity_resolved','commission_calculated')):raise AssertionError('Economic explanation events are absent.')
   if version==6 and f=='cash':base=launch['base']
 reports={}
 for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
  print('Installed economics: '+route,file=sys.stderr,flush=True)
  made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];state=finish(path);report=http(path+'/report')
  if report['sha256']!=digest(report['report']):raise AssertionError('Economic report digest differs.')
  for row in report['report']['variants']:
   manual=int(row['inputs']['input:manual']);cycles=60 if name=='experiment' else 10 if row['phase']=='holdout' else 40 if name=='search' else 20
   _,stats=expected('cash',manual,cycles);metrics=row['descriptive_metrics']['values']
   for key,want in {'net_profit':Decimal(stats['equity_exact'])-1000,'ending_equity':Decimal(stats['equity_exact']),'commission':Decimal(stats['commission'])}.items():
    if Decimal(metrics[key])!=want:raise AssertionError((name,key,metrics[key],want))
  if name=='search' and (state['decision']['winner_ordinal']!=1 or Decimal(state['holdout']['assessment']['score'])!=158):raise AssertionError('Economic selection/holdout differs.')
  if name=='walkforward' and (len(state['folds'])!=2 or any(Decimal(fold['receipt']['holdout']['assessment']['score'])!=158 for fold in state['folds'])):raise AssertionError('Economic folds differ.')
  reports[name]=report['sha256']
 return {'economics_cli_http_versions':list(range(1,7)),'economics_families':list(KINDS),'economics_prepared_jobs':jobs,
  'economics_saved_ir':True,'economics_independent_size_and_fee_oracle':True,'economics_pinned_rules_and_resume':True,
  'economics_bounded_chart_explanations':True,'economics_experiment':True,'economics_search_holdout':True,'economics_walkforward':True,'economics_report_hashes':reports}
