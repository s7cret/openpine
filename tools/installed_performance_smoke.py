"""Installed trade observations through CLI, HTTP, saved IR and research."""
import json,sys,time,uuid
from decimal import Decimal
from tools.specification import ROOT
from tools.performance_oracle import expected,FAMILIES
from tools.performance_fixtures import SETTINGS
from packages.contracts.wire import digest

def verify(http,cli,work):
 root=ROOT/'examples/performance';snapshots={f:http('/api/snapshots',{'provider':'upload','raw':(root/f'{f}-input.json').read_text()})['sha256'] for f in FAMILIES};jobs=[];base=None
 def finish(path,target='succeeded'):
  deadline=time.monotonic()+240
  while time.monotonic()<deadline:
   state=http(path)
   if state['status']==target:return state
   if state['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(state)
   time.sleep(.1)
  raise TimeoutError(state)
 for version in range(1,7):
  for family in FAMILIES:
   print(f'Installed performance: {family} v{version}',file=sys.stderr,flush=True)
   path=root/f'{family}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()})
   launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
    'primary':{'kind':'snapshot','snapshot_id':snapshots[family]},'contexts':[],'inputs':{'units':2},'timeout_seconds':180,'gap_policy':'fail','checkpoint_interval_bars':8,'pause_after_bars':3})
   if launch['status']!='ready':raise AssertionError(launch)
   key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];jobs.append(key);finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
   result=http('/api/jobs/'+key+'/result')['result'];values,stats=expected(family,2,20,version)
   if state['resumed_from_bar']!=3 or {o['title']:o['values'] for o in result['outputs']}!=values:raise AssertionError('Trade observations differ from independent cycle arithmetic.')
   if result['simulation']['schema_version']!='openpine.simulation.10':raise AssertionError('Unexpected trade simulation version.')
   actual=dict(result['simulation']['statistics']);perf=actual['performance'];actual['performance']={**perf,'archive':{'count':perf['archive']['count']}}
   if actual!=stats:raise AssertionError((family,'trade statistics',actual,stats))
   configuration=http(f'/api/jobs/{key}/configuration')['plan'];policy=configuration['simulation_policy']
   if policy['settings']!=SETTINGS or configuration['settings']['values']['input:units']!=2 or policy['profile_id']!='openpine.performance-broker.1' or policy['language_version']!=version:raise AssertionError('Trade inputs/rules/version were not pinned.')
   page=http(f'/api/jobs/{key}/trades?start=0&limit=100')
   if page['total']!=stats['trades'] or [row['closed_index'] for row in page['trades']]!=list(range(stats['trades'])):raise AssertionError('Trade history index is not stable.')
   if sum(Decimal(row['net_profit']) for row in page['trades'])!=Decimal(stats['performance']['netprofit']):raise AssertionError('Closed history differs from summary.')
   artifact=work/f'performance-ir-{family}-{version}.json';override=work/f'performance-inputs-{family}-{version}.json';override.write_text('{"units":2}')
   cli('compile','--source',path,'--profile','historical','--output',artifact)
   direct=cli('job','--artifact',artifact,'--bars',root/f'{family}-input.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'performance-cli-{family}-{version}')
   if direct['response']['result']!=result:raise AssertionError('Saved IR CLI and prepared HTTP differ.')
   local_page=cli('trades','--id',direct['job']['job_id'],'--state-dir',work/f'performance-cli-{family}-{version}','--limit','100')
   if local_page!=page:raise AssertionError('Local trade history CLI and HTTP pages differ.')
   chart=http(f'/api/jobs/{key}/chart?start=0&limit=24')
   if chart['schema_version']!='openpine.chart-window.10' or chart['protection']['event_bytes']>chart['protection']['event_byte_budget']:raise AssertionError('Trade chart profile or budget differs.')
   if not all('performance' in e['value'] for e in chart['protection']['events'] if e['kind']=='mark'):raise AssertionError('Selected-bar performance is absent.')
   if version==6 and family=='partial':base=launch['base']
 reports={}
 for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
  print('Installed performance: '+route,file=sys.stderr,flush=True)
  made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];state=finish(path);report=http(path+'/report')
  if report['sha256']!=digest(report['report']):raise AssertionError('Trade research report digest differs.')
  for row in report['report']['variants']:
   units=row['inputs']['input:units'];cycles=20 if name=='experiment' else 4 if row['phase']=='holdout' else 8 if name=='search' else 4
   _,stats=expected('partial',units,cycles);metrics=row['descriptive_metrics']['values'];net=Decimal(stats['performance']['netprofit'])
   for key,want in {'net_profit':net,'closed_net_profit':net,'open_net_profit':0,'ending_equity':Decimal(stats['equity_exact']),'commission':Decimal(stats['commission'])}.items():
    if Decimal(metrics[key])!=want:raise AssertionError((name,key,metrics[key],want))
  if name=='search' and (state['decision']['winner_ordinal']!=1 or Decimal(state['holdout']['assessment']['score'])!=68):raise AssertionError('Trade search selection differs.')
  if name=='walkforward' and (len(state['folds'])!=2 or any(Decimal(f['receipt']['holdout']['assessment']['score'])!=68 for f in state['folds'])):raise AssertionError('Trade walk-forward differs.')
  reports[name]=report['sha256']
 return {'performance_cli_http_versions':list(range(1,7)),'performance_families':list(FAMILIES),'performance_prepared_jobs':jobs,
  'performance_saved_ir':True,'performance_independent_trade_fee_oracle':True,'performance_pinned_inputs_and_resume':True,'performance_indexed_pagination':True,
  'performance_bounded_chart':True,'performance_history_cli':True,'performance_experiment':True,'performance_search_holdout':True,'performance_walkforward':True,'performance_report_hashes':reports}
