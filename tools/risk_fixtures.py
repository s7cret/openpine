"""Risk rules, daily resets and entry scope across all six language versions."""
import argparse
from dataclasses import asdict
from packages.contracts.economics import EconomicsSettings
from tools.risk_oracle import expected,FAMILIES,DAY
from tools.specification import ROOT,encode
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'RISK','type':'custom'}
RULES={'schema_version':'openpine.trading-rules.1','tick_size':'1','quantity_step':'1','minimum_quantity':'1','minimum_notional':'50'}
SETTINGS=asdict(EconomicsSettings('1','1',minimum_quantity='1',minimum_notional='50'))
RULE_DECLARATIONS={'entry':'strategy.risk.allow_entry_in(strategy.direction.long)\nstrategy.risk.max_position_size(3)\n',
 'daily':'strategy.risk.max_intraday_loss(20,strategy.cash)\nstrategy.risk.max_intraday_filled_orders(5)\n',
 'lifetime':'strategy.risk.max_drawdown(50,strategy.cash)\nstrategy.risk.max_cons_loss_days(2)\n'}
def bars(family,cycles=3):
 rows=[(100,101,99,100),(100,101,99,100),(110,111,109,110),(100,101,99,100),(90,91,89,90),(95,96,94,95),(100,101,99,100),(100,101,99,100)] if family=='entry' else [(100,101,99,100),(100,101,89,90),(88,89,87,88),*[(100,101,99,100)]*5]
 return {'schema_version':'openpine.historical-bars.2','context_id':'risk','symbol':'TEST:RISK','timeframe':'180','bars':[{'time':(day*8+i)*10800000,'open':o,'high':h,'low':l,'close':c,'volume':1.} for day in range(cycles) for i,(o,h,l,c) in enumerate(rows)]}
def source(v=6,family='daily',*,manual='na',inputs=False,rules=None,body=None,header=''):
 mode,value={'entry':('percent_of_equity',50),'daily':('fixed',2),'lifetime':('cash',300)}[family]
 result=f'//@version={v}\nstrategy("Risk rules",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.{mode},default_qty_value={value},initial_capital=1000,commission_type=strategy.commission.cash_per_order,commission_value=1{header})\n'
 result+=RULE_DECLARATIONS[family] if rules is None else rules
 if inputs:result+=('manual=input.float(0.0,title="Manual units",minval=0.0,maxval=1.0)\n' if v>=5 else 'manual=input(0.0,title="Manual units",minval=0.0,maxval=1.0)\n');manual='manual==0?na:manual'
 long='true' if v==1 else 'strategy.long';short='false' if v==1 else 'strategy.short'
 if body is None:
  body=f'if bar_index%8==0\n    strategy.entry("L",{long},qty={manual})\n'
  if family=='entry':body+=f'if bar_index%8==1\n    strategy.entry("S",{short},qty={manual})\nif bar_index%8==2\n    strategy.order("N",{short},qty=4)\nif bar_index%8==3\n    strategy.entry("R",{long},qty={manual})\nif bar_index%8==4\n    strategy.close_all()\n'
  else:
   body+='if bar_index%8==1\n    strategy.close("L")\n'
   if family=='daily':body+=f'if bar_index%8==3 or bar_index%8==5\n    strategy.entry("L",{long},qty={manual})\nif bar_index%8==4 or bar_index%8==6\n    strategy.close("L")\n'
 return result+body+'\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")\n'
