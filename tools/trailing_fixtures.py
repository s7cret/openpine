"""Versioned relative/points/price programs with hand-derived observations."""
import argparse
from decimal import Decimal
from dataclasses import asdict
from packages.contracts.trading import BrokerSettings
from tools.specification import ROOT,encode
from tools.trailing_oracle import expected,cycle_totals,statistics

INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'TRAIL','type':'custom'}
TICK=asdict(BrokerSettings(tick_size='1'))
FLAT=(100,101,99,100)
CYCLES={
 'relative':[FLAT,(100,103,99,102),(103,106,102,105),FLAT,FLAT,FLAT,FLAT,FLAT],
 'points':[FLAT,(100,104,99,103),(103,110,102,108),(108,112,108,111),(110,111,108,109),FLAT,FLAT,FLAT],
 'price':[FLAT,(100,104,99,103),(110,112,106,108),FLAT,FLAT,FLAT,FLAT,FLAT]}

def bars(rows,count=None,short=False):
    out=[]
    for i in range(count or len(rows)):
        o,h,l,c=rows[i%len(rows)]
        if short:o,h,l,c=200-o,200-l,200-h,200-c
        out.append({'time':i*60000,'open':float(o),'high':float(h),'low':float(l),'close':float(c),'volume':1.})
    return {'schema_version':'openpine.historical-bars.2','context_id':'trailing','symbol':'TEST:TRAIL','timeframe':'1','bars':out}

def source(v,body,inputs=False):
    head=f'//@version={v}\nstrategy("Trailing exits",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.fixed,default_qty_value=2,initial_capital=1000,commission_value=1)\n'
    if inputs:head+=('distance=input.int(3,title="Trailing distance",minval=2,maxval=3)\n' if v>=5 else 'distance=input(3,title="Trailing distance",minval=2,maxval=3)\n')
    return head+body+'\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")\n'

def body(v,family,distance='distance',repeat=True,short=False,reissue=False):
    direction=('false' if v==1 else 'strategy.short') if short else ('true' if v==1 else 'strategy.long')
    params=f'profit={distance}+2,loss={distance}+4' if family=='relative' else f'trail_points=5,trail_offset={distance}' if family=='points' else f'trail_price={95 if short else 105},trail_offset={distance}'
    phase='bar_index%8==0' if repeat else 'bar_index==0'
    entry=f'if {phase}\n    strategy.entry("L",{direction})'
    return entry+('\nstrategy.exit("X","L",'+params+')' if reissue else '\n    strategy.exit("X","L",'+params+')')

