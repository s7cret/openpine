"""Independent lot expectations and eighteen versioned executable examples."""
import argparse
from decimal import Decimal
from fractions import Fraction
from tools.specification import ROOT,encode
from tools.lot_oracle import expected,cycle_totals

CYCLES={
 'fifo':[(100,101,99,100),(100,101,99,100),(110,111,109,110),(120,121,119,120),(110,111,109,110),(100,101,99,100)],
 'any':[(100,101,99,100),(100,101,99,100),(110,111,109,110),(120,121,119,120),(110,111,109,110),(100,101,99,100)],
 'conditional':[(100,101,99,100),(100,101,94,100),(100,106,99,104),(104,106,102,104),(110,111,109,110),(110,116,109,115),(110,121,109,120),(100,101,99,100)]}

def bars(rows,count=None,short=False):
    result=[]
    for i in range(count or len(rows)):
        o,h,l,c=rows[i%len(rows)]
        if short:o,h,l,c=200-o,200-l,200-h,200-c
        result.append({'time':i*60000,'open':float(o),'high':float(h),'low':float(l),'close':float(c),'volume':1.})
    return {'schema_version':'openpine.historical-bars.2','context_id':'lots','symbol':'TEST:LOTS','timeframe':'1','bars':result}

def source(v,body,*,rule='ANY',cap=3,fee=1,inputs=False):
    header=f'//@version={v}\nstrategy("Lot entries",pyramiding={cap},close_entries_rule="{rule}",default_qty_type=strategy.fixed,default_qty_value=1,initial_capital=1000,commission_value={fee})\n'
    if inputs:header+=('part=input.int(2,title="Partial quantity",minval=1,maxval=2)\n' if v>=5 else 'part=input(2,title="Partial quantity",minval=1,maxval=2)\n')
    return header+body+'\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")\n'

def example_body(v,family,*,repeat=True,part='part'):
    direction='true' if v==1 else 'strategy.long';n=8 if family=='conditional' else 6
    phase=lambda i:f'bar_index%{n}=={i}' if repeat else f'bar_index=={i}'
    if family!='conditional':
        return f'if {phase(0)}\n    strategy.entry("A",{direction},qty=2)\nif {phase(1)}\n    strategy.entry("B",{direction},qty=3)\nif {phase(2)}\n    strategy.close("B",qty={part})\nif {phase(3)}\n    strategy.close("A")\n    strategy.close("B")'
    return f'if {phase(0)}\n    strategy.entry("A",{direction},qty=2,limit=95)\n    strategy.exit("PA","A",stop=80,limit=140,qty_percent=100)\nif {phase(1)}\n    strategy.entry("B",{direction},qty=3,stop=105,limit=103)\n    strategy.exit("PB","B",stop=80,limit=140,qty_percent=100)\nif {phase(3)}\n    strategy.close("B",qty={part})\nif {phase(4)}\n    strategy.exit("PA","A",stop=80,limit=115,qty_percent=100)\n    strategy.exit("PB","B",stop=80,limit=120,qty_percent=100)'

def stats(gross=0,entry_fee=0,exit_fee=0,*,orders=4,fills=4,trades=2,closed_lots=2,closed_positions=1):
    text=lambda v:format(Decimal(v).normalize(),'f') if v else '0';fees=Decimal(entry_fee)+Decimal(exit_fee);cash=1000+Decimal(gross)-fees
    return {'cash':text(cash),'equity':text(cash),'position_size':'0','position_avg_price':None,'gross_realized':text(gross),'unrealized':'0',
        'commission':text(fees),'orders':orders,'fills':fills,'trades':trades,'cash_exact':text(cash),'equity_exact':text(cash),'gross_realized_exact':text(gross),
        'unrealized_exact':'0','allocated_entry_commission':text(entry_fee),'entry_commission':text(entry_fee),'exit_commission':text(exit_fee),
        'closed_positions':closed_positions,'protections':[],'entries':[],'lots':[],'open_lots':0,'closed_lots':closed_lots}

