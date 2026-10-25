"""Independent expectations and eighteen versioned examples of grouped orders."""
import argparse
from decimal import Decimal
from tools.specification import ROOT,encode
from tools.order_group_oracle import expected,cycle_totals,statistics,waiting

CYCLES={
    'netting':[(p,p+1,p-1,p) for p in (100,100,110,120,90,100)],
    'cancel':[(100,101,99,100),(100,106,94,100),(110,121,109,115),(100,101,99,100),(100,101,99,100),(100,101,99,100)],
    'reduce':[(100,101,99,100),(100,106,99,100),(90,100,89,95),(100,111,99,100),(110,121,109,110),(100,101,99,100),(100,101,99,100),(100,101,99,100)]}


def bars(rows,count=None,short=False):
    result=[]
    for i in range(count or len(rows)):
        o,h,l,c=rows[i%len(rows)]
        if short:o,h,l,c=200-o,200-l,200-h,200-c
        result.append({'time':i*60000,'open':float(o),'high':float(h),'low':float(l),'close':float(c),'volume':1.})
    return {'schema_version':'openpine.historical-bars.2','context_id':'groups','symbol':'TEST:GROUPS','timeframe':'1','bars':result}


def source(v,body,*,inputs=False):
    head=f'//@version={v}\nstrategy("Order groups",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.fixed,default_qty_value=1,initial_capital=1000,commission_value=1)\n'
    if inputs:head+=('part=input.int(2,title="Order quantity",minval=1,maxval=2)\n' if v>=5 else 'part=input(2,title="Order quantity",minval=1,maxval=2)\n')
    return head+body+'\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")\n'


def example_body(v,family,*,repeat=True,part='part'):
    long='true' if v==1 else 'strategy.long';short='false' if v==1 else 'strategy.short';length=8 if family=='reduce' else 6
    phase=lambda i:f'bar_index%{length}=={i}' if repeat else f'bar_index=={i}'
    if family=='netting':
        return f'if {phase(0)}\n    strategy.order("Buy",{long},qty=5)\nif {phase(1)}\n    strategy.order("Reduce",{short},qty={part})\nif {phase(2)}\n    strategy.order("Flip",{short},qty=8)\nif {phase(3)}\n    strategy.close_all()'
    if family=='cancel':
        return f'if {phase(0)}\n    strategy.entry("L",{long},qty={part}+1,stop=105,oca_name="entry-pair",oca_type=strategy.oca.cancel)\n    strategy.entry("S",{short},qty=3,stop=95,oca_name="entry-pair",oca_type=strategy.oca.cancel)\n    strategy.exit("PL","L",limit=120,qty_percent=100)'
    return f'if {phase(0)}\n    strategy.order("A",{long},qty={part},limit=95,oca_name="entries",oca_type=strategy.oca.reduce)\n    strategy.order("B",{long},qty=5,stop=105,limit=103,oca_name="entries",oca_type=strategy.oca.reduce)\n    strategy.exit("PA","A",limit=110,qty_percent=100,oca_name="profit")\n    strategy.exit("PB","B",limit=120,qty_percent=100,oca_name="profit")\nif {phase(4)}\n    strategy.close_all()'