def prepare():
    files={};cases=[]
    def put(path,value):files[path]=value.encode() if isinstance(value,str) else encode(value)
    def fixture(name,rows,short=False):
        put('conformance/trailing/'+name+'.json',bars(rows,short=short));return name+'.json'
    for name,rows in CYCLES.items():
        fixture(name,rows);fixture(name+'-short',rows,True)
        data=bars(rows,480);put(f'examples/trailing/{name}-bars.json',data)
        put(f'examples/trailing/{name}-input.json',{'schema_version':'openpine.market-input.1','data':data,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':INSTRUMENT}})
    flat=fixture('flat',[FLAT]*4);one=fixture('one',[FLAT])
    def add(v,name,program,data,values,stats,pending=None,settings=TICK):
        filename=f'v{v}-{name}.pine';put('conformance/trailing/'+filename,source(v,program))
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':data,'broker_settings':settings,
            'expected':{'status':'executed','title':'Trailing exits','shorttitle':'','overlay':False,'observation_count':len(values[0]),'qualifiers':['series']*3,
                'outputs':[{'title':t,'type':'float','values':list(row)} for t,row in zip(('Position','Equity','Entry price'),values,strict=True)],'simulation_statistics':stats,'pending_entries':pending or []}})
    def reject(v,name,program,code,category='PROFILE_UNCOVERED',settings=TICK):
        filename=f'v{v}-reject-{name}.pine';put('conformance/trailing/'+filename,source(v,program))
        cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':filename,'bars':one,'broker_settings':settings,'expected':{'status':'rejected','category':category,'code':code}})
    for v in range(1,7):
        direction='true' if v==1 else 'strategy.long'
        for family in CYCLES:
            put(f'examples/trailing/{family}-v{v}.pine',source(v,body(v,family),True))
            for d in (2,3):
                for short in (False,True):
                    total=cycle_totals(family,d,short);total.pop('exit_price')
                    add(v,f'{family}-{d}'+('-short' if short else '-long'),body(v,family,str(d),False,short),family+('-short' if short else '')+'.json',expected(family,d,1,short),statistics(**total))
        # Repeated identical exits must retain activation/extrema, rather than
        # restarting a trailing stop at each source close.
        for short in (False,True):
            total=cycle_totals('points',3,short);total.pop('exit_price');total['orders']=5
            # One entry plus one reached exit on every bar. The remaining
            # unmatched calls after closure are ignored and create no order.
            add(v,'reissue'+('-short' if short else '-long'),body(v,'points','3',False,short,True),'points'+('-short' if short else '')+'.json',expected('points',3,1,short),statistics(**total))
        # v1-v5 take absolute 120; v6 takes relative 105 first.
        pair=fixture('pairs', [FLAT,(100,106,99,103),(110,121,109,115),FLAT])
        program=f'if bar_index==0\n    strategy.order("L",{direction},qty=2)\n    strategy.exit("X","L",limit=120,profit=5,stop=90,loss=3)'
        price=105 if v==6 else 120;fee=Decimal(price)*Decimal('.02');net=2*(price-100)-2-fee
        values=([0.,0.,0.,0.],[1000.,float(1000+net),float(1000+net),float(1000+net)],[None]*4) if v==6 else ([0.,2.,0.,0.],[1000.,1004.,float(1000+net),float(1000+net)],[None,100.,None,None])
        add(v,'absolute-relative-pairs',program,pair,values,statistics(2*(price-100),2,fee,orders=2,fills=2,trades=1,closed_lots=1,closed_positions=1))
        for args in ('profit=0,loss=0','trail_points=0,trail_offset=0'):
            name='zero-fixed' if args.startswith('profit') else 'zero-trailing'
            add(v,name,f'if bar_index==0\n    strategy.entry("L",{direction},qty=2)\n    strategy.exit("X","L",{args})',flat,([0.]*4,[1000.,996.,996.,996.],[None]*4),statistics(0,2,2,orders=2,fills=2,trades=1,closed_lots=1,closed_positions=1))
        prefix=f'strategy.exit("X","L",'
        reject(v,'missing-offset',prefix+'trail_points=5)','EXIT_TRAILING_REQUIRED')
        reject(v,'missing-activation',prefix+'trail_offset=2)','EXIT_TRAILING_REQUIRED')
        reject(v,'negative',prefix+'profit=-1)','EXIT_LEVEL','RUNTIME_ERROR')
        reject(v,'fractional-ticks',prefix+'loss=0.5)','EXIT_LEVEL','RUNTIME_ERROR')
        reject(v,'negative-offset',prefix+'trail_points=5,trail_offset=-1)','EXIT_LEVEL','RUNTIME_ERROR')
        reject(v,'negative-price',prefix+'trail_price=-1,trail_offset=2)','EXIT_LEVEL','RUNTIME_ERROR')
        reject(v,'missing-tick',prefix+'profit=5)','EXIT_TICK_METADATA','INPUT_UNAVAILABLE',settings=asdict(BrokerSettings()))
        reject(v,'unverified-alert',prefix+'profit=5,alert_profit="x")','UNKNOWN_ARGUMENT','LANGUAGE_INVALID')
        reject(v,'unverified-immediate','strategy.close_all(immediately=true)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID')
        if v==6:reject(v,'when',prefix+'profit=5,when=true)','UNKNOWN_ARGUMENT','LANGUAGE_INVALID')
        else:add(v,'when-false',prefix+'profit=5,when=false)',flat,([0.]*4,[1000.]*4,[None]*4),statistics())
    put('conformance/trailing/cases.json',{'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],
        'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Closed-form long/short phase prices, quantities, fees and version-pair expectations; no broker-generated expected values.'},'cases':cases})
    context={'context_id':'trailing','symbol':'TEST:TRAIL','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}
    put('examples/trailing/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':context,'contexts':[]})
    put('examples/trailing/experiment.json',{'grid':{'distance':[2,3]},'inputs':{},'name':'Trailing experiment','timeout_seconds':120})
    put('examples/trailing/search.json',{'budget':2,'inputs':{},'method':'grid','name':'Trailing search','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'distance':[2,3]},'timeout_seconds':120,
        'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':19200000},'holdout':{'warmup_start_ms':19200000,'score_start_ms':19200000,'end_ms':24000000}}})
    put('examples/trailing/walkforward.json',{'budget':2,'inputs':{},'method':'grid','name':'Trailing walkforward','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'distance':[2,3]},'timeout_seconds':120,
        'walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':0,'step_ms':4800000,'test_ms':4800000,'train_ms':9600000,'warmup_ms':0}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        target=ROOT/name
        if args.check:
            if not target.is_file() or target.read_bytes()!=raw:raise ValueError('Trailing fixture differs: '+name)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print('Independent trailing corpus and eighteen examples verified.');return 0
if __name__=='__main__':raise SystemExit(main())
