"""Reproducible economic programs and independent expectations for all versions."""
import argparse
from dataclasses import asdict
from packages.contracts.economics import EconomicsSettings
from tools.specification import ROOT,encode
from tools.trailing_fixtures import bars as trail_bars,FLAT,CYCLES as TRAIL
from tools.economics_oracle import KINDS,expected
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'ECON','type':'custom'}
RULES={'schema_version':'openpine.trading-rules.1','tick_size':'1','quantity_step':'1','minimum_quantity':'1','minimum_notional':'50'}
SETTINGS=asdict(EconomicsSettings('1','1',minimum_quantity='1',minimum_notional='50'))
CYCLES={'cash':[FLAT,(100,104,99,103),(108,112,107,110),FLAT,FLAT,FLAT,FLAT,FLAT],'equity':TRAIL['points'],'flat':TRAIL['points']}

def bars(family,cycles=1,short=False):
    data=trail_bars(CYCLES[family],8*cycles,short);data.update(context_id='economics',symbol='TEST:ECON');return data

def source(v,family,*,manual='na',repeat=False,short=False,inputs=False,body=None,header=''):
    mode,kind,value=KINDS[family];default=100 if family=='cash' else 20 if family=='equity' else 2
    result=f'//@version={v}\nstrategy("Economic orders",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.{mode},default_qty_value={default},initial_capital=1000,commission_type=strategy.commission.{kind},commission_value={value}{header})\n'
    if inputs:
        result+=('manual=input.float(0.0,title="Manual units",minval=0.0,maxval=2.0)\n' if v>=5 else 'manual=input(0.0,title="Manual units",minval=0.0,maxval=2.0)\n');manual='manual==0?na:manual'
    if body is None:
        direction=('false' if v==1 else 'strategy.short') if short else ('true' if v==1 else 'strategy.long')
        body=f'if bar_index%8==0\n' if repeat else 'if bar_index==0\n'
        body+=f'    strategy.entry("L",{direction},qty={manual})\n'
        if family=='cash':body+='    strategy.exit("X","L",profit=10,loss=20)\n'
        elif family=='equity':body+='    strategy.exit("X","L",trail_points=5,trail_offset=3)\n'
        else:body+='    strategy.exit("A","L",profit=5,qty=1)\n    strategy.exit("B","L",trail_points=5,trail_offset=3,qty_percent=100)\n'
    return result+body+'\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")\n'

def prepare():
    files={};cases=[]
    def put(n,v):files[n]=v.encode() if isinstance(v,str) else encode(v)
    def add(v,name,program,family,values,stats,settings=SETTINGS):
        filename=f'v{v}-{name}.pine';put('conformance/economics/'+filename,program)
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':family+'.json','broker_settings':settings,
         'expected':{'status':'executed','title':'Economic orders','shorttitle':'','overlay':False,'observation_count':len(values[0]),'qualifiers':['series']*3,
          'outputs':[{'title':t,'type':'float','values':list(s)} for t,s in zip(('Position','Equity','Entry price'),values,strict=True)],'simulation_statistics':stats,'pending_entries':[]}})
    def reject(v,name,program,code,category='PROFILE_UNCOVERED'):
        filename=f'v{v}-reject-{name}.pine';put('conformance/economics/'+filename,program)
        cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':filename,'bars':'cash.json','broker_settings':SETTINGS,'expected':{'status':'rejected','category':category,'code':code}})
    for f in KINDS:
        for short in (False,True):put('conformance/economics/'+f+('-short' if short else '')+'.json',bars(f,short=short))
        data=bars(f,60);put(f'examples/economics/{f}-bars.json',data)
        put(f'examples/economics/{f}-input.json',{'schema_version':'openpine.market-input.2','data':data,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':INSTRUMENT},'trading_rules':RULES})
    for v in range(1,7):
        for f in KINDS:
            put(f'examples/economics/{f}-v{v}.pine',source(v,f,inputs=True,repeat=True))
            for manual in (0,2):
                for short in (False,True):
                    values,stats=expected(f,manual,1,short);add(v,f'{f}-{manual}'+('-short' if short else '-long'),source(v,f,manual=str(manual) if manual else 'na',short=short),f+('-short' if short else ''),values,stats)
        for name,old,new in [('negative-budget','default_qty_value=100','default_qty_value=-1'),('negative-commission','commission_value=1','commission_value=-1')]:
            reject(v,name,source(v,'cash').replace(old,new),'STRATEGY_PROFILE')
        reject(v,'percentage',source(v,'equity').replace('default_qty_value=20','default_qty_value=101'),'STRATEGY_SETTINGS')
        reject(v,'fractional-explicit',source(v,'cash',manual='0.5'),'ORDER_QUANTITY','RUNTIME_ERROR')
        reject(v,'zero-explicit',source(v,'cash',manual='0'),'ORDER_QUANTITY','RUNTIME_ERROR')
        reject(v,'unknown-fee',source(v,'cash').replace('strategy.commission.percent','"volume"'),'STRATEGY_PROFILE')
        reject(v,'unknown-mode',source(v,'flat').replace('strategy.fixed','"shares"'),'STRATEGY_PROFILE')
        # One positive unit is too small for an explicitly pinned minimum of two.
        values,stats=expected('cash',0,1);stats={**stats}
        from tools.order_group_oracle import statistics
        empty=statistics();empty.update(exit_states=[],economics={**stats['economics'],'charged_count':0})
        settings={**SETTINGS,'minimum_quantity':'2'}
        add(v,'below-minimum',source(v,'cash'),'cash',([0.]*8,[1000.]*8,[None]*8),empty,settings)
        if v==6:reject(v,'when',source(v,'cash').replace('qty=na','qty=na,when=true'),'UNKNOWN_ARGUMENT','LANGUAGE_INVALID')
    put('conformance/economics/cases.json',{'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],
        'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Independent Decimal cycle recurrence for creation-time quantities, gap exposure and all fee modes; no broker-generated prices or expected statistics.'},'cases':cases})
    context={'context_id':'economics','symbol':'TEST:ECON','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}
    put('examples/economics/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':context,'contexts':[]})
    put('examples/economics/experiment.json',{'grid':{'manual':[0.,2.]},'inputs':{},'name':'Economic experiment','timeout_seconds':180})
    put('examples/economics/search.json',{'budget':2,'inputs':{},'method':'grid','name':'Economic search','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'manual':[0.,2.]},'timeout_seconds':180,
        'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':19200000},'holdout':{'warmup_start_ms':19200000,'score_start_ms':19200000,'end_ms':24000000}}})
    put('examples/economics/walkforward.json',{'budget':2,'inputs':{},'method':'grid','name':'Economic walkforward','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'manual':[0.,2.]},'timeout_seconds':180,
        'walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':0,'step_ms':4800000,'test_ms':4800000,'train_ms':9600000,'warmup_ms':0}})
    return files
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    for n,b in prepare().items():
        path=ROOT/n
        if a.check:
            if not path.is_file() or path.read_bytes()!=b:raise SystemExit('Economic fixture differs: '+n)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b)
    print('Independent economic corpus and eighteen examples verified.')
if __name__=='__main__':main()
