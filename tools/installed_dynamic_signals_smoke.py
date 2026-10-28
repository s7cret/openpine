"""Installed changing signals: all APIs/versions plus private workers and replay."""
import json,math,time,uuid
from tools.installed_primary_targets_smoke import verify as primary_verify
from tools.dynamic_signals_spec import oracle
from tools.specification import ROOT
def verify(http,cli,work):
 root=ROOT/'examples/dynamic-signals'
 result=primary_verify(http,cli,work,root=root,flow='dynamic_signals_flow',prefix='dynamic_signals')
 bars=ROOT/'conformance/dynamic-signals/bars.json';data=json.loads(bars.read_text());rows=data['bars']
 snapshot=http('/api/snapshots',{'provider':'upload','raw':bars.read_text()})['sha256']
 def check(outputs,v):
  expected=oracle([b['close'] for b in rows],[b['volume'] for b in rows],[i%4+1 for i in range(len(rows))],v)
  for i,(a,w) in enumerate(zip(outputs,expected,strict=True)):
   if i in (3,4):w=[1 if x else 0 for x in w]
   assert all(x is None if y is None else x is not None and math.isclose(x,y,abs_tol=1e-12,rel_tol=1e-12) for x,y in zip(a['values'],w,strict=True))
 for v in range(1,7):
  source=root/f'v{v}-adaptive.pine';artifact=work/f'all-signals-{v}.json';cli('compile','--source',source,'--profile','historical','--output',artifact)
  check(cli('job','--artifact',artifact,'--bars',bars,'--state-dir',work/f'all-signals-cli-{v}')['response']['result']['outputs'],v)
  compiled=http('/api/compile',{'source':source.read_text(),'source_name':source.name})
  launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,'gap_policy':'fail'})
  key=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];deadline=time.monotonic()+120
  while time.monotonic()<deadline:
   state=http('/api/jobs/'+key)
   if state['status']=='succeeded':break
   if state['status'] in ('failed','cancelled','timed_out'):raise AssertionError(state)
   time.sleep(.05)
  assert state['status']=='succeeded';check(http('/api/jobs/'+key+'/result')['result']['outputs'],v)
 return {**result,'dynamic_signals_full_api_versions':list(range(1,7)),'dynamic_signals_functions':['vwma','mom','roc','rising','falling','highestbars','lowestbars']}