def prepare():
    files={};cases=[]
    def fixture(name,rows,short=False):files[f'conformance/lots/{name}.json']=encode(bars(rows,short=short));return name+'.json'
    for family,rows in CYCLES.items():
        fixture(family,rows);files[f'examples/lots/{family}-bars.json']=encode(bars(rows,count=400 if family=='conditional' else 360))
    entry_rows=[(100,101,99,100),(100,101,99,100),(110,116,104,110),(114,116,111,114),(120,121,119,120)]
    fixture('entries',entry_rows);fixture('entries-short',entry_rows,True)
    fixture('reverse',[(p,p+1,p-1,p) for p in (100,100,110,120,100,90)])
    fixture('capacity',[(p,p+1,p-1,p) for p in (100,100,110)])
    fixture('cancel',[(p,p+1,p-1,p) for p in (100,100,110,120)])
    fixture('open',CYCLES['any'][:3]);fixture('pending',CYCLES['any'][:1])
    def add(v,name,text,data,outputs,statistics,pending=None):
        filename=f'v{v}-{name}.pine';files['conformance/lots/'+filename]=text.encode()
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':data,'expected':{'status':'executed','title':'Lot entries','shorttitle':'','overlay':False,
            'observation_count':len(outputs[0]),'qualifiers':['series']*3,'outputs':[{'title':t,'type':'float','values':list(values)} for t,values in zip(('Position','Equity','Entry price'),outputs,strict=True)],
            'simulation_statistics':statistics,'pending_entries':pending or []}})
    for v in range(1,7):
        long='true' if v==1 else 'strategy.long';short='false' if v==1 else 'strategy.short'
        for family in CYCLES:
            for part in (1,2):
                add(v,f'{family}-{part}',source(v,example_body(v,family,repeat=False,part=str(part)),rule='FIFO' if family=='fifo' else 'ANY'),family+'.json',expected(family,part,1),stats(**cycle_totals(family,part)))
            files[f'examples/lots/{family}-v{v}.pine']=source(v,example_body(v,family),rule='FIFO' if family=='fifo' else 'ANY',inputs=True).encode()
        for family,price,params in [('market',110,''),('limit',105,',limit=105'),('stop',115,',stop=115'),('stop-limit',112,',stop=115,limit=112')]:
            for bearish in (False,True):
                direction=short if bearish else long;actual=200-price if bearish else price;sign=-1 if bearish else 1
                arguments=params.replace('105','95').replace('115','85').replace('112','88') if bearish else params
                body=f'if bar_index==0\n    strategy.entry("A",{direction},qty=2)\nif bar_index==1\n    strategy.entry("B",{direction},qty=3{arguments})\nif bar_index==3\n    strategy.close("A")\n    strategy.close("B")'
                active=3 if family=='stop-limit' else 2;positions=[];equity=[];avg=[];entry_fee=Decimal(2)+Decimal(actual)*Decimal('.03')
                for i,(_,_,_,mark) in enumerate(entry_rows):
                    mark=200-mark if bearish else mark
                    if i==0:positions.append(0.);equity.append(1000.);avg.append(None)
                    elif i<4:
                        quantity=5 if i>=active else 2;fee=entry_fee if quantity==5 else Decimal(2)
                        gain=Decimal(2)*(Decimal(mark)-100)*sign+(Decimal(3)*(Decimal(mark)-actual)*sign if quantity==5 else 0)
                        positions.append(float(sign*quantity));equity.append(float(1000+gain-fee));avg.append(float(Fraction(200+3*actual,5)) if quantity==5 else 100.)
                    else:
                        gross=Decimal(40)+3*(120-price);exit_fee=Decimal(4 if bearish else 6)
                        positions.append(0.);equity.append(float(1000+gross-entry_fee-exit_fee));avg.append(None)
                add(v,f'{family}-'+('short' if bearish else 'long'),source(v,body),'entries-short.json' if bearish else 'entries.json',(positions,equity,avg),stats(gross,entry_fee,exit_fee))
        reverse=f'if bar_index==0\n    strategy.entry("A",{long},qty=2)\nif bar_index==1\n    strategy.entry("B",{long},qty=3)\nif bar_index==2\n    strategy.entry("S",{short},qty=4)\nif bar_index==3\n    strategy.close("S",qty=1)\nif bar_index==4\n    strategy.close("S")'
        add(v,'reverse',source(v,reverse),'reverse.json',([0.,2.,5.,-4.,-3.,0.],[1000.,998.,1014.7,1053.9,1132.9,1160.2],[None,100.,106.,120.,120.,None]),stats(180,'10.1','9.7',orders=5,fills=6,trades=4,closed_lots=3,closed_positions=2))
        body=f'if bar_index==0\n    strategy.entry("A",{long})\n    strategy.entry("B",{long})\n    strategy.entry("C",{long})\nif bar_index==1\n    strategy.close("A")\n    strategy.close("B")'
        add(v,'capacity',source(v,body,cap=2),'capacity.json',([0.,2.,0.],[1000.,998.,1015.8],[None,100.,None]),stats(20,2,'2.2',orders=5))
        body=f'if bar_index==0\n    strategy.entry("A",{long},qty=2)\nif bar_index==1\n    strategy.entry("B",{long},qty=3,stop=115)\n    strategy.exit("PB","B",stop=80,limit=140,qty_percent=100)\nif bar_index==2\n    strategy.cancel("B")\n    strategy.close("A")'
        add(v,'cancel',source(v,body),'cancel.json',([0.,2.,2.,0.],[1000.,998.,1018.,1035.6],[None,100.,100.,None]),stats(40,2,'2.4',orders=4,fills=2,trades=1,closed_lots=1))
        body=f'if bar_index==0\n    strategy.entry("A",{long},qty=2)\nif bar_index==1\n    strategy.entry("B",{long},qty=3)'
        opened=stats(0,'5.3',0,orders=2,fills=2,trades=0,closed_lots=0,closed_positions=0)
        opened.update(cash='464.7',cash_exact='464.7',equity='1014.7',equity_exact='1014.7',position_size='5',position_avg_price='106',unrealized='20',unrealized_exact='20',allocated_entry_commission='0',open_lots=2,
            lots=[{'entry_id':name,'direction':1,'quantity':str(q),'price':str(p),'commission':fee,'bar_index':i,'original_quantity':str(q),'original_commission':fee,'closed_quantity':'0','allocated_commission':'0','closed_slices':0} for name,q,p,fee,i in [('A',2,100,'2',1),('B',3,110,'3.3',2)]])
        add(v,'open-lots',source(v,body),'open.json',([0.,2.,5.],[1000.,998.,1014.7],[None,100.,106.]),opened)
        body=f'if bar_index==0\n    strategy.entry("A",{long},qty=2,limit=95)\n    strategy.exit("PA","A",stop=80,limit=120,qty_percent=100)'
        waiting=stats(orders=2,fills=0,trades=0,closed_lots=0,closed_positions=0)
        waiting.update(entries=[{'entry_id':'A','direction':1,'quantity':'2','limit':'95','stop':None,'order_kind':'limit','status':'pending','created_bar':0,'activated_bar':None,'activation_basis':None}],
            protections=[{'exit_id':'PA','from_entry':'A','stop':'80','limit':'120','quantity':'2','created_bar':0,'eligible_bar':1,'direction':1,'targets':[{'kind':'pending','entry_id':'A','created_bar':0,'quantity':'2'}]}])
        add(v,'pending-protection',source(v,body),'pending.json',([0.],[1000.],[None]),waiting,['A'])
        negatives=[('cap',source(v,'',cap=17),'PROFILE_UNCOVERED','STRATEGY_PROFILE'),('rule',source(v,'',rule='LIFO'),'PROFILE_UNCOVERED','STRATEGY_PROFILE'),
            ('quantity',source(v,f'strategy.entry("L",{long},qty=-1)'),'RUNTIME_ERROR','ORDER_QUANTITY'),
            ('step',source(v,f'strategy.entry("L",{long},qty=0.000000001)'),'RUNTIME_ERROR','ORDER_QUANTITY'),
            ('level',source(v,f'strategy.entry("L",{long},stop=-1)'),'RUNTIME_ERROR','ORDER_LEVEL'),
            ('oca',source(v,f'strategy.entry("L",{long},oca_name="custom")'),'LANGUAGE_INVALID','UNKNOWN_ARGUMENT')]
        for name,text,category,code in negatives:
            if name=='oca':
                # This formerly unsupported source now has an admitted none group.
                from tools.order_group_oracle import statistics as group_stats,waiting
                state=group_stats(orders=1);state['entries']=[waiting('L',1)]
                add(v,'reject-oca',text,'pending.json',([0.],[1000.],[None]),state,['L'])
                continue
            filename=f'v{v}-reject-{name}.pine';files['conformance/lots/'+filename]=text.encode()
            cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':filename,'bars':'pending.json','expected':{'status':'rejected','category':category,'code':code}})
    files['conformance/lots/cases.json']=encode({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],
        'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False},'cases':cases})
    files['examples/lots/contexts.json']=encode({'schema_version':'openpine.context-inputs.1','primary_context':{'context_id':'lots','symbol':'TEST:LOTS','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'},'contexts':[]})
    files['examples/lots/experiment.json']=encode({'grid': {'part': [1, 2]}, 'inputs': {}, 'name': 'Lot experiment', 'timeout_seconds': 120})
    files['examples/lots/search.json']=encode({'budget': 2, 'inputs': {}, 'method': 'grid', 'name': 'Lot search', 'objective': {'direction': 'maximize', 'metric': 'net_profit'}, 'seed': 0, 'space': {'part': [1, 2]}, 'timeout_seconds': 120, 'windows': {'holdout': {'end_ms': 24000000, 'score_start_ms': 15000000, 'warmup_start_ms': 14400000}, 'selection': {'end_ms': 15000000, 'score_start_ms': 0, 'warmup_start_ms': 0}}})
    files['examples/lots/walkforward.json']=encode({'budget': 2, 'inputs': {}, 'method': 'grid', 'name': 'Lot walkforward', 'objective': {'direction': 'maximize', 'metric': 'net_profit'}, 'seed': 0, 'space': {'part': [1, 2]}, 'timeout_seconds': 120, 'walkforward': {'folds': 2, 'max_jobs': 6, 'mode': 'rolling', 'start_ms': 600000, 'step_ms': 3600000, 'test_ms': 3600000, 'train_ms': 7200000, 'warmup_ms': 600000}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        target=ROOT/name
        if args.check:
            if not target.exists() or target.read_bytes()!=raw:raise ValueError('Lot fixture differs: '+name)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print('Lot independent fixtures and eighteen examples verified.');return 0
if __name__=='__main__':raise SystemExit(main())
