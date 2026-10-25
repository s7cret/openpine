"""Installed risk modes through frozen launches, saved IR and research."""
import json,sys,time,uuid
from decimal import Decimal
from tools.specification import ROOT
from tools.risk_oracle import expected,FAMILIES
from tools.risk_fixtures import SETTINGS
from packages.contracts.wire import digest
def verify(http,cli,work):
 root=ROOT/'examples/risk';snapshots={f:http('/api/snapshots',{'provider':'upload','raw':(root/f'{f}-input.json').read_text()})['sha256'] for f in FAMILIES};jobs=[];base=None;covered=set()
 def finish(path,target='succeeded'):
  deadline=time.monotonic()+240
  while time.monotonic()<deadline:
   v=http(path)
   if v['status']==target:return v
   if v['status'] in ('failed','cancelled','timed_out','succeeded','paused','completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(v)
   time.sleep(.1)
  raise TimeoutError(v)
 for version in range(1,7):
  for f in FAMILIES:
   print(f'Installed risks: {f} v{version}',file=sys.stderr,flush=True)
   path=root/f'{f}-v{version}.pine';compiled=http('/api/compile',{'source':path.read_text(),'source_name':path.as_posix()});cut={'entry':4,'daily':2,'lifetime':10}[f]
   launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
    'primary':{'kind':'snapshot','snapshot_id':snapshots[f]},'contexts':[],'inputs':{'manual':0.},'timeout_seconds':180,'gap_policy':'fail','checkpoint_interval_bars':8,'pause_after_bars':cut})
   if launch['status']!='ready':raise AssertionError(launch)
   key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];jobs.append(key);finish('/api/jobs/'+key,'paused');http('/api/jobs/'+key+'/resume',{});state=finish('/api/jobs/'+key)
   result=http('/api/jobs/'+key+'/result')['result'];values,stats=expected(f)
   if state['resumed_from_bar']!=cut or [o['values'] for o in result['outputs']]!=[list(s) for s in values]:raise AssertionError('Risk outputs differ from independent recurrence.')
   if result['simulation']['schema_version']!='openpine.simulation.9':raise AssertionError('Risk simulation profile differs.')
   for field in ('equity_exact','cash_exact','gross_realized_exact','entry_commission','exit_commission','allocated_entry_commission','commission','orders','fills','trades','closed_lots','closed_positions','economics'):
    if result['simulation']['statistics'][field]!=stats[field]:raise AssertionError((f,field))
   got=dict(result['simulation']['statistics']['risk']);halt=got['halt']
   if halt is not None:got['halt']={k:v for k,v in halt.items() if k!='call_site_id'}
   if got!=stats['risk']:raise AssertionError((f,'risk state',got,stats['risk']))
   configuration=http(f'/api/jobs/{key}/configuration')['plan']
   if configuration['simulation_policy']['settings']!=SETTINGS or configuration['settings']['values']['input:manual']!=0.:raise AssertionError('Risk settings and rules are not pinned.')
   policy=configuration['simulation_policy']
   if policy['profile_id']!='openpine.risk-broker.1' or policy['timeframe']!='180' or policy['language_version']!=version:raise AssertionError('Risk policy calendar/version differs.')
   declared={k for k,r in policy['limits'].items() if r is not None};covered.update(declared)
   if declared!={'entry':{'allow_entry_in','max_position_size'},'daily':{'max_intraday_loss','max_intraday_filled_orders'},'lifetime':{'max_drawdown','max_cons_loss_days'}}[f]:raise AssertionError('Risk rule inventory differs.')
   artifact=work/f'risk-ir-{f}-{version}.json';override=work/f'risk-inputs-{f}-{version}.json';override.write_text('{"manual":0.0}')
   cli('compile','--source',path,'--profile','historical','--output',artifact)
   direct=cli('job','--artifact',artifact,'--bars',root/f'{f}-input.json','--contexts',root/'contexts.json','--inputs',override,'--state-dir',work/f'risk-cli-{f}-{version}')
   if direct['response']['result']!=result:raise AssertionError('Saved risk CLI and prepared HTTP differ.')
   chart=http(f'/api/jobs/{key}/chart?start=0&limit=24');events=chart['protection']['events']
   if chart['schema_version']!='openpine.chart-window.9' or chart['protection']['event_bytes']>chart['protection']['event_byte_budget']:raise AssertionError('Risk chart profile/budget differs.')
   if not all(any(e['kind']==kind for e in events) for kind in ('quantity_resolved','commission_calculated','risk_period',*(['risk_adjusted'] if f=='entry' else ['risk_triggered','risk_liquidation']))):raise AssertionError('Risk explanation events are absent.')
   if version==6 and f=='daily':base=launch['base']
 reports={}
 for route,name,field in (('experiments','experiment','experiment_id'),('searches','search','search_id'),('walkforwards','walkforward','walkforward_id')):
  print('Installed risks: '+route,file=sys.stderr,flush=True)
  made=http('/api/'+route,{**json.loads((root/(name+'.json')).read_text()),**base});path='/api/'+route+'/'+made[field];state=finish(path);report=http(path+'/report')
  if report['sha256']!=digest(report['report']):raise AssertionError('Risk report digest differs.')
  for row in report['report']['variants']:
   manual=int(row['inputs']['input:manual']);cycles=20 if name=='experiment' else 4 if row['phase']=='holdout' else 8 if name=='search' else 4
   _,stats=expected('daily',manual,cycles);metrics=row['descriptive_metrics']['values']
   for key,want in {'net_profit':Decimal(stats['equity_exact'])-1000,'ending_equity':Decimal(stats['equity_exact']),'commission':Decimal(stats['commission'])}.items():
    if Decimal(metrics[key])!=want:raise AssertionError((name,key,metrics[key],want))
  if name=='search' and (state['decision']['winner_ordinal']!=1 or Decimal(state['holdout']['assessment']['score'])!=-72):raise AssertionError('Risk selection/holdout differs.')
  if name=='walkforward' and (len(state['folds'])!=2 or any(Decimal(fold['receipt']['holdout']['assessment']['score'])!=-72 for fold in state['folds'])):raise AssertionError('Risk folds differ.')
  reports[name]=report['sha256']
 return {'risk_cli_http_versions':list(range(1,7)),'risk_families':list(FAMILIES),'risk_declarations':sorted(covered),'risk_prepared_jobs':jobs,
  'risk_saved_ir':True,'risk_independent_rule_and_fee_oracle':True,'risk_pinned_rules_and_resume':True,
  'risk_bounded_chart_explanations':True,'risk_experiment':True,'risk_search_holdout':True,'risk_walkforward':True,'risk_report_hashes':reports}