def prepare():
    files={};cases=[]
    def fixture(name,rows,short=False):files[f'conformance/order-groups/{name}.json']=encode(bars(rows,short=short));return name+'.json'
    for family,rows in CYCLES.items():
        fixture(family,rows);files[f'examples/order-groups/{family}-bars.json']=encode(bars(rows,count=400 if family=='reduce' else 360))
    flat=fixture('flat',[(100,101,99,100)]*4);one=fixture('one',[(100,101,99,100)])
    entry_rows=[(100,101,99,100),(110,116,104,110),(114,116,111,114),(120,121,119,120),(100,101,99,100)]
    fixture('entries',entry_rows);fixture('entries-short',entry_rows,True)
    def add(v,name,body,data,outputs,stats,pending=None):
        filename=f'v{v}-{name}.pine';files['conformance/order-groups/'+filename]=source(v,body).encode()
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':filename,'bars':data,'expected':{'status':'executed','title':'Order groups','shorttitle':'','overlay':False,
            'observation_count':len(outputs[0]),'qualifiers':['series']*3,'outputs':[{'title':t,'type':'float','values':list(values)} for t,values in zip(('Position','Equity','Entry price'),outputs,strict=True)],
            'simulation_statistics':stats,'pending_entries':pending or []}})
    for v in range(1,7):
        long='true' if v==1 else 'strategy.long';short='false' if v==1 else 'strategy.short'
        for family in CYCLES:
            for part in (1,2):add(v,f'{family}-{part}',example_body(v,family,repeat=False,part=str(part)),family+'.json',expected(family,part,1),statistics(**cycle_totals(family,part)))
            files[f'examples/order-groups/{family}-v{v}.pine']=source(v,example_body(v,family),inputs=True).encode()
        for kind,q,fee,orders,fills,trades,lots,positions in [('none',-1,6,3,4,2,2,2),('cancel',2,4,3,2,1,1,1),('reduce',5,10,4,3,2,2,1)]:
            body=f'if bar_index==0\n    strategy.order("A",{long},qty=2,oca_name="g",oca_type=strategy.oca.{kind})\n    strategy.order("B",{short if kind!="reduce" else long},qty={3 if kind!="reduce" else 5},oca_name="g",oca_type=strategy.oca.{kind})'
            if kind=='reduce':body+=f'\n    strategy.order("C",{long},qty=1,oca_name="g",oca_type=strategy.oca.reduce)'
            body+='\nif bar_index==1\n    strategy.close_all()'
            entry_fee=3 if kind=='none' else 2 if kind=='cancel' else 5
            add(v,'open-'+kind,body,flat,([0.,float(q),0.,0.],[1000.,1000.-(5 if kind=='none' else 2 if kind=='cancel' else 5),1000.-fee,1000.-fee],[None,100.,None,None]),
                statistics(0,entry_fee,fee-entry_fee,orders=orders,fills=fills,trades=trades,closed_lots=lots,closed_positions=positions))
        for kind,price,arguments in [('market',110,''),('limit',105,',limit=105'),('stop',115,',stop=115'),('stop-limit',112,',stop=115,limit=112')]:
            for bearish in (False,True):
                actual=200-price if bearish else price;direction=short if bearish else long;sign=-1 if bearish else 1
                params=arguments.replace('105','95').replace('115','85').replace('112','88') if bearish else arguments
                body=f'if bar_index==0\n    strategy.order("A",{direction},qty=2{params})\nif bar_index==2\n    strategy.close_all()'
                active=2 if kind=='stop-limit' else 1;positions=[];equity=[];avg=[];entry_fee=Decimal(actual)*Decimal('.02');exit_fee=Decimal('1.6') if bearish else Decimal('2.4');gross=2*(120-price)
                for i,(_,_,_,mark) in enumerate(entry_rows):
                    mark=200-mark if bearish else mark
                    q=sign*2 if active<=i<3 else 0
                    positions.append(float(q));avg.append(float(actual) if q else None)
                    equity.append(float(1000+2*(Decimal(mark)-actual)*sign-entry_fee) if q else float(1000+gross-entry_fee-exit_fee) if i>=3 else 1000.)
                add(v,kind+('-short' if bearish else '-long'),body,'entries-short.json' if bearish else 'entries.json',(positions,equity,avg),statistics(gross,entry_fee,exit_fee,orders=2,fills=2,trades=1,closed_lots=1,closed_positions=1))
        body=f'if bar_index==0\n    strategy.order("A",{long},qty=2)\nif bar_index==1\n    strategy.order("B",{long},qty=3)\n    strategy.close_all()\nif bar_index==2\n    strategy.close_all()'
        add(v,'close-all-future',body,flat,([0.,2.,3.,0.],[1000.,998.,993.,990.],[None,100.,100.,None]),statistics(0,5,5,orders=4,fills=4,trades=2,closed_lots=2,closed_positions=1))
        body=f'if bar_index==0\n    strategy.entry("A",{long},qty=3,limit=90,oca_name="old",oca_type=strategy.oca.cancel)\n    strategy.exit("PA","A",limit=120,qty_percent=100,oca_name="profit")\nif bar_index==1\n    strategy.order("A",{long},qty=2,limit=95,oca_name="new",oca_type=strategy.oca.reduce)\nif bar_index==2\n    strategy.cancel("A")'
        add(v,'replace-group',body,flat,([0.]*4,[1000.]*4,[None]*4),statistics(orders=3))
        body=f'if bar_index==0\n    strategy.order("A",{long},qty=2,limit=95,oca_name="a",oca_type=strategy.oca.cancel)\n    strategy.order("B",{long},qty=3,limit=90,oca_name="b",oca_type=strategy.oca.reduce)\nif bar_index==1\n    strategy.cancel_all()'
        add(v,'cancel-all',body,flat,([0.]*4,[1000.]*4,[None]*4),statistics(orders=2))
        body=f'if bar_index==0\n    strategy.order("A",{long},qty=3)\nif bar_index==1\n    strategy.entry("R",{short},qty=2,oca_name="g",oca_type=strategy.oca.reduce)\n    strategy.order("P",{long},qty=7,limit=90,oca_name="g",oca_type=strategy.oca.reduce)\nif bar_index==2\n    strategy.cancel("P")\n    strategy.close_all()'
        add(v,'entry-reversal',body,flat,([0.,3.,-2.,0.],[1000.,997.,992.,990.],[None,100.,100.,None]),statistics(0,5,5,orders=4,fills=4,trades=2,closed_lots=2,closed_positions=2))
        body=f'strategy.order("A",{long},qty=2,limit=95,oca_name="g",oca_type=strategy.oca.cancel)\nstrategy.order("B",{long},qty=3,stop=105,oca_name="g",oca_type=strategy.oca.cancel)'
        waiting_stats=statistics(orders=2);waiting_stats['entries']=[waiting('A',2,95),waiting('B',3,stop=105)]
        waiting_stats['order_groups']=[{'name':'g','type':'cancel','members':[{'command':'order','name':'g','type':'cancel','original_quantity':str(q),'remaining_quantity':str(q),'entry_id':name,'exit_id':''} for name,q in [('A',2),('B',3)]]}]
        add(v,'unfilled-group',body,one,([0.],[1000.],[None]),waiting_stats,['A','B'])
        negatives=[
            ('type',f'strategy.order("A",{long},oca_name="g",oca_type="bad")','PROFILE_UNCOVERED','OCA_BINDING'),
            ('name',f'strategy.order("A",{long},oca_type=strategy.oca.cancel)','PROFILE_UNCOVERED','OCA_BINDING'),
            ('quantity',f'strategy.order("A",{long},qty=-1)','RUNTIME_ERROR','ORDER_LEVEL'),
            ('step',f'strategy.order("A",{long},qty=0.000000001)','RUNTIME_ERROR','ORDER_QUANTITY'),
            ('level',f'strategy.order("A",{long},stop=-1)','RUNTIME_ERROR','ORDER_LEVEL'),
            ('immediate','strategy.close_all(immediately=true)','LANGUAGE_INVALID','UNKNOWN_ARGUMENT'),
            ('trailing','strategy.exit("P","A",trail_offset=2,oca_name="g")','LANGUAGE_INVALID','UNKNOWN_ARGUMENT'),
            ('long-name',f'strategy.order("A",{long},oca_name="'+('x'*129)+'",oca_type=strategy.oca.cancel)','PROFILE_UNCOVERED','OCA_BINDING'),
            ('conflict',f'strategy.order("A",{long},oca_name="g",oca_type=strategy.oca.cancel)\nstrategy.order("B",{long},oca_name="g",oca_type=strategy.oca.reduce)','RUNTIME_ERROR','ORDER_QUANTITY')]
        for name,body,category,code in negatives:
            filename=f'v{v}-reject-{name}.pine';files['conformance/order-groups/'+filename]=source(v,body).encode()
            cases.append({'case_id':f'v{v}-reject-{name}','language_version':v,'source':filename,'bars':one,'expected':{'status':'rejected','category':category,'code':code}})
    files['conformance/order-groups/cases.json']=encode({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','rule_refs':[],'assertion_refs':[],
        'oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Closed-form phase quantities, cash, fill fees and exact lot allocations; no broker-derived expectations.'},'cases':cases})
    files['examples/order-groups/contexts.json']=encode({'schema_version':'openpine.context-inputs.1','primary_context':{'context_id':'groups','symbol':'TEST:GROUPS','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'},'contexts':[]})
    files['examples/order-groups/experiment.json']=encode({'grid':{'part':[1,2]},'inputs':{},'name':'Order group experiment','timeout_seconds':120})
    files['examples/order-groups/search.json']=encode({'budget':2,'inputs':{},'method':'grid','name':'Order group search','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'part':[1,2]},'timeout_seconds':120,
        'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':14400000},'holdout':{'warmup_start_ms':14400000,'score_start_ms':14400000,'end_ms':19200000}}})
    files['examples/order-groups/walkforward.json']=encode({'budget':2,'inputs':{},'method':'grid','name':'Order group walkforward','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'part':[1,2]},'timeout_seconds':120,
        'walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':0,'step_ms':4800000,'test_ms':4800000,'train_ms':9600000,'warmup_ms':0}})
    return files


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        target=ROOT/name
        if args.check:
            if not target.exists() or target.read_bytes()!=raw:raise ValueError('Order group fixture differs: '+name)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print('Order group independent corpus and eighteen examples verified.');return 0
if __name__=='__main__':raise SystemExit(main())
