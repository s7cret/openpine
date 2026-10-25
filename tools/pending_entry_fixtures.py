"""Independent absolute-price entry expectations and eighteen executable examples."""
from decimal import Decimal
import argparse
from tools.specification import ROOT,encode

CYCLES={
 'limit':[(100,101,99,100),(100,101,94,100),(100,111,99,110),(100,101,89,90),(100,101,99,100)],
 'stop':[(100,101,99,100),(100,106,99,106),(110,116,109,115),(100,101,94,95),(100,101,99,100)],
 'stop-limit':[(100,101,99,100),(100,106,100,104),(104,106,102,104),(104,111,103,110),(100,101,94,95)]}

def bars(rows,short=False,count=None):
    data=[]
    for i in range(count or len(rows)):
        o,h,l,c=rows[i%len(rows)]
        if short:o,h,l,c=200-o,200-l,200-h,200-c
        data.append({'time':i*60000,'open':float(o),'high':float(h),'low':float(l),'close':float(c),'volume':1.})
    return {'schema_version':'openpine.historical-bars.2','context_id':'entry','symbol':'TEST:ENTRY','timeframe':'1','bars':data}

def source(version,body,fee=0,*,plots=True,default_qty=10,title='Pending entries'):
    text=f'//@version={version}\nstrategy("{title}",default_qty_type=strategy.fixed,default_qty_value={default_qty},initial_capital=1000,commission_type=strategy.commission.percent,commission_value={fee})\n{body}\n'
    return text+('plot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\n' if plots else '')

def stats(gross=0,entry_fee=0,exit_fee=0,*,orders=3,fills=3,trades=2,closed=1,entries=None):
    text=lambda v:format(Decimal(v).normalize(),'f');fees=Decimal(entry_fee)+Decimal(exit_fee);cash=1000+Decimal(gross)-fees
    return {'cash':text(cash),'equity':text(cash),'position_size':'0','position_avg_price':None,'gross_realized':text(gross),'unrealized':'0',
        'commission':text(fees),'orders':orders,'fills':fills,'trades':trades,'cash_exact':text(cash),'equity_exact':text(cash),'gross_realized_exact':text(gross),
        'unrealized_exact':'0','allocated_entry_commission':text(entry_fee),'entry_commission':text(entry_fee),'exit_commission':text(exit_fee),
        'closed_positions':closed,'protections':[],'entries':entries or []}

def family_body(version,family,short=False,quantity='5',repeat=False):
    direction=('false' if short else 'true') if version==1 else ('strategy.short' if short else 'strategy.long')
    e,t,s,b={'limit':(95,110,90,120),'stop':(105,115,95,125),'stop-limit':(103,110,95,120)}[family]
    price=lambda v:200-v if short else v
    parameters=f'limit={price(e)}' if family=='limit' else f'stop={price(e)}' if family=='stop' else f'limit={price(e)},stop={price(105)}'
    prefix='if bar_index%5==0' if repeat else 'if bar_index==0'
    return prefix+f'\n    strategy.entry("L",{direction},{parameters})\n    strategy.exit("A","L",stop={price(s)},limit={price(t)},qty={quantity})\n    strategy.exit("B","L",stop={price(s)},limit={price(b)},qty_percent=100)'