def prepare():
 files={};cases=[]
 def put(n,v):files[n]=v.encode() if isinstance(v,str) else encode(v)
 for f in FAMILIES:
  cycles=3 if f=='lifetime' else 1;put(f'conformance/risk/{f}.json',bars(f,cycles))
  data=bars(f,20);put(f'examples/risk/{f}-bars.json',data)
  put(f'examples/risk/{f}-input.json',{'schema_version':'openpine.market-input.2','data':data,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':INSTRUMENT},'trading_rules':RULES})
 for v in range(1,7):
  for f in FAMILIES:
   put(f'examples/risk/{f}-v{v}.pine',source(v,f,inputs=True))
   for manual in (0,1):
    name=f'v{v}-{f}-{manual}';put(f'conformance/risk/{name}.pine',source(v,f,manual='1' if manual else 'na'));values,stats=expected(f,manual,3 if f=='lifetime' else 1)
    cases.append({'case_id':name,'language_version':v,'source':name+'.pine','bars':f+'.json','broker_settings':SETTINGS,'expected':{'status':'executed','title':'Risk rules','shorttitle':'','overlay':False,'observation_count':len(values[0]),'qualifiers':['series']*3,'outputs':[{'title':n,'type':'float','values':list(z)} for n,z in zip(('Position','Equity','Entry price'),values,strict=True)],'simulation_statistics':stats,'pending_entries':[]}})
  negatives=[('percentage','strategy.risk.max_drawdown(101,strategy.percent_of_equity)\n','RISK_SETTINGS','PROFILE_UNCOVERED'),
   ('unit','strategy.risk.max_intraday_loss(1,"USD")\n','RISK_SETTINGS','PROFILE_UNCOVERED'),('direction','strategy.risk.allow_entry_in("both")\n','RISK_SETTINGS','PROFILE_UNCOVERED'),
   ('zero-cap','strategy.risk.max_position_size(0)\n','RISK_SETTINGS','PROFILE_UNCOVERED'),('fractional-count','strategy.risk.max_intraday_filled_orders(1.5)\n','ARGUMENT_TYPE','LANGUAGE_INVALID'),
   ('duplicate','strategy.risk.max_cons_loss_days(2)\nstrategy.risk.max_cons_loss_days(3)\n','RISK_DUPLICATE','PROFILE_UNCOVERED'),
   ('conditional','if true\n    strategy.risk.max_drawdown(5,strategy.cash)\n','GLOBAL_ONLY_CALL','LANGUAGE_INVALID'),
   ('function','unused() =>\n    strategy.risk.max_position_size(2)\n    1\n','GLOBAL_ONLY_CALL','LANGUAGE_INVALID'),
   ('input','limit='+('input.int(2)' if v>=5 else 'input(2)')+'\nstrategy.risk.max_cons_loss_days(limit)\n','ARGUMENT_QUALIFIER','LANGUAGE_INVALID'),
   ('message','strategy.risk.max_drawdown(5,strategy.cash,alert_message="'+'x'*1025+'")\n','RISK_SETTINGS','PROFILE_UNCOVERED')]
  for name,rules,code,category in negatives:
   n=f'v{v}-reject-{name}';put('conformance/risk/'+n+'.pine',source(v,rules=rules));cases.append({'case_id':n,'language_version':v,'source':n+'.pine','bars':'daily.json','broker_settings':SETTINGS,'expected':{'status':'rejected','category':category,'code':code}})
 put('conformance/risk/cases.json',{'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Independent phase arithmetic for entry caps/direction, daily cash loss/order counts, lifetime drawdown and losing observed UTC days; no broker-generated values.'},'cases':cases})
 context={'context_id':'risk','symbol':'TEST:RISK','timeframe':'180','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}
 put('examples/risk/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':context,'contexts':[]})
 put('examples/risk/experiment.json',{'grid':{'manual':[0.,1.]},'inputs':{},'name':'Risk experiment','timeout_seconds':180})
 put('examples/risk/search.json',{'budget':2,'inputs':{},'method':'grid','name':'Risk search','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'manual':[0.,1.]},'timeout_seconds':180,'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':8*DAY},'holdout':{'warmup_start_ms':8*DAY,'score_start_ms':8*DAY,'end_ms':12*DAY}}})
 put('examples/risk/walkforward.json',{'budget':2,'inputs':{},'method':'grid','name':'Risk walkforward','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'manual':[0.,1.]},'timeout_seconds':180,'walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':0,'step_ms':4*DAY,'test_ms':4*DAY,'train_ms':4*DAY,'warmup_ms':0}})
 return files
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
 for n,b in prepare().items():
  path=ROOT/n
  if a.check:
   if not path.is_file() or path.read_bytes()!=b:raise SystemExit('Risk fixture differs: '+n)
  else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b)
 print('Independent risk corpus and eighteen examples verified.')
if __name__=='__main__':main()
