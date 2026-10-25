"""Manual expected trade histories, loss cooldown and holding-period strategies."""
import argparse
from dataclasses import asdict
from tools.performance_oracle import expected,FAMILIES,TITLES
from tools.specification import ROOT,encode
from packages.contracts.economics import EconomicsSettings
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'PERF','type':'custom'}
RULES={'schema_version':'openpine.trading-rules.1','tick_size':'1','quantity_step':'0.5','minimum_quantity':'0.5','minimum_notional':'25'}
SETTINGS=asdict(EconomicsSettings('1','0.5',minimum_quantity='0.5',minimum_notional='25'))
def bars(family='partial',cycles=3):
 prices=(100,100,90,110,105,100,100,100) if family=='cooldown' else (100,100,110,110,105,100,100,100)
 return {'schema_version':'openpine.historical-bars.2','context_id':'performance','symbol':'TEST:PERFORMANCE','timeframe':'1','bars':[{'time':(cycle*8+i)*60000,'open':float(p),'close':float(p),'high':float(p+1),'low':float(p-1),'volume':1.} for cycle in range(cycles) for i,p in enumerate(prices)]}
def source(v=6,family='partial',units=2,*,inputs=False):
 long='true' if v==1 else 'strategy.long'
 code=f'//@version={v}\nstrategy("Trade observations",default_qty_type=strategy.fixed,default_qty_value=2,initial_capital=1000,commission_type=strategy.commission.cash_per_order,commission_value=1)\n'
 code+=('units='+('input.int' if v>=5 else 'input')+'(2,title="Units",minval=1,maxval=3)\n') if inputs else f'units={units}\n'
 if family=='cooldown':
  code+='var int stoppedUntil=0\nif strategy.closedtrades>nz(strategy.closedtrades[1]) and strategy.netprofit<strategy.netprofit[1]\n    stoppedUntil:=bar_index+8\n'
  code+=f'if bar_index%8==0 and bar_index>=stoppedUntil\n    strategy.entry("L",{long},qty=units)\nif bar_index%8==1\n    strategy.close("L")\n'
 else:
  code+=f'if bar_index%8==0\n    strategy.entry("L",{long},qty=units)\n'
  if family=='partial':code+='if bar_index%8==1\n    strategy.close("L",qty=units/2.0)\nif bar_index%8==2\n    strategy.close("L")\n'
  elif v>=5:code+='if strategy.opentrades>0 and bar_index-strategy.opentrades.entry_bar_index(0)>=2\n    strategy.close("L")\n'
  else:code+='var int entryBar=na\nif strategy.position_size!=0 and nz(strategy.position_size[1])==0\n    entryBar:=bar_index\nif strategy.position_size!=0 and bar_index-entryBar>=2\n    strategy.close("L")\n'
 for name,slot in zip(TITLES,('equity','netprofit','openprofit','closedtrades','opentrades','grossprofit','grossloss','wintrades','losstrades'),strict=True):code+=f'plot(strategy.{slot},title="{name}")\n'
 if v>=5:code+='plot(strategy.closedtrades.profit(strategy.closedtrades-1),title="Last trade")\nplot(strategy.closedtrades.exit_bar_index(strategy.closedtrades-1),title="Last exit")\n'
 return code
def prepare():
 files={};cases=[]
 def put(n,v):files[n]=v.encode() if isinstance(v,str) else encode(v)
 for f in FAMILIES:
  put(f'conformance/performance/{f}.json',bars(f,3));data=bars(f,20);put(f'examples/performance/{f}-bars.json',data)
  put(f'examples/performance/{f}-input.json',{'schema_version':'openpine.market-input.2','data':data,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':INSTRUMENT},'trading_rules':RULES})
 for v in range(1,7):
  for f in FAMILIES:
   put(f'examples/performance/{f}-v{v}.pine',source(v,f,inputs=True))
   for units in (1,2):
    name=f'v{v}-{f}-{units}';put(f'conformance/performance/{name}.pine',source(v,f,units));values,stats=expected(f,units,3,v)
    cases.append({'case_id':name,'language_version':v,'source':name+'.pine','bars':f+'.json','broker_settings':SETTINGS,'numeric_tolerance':{'absolute':1e-12,'relative':1e-12},'expected':{'status':'executed','title':'Trade observations','shorttitle':'','overlay':False,'observation_count':24,'qualifiers':['series']*len(values),'outputs':[{'title':n,'type':'int' if n in ('Closed','Active','Win count','Loss count','Last exit') else 'float','values':z} for n,z in values.items()],'simulation_statistics':stats,'pending_entries':[]}})
  negatives=[('indicator',f'//@version={v}\n'+('indicator' if v>=5 else 'study')+'("Invalid")\nplot(strategy.netprofit)\n','STRATEGY_PROFILE','PROFILE_UNCOVERED')]
  if v<5:negatives.append(('reader-version',source(v)+'plot(strategy.closedtrades.profit(0))\n','SIGNATURE_UNAVAILABLE','LANGUAGE_INVALID'))
  else:
   negatives.extend([('index-type',source(v)+'plot(strategy.closedtrades.profit(0.5))\n','ARGUMENT_TYPE','LANGUAGE_INVALID'),('index-string',source(v)+'plot(strategy.opentrades.size("0"))\n','ARGUMENT_TYPE','LANGUAGE_INVALID')])
  for kind,code,error,category in negatives:
   n=f'v{v}-reject-{kind}';put(f'conformance/performance/{n}.pine',code);cases.append({'case_id':n,'language_version':v,'source':n+'.pine','bars':'partial.json','broker_settings':SETTINGS,'expected':{'status':'rejected','code':error,'category':category}})
 put('conformance/performance/cases.json',{'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Independent cycle arithmetic for partial fee allocations, loss cooldown and holding time; closed indices are checked outside generated expectations.'},'cases':cases})
 put('examples/performance/experiment.json',{'grid':{'units':[1,2]},'inputs':{},'name':'Trade history experiment','timeout_seconds':180})
 put('examples/performance/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':{'context_id':'performance','symbol':'TEST:PERFORMANCE','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'},'contexts':[]})
 put('examples/performance/search.json',{'budget':2,'inputs':{},'method':'grid','name':'Trade history search','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'units':[1,2]},'timeout_seconds':180,'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':64*60000},'holdout':{'warmup_start_ms':64*60000,'score_start_ms':64*60000,'end_ms':96*60000}}})
 put('examples/performance/walkforward.json',{'budget':2,'inputs':{},'method':'grid','name':'Trade history walkforward','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'units':[1,2]},'timeout_seconds':180,'walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':0,'step_ms':32*60000,'test_ms':32*60000,'train_ms':32*60000,'warmup_ms':0}})
 return files
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
 for n,b in prepare().items():
  path=ROOT/n
  if a.check:
   if not path.is_file() or path.read_bytes()!=b:raise SystemExit('Performance fixture differs: '+n)
  else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b)
 print('Independent performance corpus and eighteen examples verified.')
if __name__=='__main__':main()