def prepare():
    files={};cases=[]
    def fixture(name,rows,short=False):
        path=f'conformance/pending-entries/{name}.json';files[path]=encode(bars(rows,short));return name+'.json'
    for family,rows in CYCLES.items():
        fixture(family,rows);fixture(family+'-short',rows,True)
        files[f'examples/pending-entries/{family}-bars.json']=encode(bars(rows,count=400))
    gap=fixture('limit-gap',[(100,101,99,100),(100,111,99,110),(100,101,99,100),(100,101,99,100)])
    stop_gap=fixture('stop-gap',[(100,101,99,100),(110,111,109,110),(125,126,124,125),(125,126,124,125)])
    stop_open=fixture('stop-limit-open',[(100,101,99,100),(110,116,109,115),(100,101,99,100),(100,101,99,100)])
    modify=fixture('modify',[(100,101,99,100),(100,101,99,100),(100,111,94,100),(100,111,99,110)])
    flat=fixture('flat',[(100,101,99,100)]*4)
    market=fixture('market',[(100,101,99,100),(100,106,99,105),(110,111,109,110),(90,91,89,90)])
    pending=fixture('pending',[(100,101,99,100),(100,106,99,104),(110,111,109,110),(110,111,109,110)])
    def add(v,name,body,data,positions,equity,statistics,fee=0,pending_names=None):
        filename=f'v{v}-{name}.pine';files['conformance/pending-entries/'+filename]=source(v,body,fee).encode()
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':data,
            'expected':{'status':'executed','title':'Pending entries','shorttitle':'','overlay':False,'observation_count':len(positions),'qualifiers':['series','series'],
                'outputs':[{'title':'Position','type':'float','values':positions},{'title':'Equity','type':'float','values':equity}],
                'simulation_statistics':statistics,'pending_entries':pending_names or []}})
    main={
      ('limit',False):([0.,10.,5.,0.,0.],[1000.,1040.5,1135.,1030.5,1030.5],stats(50,'9.5',10)),
      ('limit',True):([0.,-10.,-5.,0.,0.],[1000.,1039.5,1135.,1029.5,1029.5],stats(50,'10.5',10)),
      ('stop',False):([0.,10.,5.,0.,0.],[1000.,999.5,1083.75,979.,979.],stats(0,'10.5','10.5')),
      ('stop',True):([0.,-10.,-5.,0.,0.],[1000.,1000.5,1086.25,981.,981.],stats(0,'9.5','9.5')),
      ('stop-limit',False):([0.,0.,10.,5.,0.],[1000.,1000.,999.7,1054.2,974.45],stats(-5,'10.3','10.25')),
      ('stop-limit',True):([0.,0.,-10.,-5.,0.],[1000.,1000.,1000.3,1055.8,975.55],stats(-5,'9.7','9.75'))}
    for v in range(1,7):
        long='true' if v==1 else 'strategy.long'
        for (family,short),(positions,equity,statistics) in main.items():
            add(v,family+('-short' if short else '-long'),family_body(v,family,short),family+('-short' if short else '')+'.json',positions,equity,statistics,1)
        add(v,'limit-gap',f'if bar_index==0\n    strategy.entry("L",{long},limit=105)\n    strategy.exit("A","L",stop=95,limit=110,qty=10)',gap,[0.,0.,0.,0.],[1000.,1100.,1100.,1100.],stats(100,orders=2,fills=2,trades=1))
        add(v,'stop-gap',f'if bar_index==0\n    strategy.entry("L",{long},stop=105)\n    strategy.exit("A","L",stop=100,limit=120,qty=10)',stop_gap,[0.,10.,0.,0.],[1000.,1000.,1150.,1150.],stats(150,orders=2,fills=2,trades=1))
        add(v,'stop-limit-open',f'if bar_index==0\n    strategy.entry("L",{long},stop=105,limit=112)\n    strategy.exit("A","L",stop=100,limit=115,qty=10)',stop_open,[0.,0.,0.,0.],[1000.,1050.,1050.,1050.],stats(50,orders=2,fills=2,trades=1))
        change=f'if bar_index==0\n    strategy.entry("L",{long},limit=90)\n    strategy.exit("A","L",stop=80,limit=110,qty=10)\nif bar_index==1\n    strategy.entry("L",{long},limit=95)\n    strategy.exit("A","L",stop=80,limit=110,qty=10)'
        add(v,'modify',change,modify,[0.,0.,10.,0.],[1000.,1000.,1050.,1150.],stats(150,orders=4,fills=2,trades=1))
        add(v,'cancel',f'if bar_index==0\n    strategy.entry("L",{long},limit=95)\n    strategy.exit("A","L",stop=90,limit=110,qty=10)\nif bar_index==1\n    strategy.cancel_all()',flat,[0.]*4,[1000.]*4,stats(orders=2,fills=0,trades=0,closed=0))
        add(v,'na-market',f'if bar_index==0\n    strategy.entry("L",{long},limit=na,stop=na)\nif bar_index==1\n    strategy.close("L",qty=5)\nif bar_index==2\n    strategy.close("L",qty_percent=100)',market,[0.,10.,5.,0.],[1000.,1050.,1100.,1000.],stats())
        for active in (False,True):
            record={'entry_id':'L','direction':1,'quantity':'10','limit':'95','stop':'105' if active else None,'order_kind':'stop_limit' if active else 'limit',
                'status':'limit_active' if active else 'pending','created_bar':0,'activated_bar':1 if active else None,'activation_basis':'ohlc_touch' if active else None}
            add(v,'pending-active' if active else 'pending-limit',f'if bar_index==0\n    strategy.entry("L",{long},limit=95'+(',stop=105)' if active else ')'),pending,[0.]*4,[1000.]*4,stats(orders=1,fills=0,trades=0,closed=0,entries=[record]),pending_names=['L'])
        for name,params,category,code in [('limit-zero','limit=0','RUNTIME_ERROR','ORDER_LEVEL'),('stop-zero','stop=0','RUNTIME_ERROR','ORDER_LEVEL'),
                ('qty-zero','limit=95,qty=0','RUNTIME_ERROR','ORDER_LEVEL'),('oca','limit=95,oca_name="group"','LANGUAGE_INVALID','UNKNOWN_ARGUMENT'),
                ('comment','limit=95,comment="x"','LANGUAGE_INVALID','UNKNOWN_ARGUMENT'),('expiry','limit=95,expiry=1','LANGUAGE_INVALID','UNKNOWN_ARGUMENT')]:
            if name=='oca':
                # Preserve the source and case ID; now verify the pending order.
                from tools.order_group_oracle import statistics as group_stats,waiting
                state=group_stats(orders=1);state['entries']=[waiting('L',10,95)]
                add(v,'reject-oca',f'if bar_index==0\n    strategy.entry("L",{long},{params})',flat,[0.]*4,[1000.]*4,state,pending_names=['L'])
                continue
            filename=f'v{v}-reject-{name}.pine';files['conformance/pending-entries/'+filename]=source(v,f'if bar_index==0\n    strategy.entry("L",{long},{params})').encode()
            cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':filename,'bars':'flat.json','expected':{'status':'rejected','category':category,'code':code}})
        for family in CYCLES:
            make='input.int' if v>=5 else 'input';body=f'part={make}(8,title="First take",minval=1,maxval=9)\n'+family_body(v,family,quantity='part',repeat=True)
            files[f'examples/pending-entries/{family}-v{v}.pine']=(source(v,body,1)+ 'plot(strategy.position_avg_price,title="Entry price")\n').encode()
    files['conformance/pending-entries/cases.json']=encode({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Hand-authored OHLC entry/exit prices, quantities, phase deferral and Decimal fee arithmetic. Pending semantics exclude execution hashes/call-site IDs; no broker-generated expectations.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})
    files['examples/pending-entries/contexts.json']=encode({'schema_version':'openpine.context-inputs.1','contexts':[],
        'primary_context':{'context_id':'entry','symbol':'TEST:ENTRY','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}})
    files['examples/pending-entries/experiment.json']=encode({'name':'Pending entry experiment','grid':{'part':[7,8]},'inputs':{},'timeout_seconds':120})
    common={'budget':2,'inputs':{},'method':'grid','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'part':[7,8]},'timeout_seconds':120}
    files['examples/pending-entries/search.json']=encode({**common,'name':'Pending entry search','windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':15000000},'holdout':{'warmup_start_ms':14400000,'score_start_ms':15000000,'end_ms':24000000}}})
    files['examples/pending-entries/walkforward.json']=encode({**common,'name':'Pending entry walkforward','walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':600000,'step_ms':3600000,'test_ms':3600000,'train_ms':7200000,'warmup_ms':600000}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        path=ROOT/name
        if args.check:
            if not path.is_file() or path.read_bytes()!=raw:raise ValueError('Pending fixture differs: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Pending-entry independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
