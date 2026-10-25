"""Independent four-bar partial fill arithmetic and versioned Pine programs."""
import argparse
from decimal import Decimal
from tools.specification import ROOT,encode

def source(version,body,fee=0):
    return f'//@version={version}\nstrategy("Partial exits",default_qty_type=strategy.fixed,default_qty_value=10,initial_capital=1000,commission_type=strategy.commission.percent,commission_value={fee})\n{body}\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\n'

def bars(short=False,count=4):
    rows=[(100,101,99,100),(100,101,89,90),(100,106,99,105),(100,101,99,100)] if short else [(100,101,99,100),(100,111,99,110),(100,101,94,95),(100,101,99,100)]
    return {'schema_version':'openpine.historical-bars.2','context_id':'partial','symbol':'TEST:PARTIAL','timeframe':'1',
        'bars':[{'time':i*60000,'open':float(o),'high':float(h),'low':float(l),'close':float(c),'volume':100.} for i,(o,h,l,c) in enumerate((rows*((count+3)//4))[:count])]}

def stats(gross,fee=0,short=False,orders=3,fills=3,trades=2):
    entry=Decimal('10') if fee else Decimal(0)
    exits=Decimal('9.75' if short else '10.25') if fee else Decimal(0)
    commission=entry+exits;final=Decimal(1000)+Decimal(gross)-commission
    text=lambda x:format(Decimal(x).normalize(),'f') if x else '0'
    return {'cash':text(final),'equity':text(final),'position_size':'0','position_avg_price':None,
        'gross_realized':text(gross),'unrealized':'0','commission':text(commission),'orders':orders,'fills':fills,'trades':trades,
        'cash_exact':text(final),'equity_exact':text(final),'gross_realized_exact':text(gross),'unrealized_exact':'0',
        'allocated_entry_commission':text(entry),'entry_commission':text(entry),'exit_commission':text(exits),'closed_positions':1,'protections':[]}

def prepare():
    files={'conformance/partial-exits/bars.json':encode(bars()),'conformance/partial-exits/short.json':encode(bars(True))};cases=[]
    for version in range(1,7):
        long='true' if version==1 else 'strategy.long';short='false' if version==1 else 'strategy.short'
        first=f'if bar_index==0\n    strategy.entry("L",{long})'
        protective=lambda qty1='qty=5',qty2='qty=5':first+f'\n    strategy.exit("A","L",stop=95,limit=110,{qty1})\n    strategy.exit("B","L",stop=95,limit=120,{qty2})'
        samples=[('take-stop',protective(),False,0,[0.,5.,0.,0.],[1000.,1100.,1025.,1025.],stats(25)),
            ('percent',protective('qty_percent=50','qty_percent=50'),False,0,[0.,5.,0.,0.],[1000.,1100.,1025.,1025.],stats(25)),
            ('priority',protective('qty=5,qty_percent=0','qty=5,qty_percent=0'),False,0,[0.,5.,0.,0.],[1000.,1100.,1025.,1025.],stats(25)),
            ('clip',protective('qty=8','qty=8'),False,0,[0.,2.,0.,0.],[1000.,1100.,1070.,1070.],stats(70)),
            ('fee',protective(),False,1,[0.,5.,0.,0.],[1000.,1084.5,1004.75,1004.75],stats(25,1)),
            ('short-fee',f'if bar_index==0\n    strategy.entry("L",{short})\n    strategy.exit("A","L",stop=105,limit=90,qty=5)\n    strategy.exit("B","L",stop=105,limit=80,qty=5)',True,1,[0.,-5.,0.,0.],[1000.,1085.5,1005.25,1005.25],stats(25,1,True)),
            ('manual',first+'\nif bar_index==1\n    strategy.close("L",qty=5)\nif bar_index==2\n    strategy.close("L",qty_percent=100)',False,0,[0.,10.,5.,0.],[1000.,1100.,975.,1000.],stats(0)),
            ('cancel',protective()+'\nif bar_index==1\n    strategy.cancel("B")\n    strategy.close("L",qty_percent=100)',False,0,[0.,5.,0.,0.],[1000.,1100.,1050.,1050.],stats(50,orders=4)),
            ('na-fallback',protective('qty=na','qty=5'),False,0,[0.,0.,0.,0.],[1000.,1100.,1100.,1100.],stats(100,orders=2,fills=2,trades=1))]
        for name,body,short_data,fee,positions,equity,statistics in samples:
            filename=f'v{version}-{name}.pine';files['conformance/partial-exits/'+filename]=source(version,body,fee).encode()
            cases.append({'case_id':f'v{version}-{name}','language_version':version,'source':filename,'bars':'short.json' if short_data else 'bars.json',
                'expected':{'status':'executed','title':'Partial exits','shorttitle':'','overlay':False,'observation_count':4,'qualifiers':['series','series'],
                    'outputs':[{'title':'Position','type':'float','values':positions},{'title':'Equity','type':'float','values':equity}],
                    'simulation_statistics':statistics,'pending_entries':[]}})
        failures=[('negative',first+'\n    strategy.exit("A","L",stop=95,qty=-1)','RUNTIME_ERROR','ORDER_QUANTITY'),
            ('percent-range',first+'\n    strategy.exit("A","L",stop=95,qty_percent=101)','RUNTIME_ERROR','ORDER_QUANTITY'),
            ('na-levels',first+'\n    strategy.exit("A","L",stop=na,limit=na,qty=5)','RUNTIME_ERROR','ORDER_QUANTITY'),
            ('missing-levels',first+'\n    strategy.exit("A","L",qty=5)','PROFILE_UNCOVERED','EXIT_LEVEL_REQUIRED'),
            ('profit-excluded',first+'\n    strategy.exit("A","L",profit=10,qty=5)','LANGUAGE_INVALID','UNKNOWN_ARGUMENT')]
        for name,body,category,code in failures:
            filename=f'v{version}-reject-{name}.pine';files['conformance/partial-exits/'+filename]=source(version,body).encode()
            cases.append({'case_id':f'v{version}-reject-{name}','language_version':version,'source':filename,'bars':'bars.json','expected':{'status':'rejected','category':category,'code':code}})
            if name=='profit-excluded':
                from dataclasses import asdict
                from packages.contracts.trading import BrokerSettings
                from tools.trailing_oracle import statistics
                st=statistics(50,orders=2,fills=2,trades=1)
                st.update(cash='550',cash_exact='550',position_size='5',position_avg_price='100',open_lots=1,
                    lots=[{'entry_id':'L','direction':1,'quantity':'5','price':'100','commission':'0','bar_index':1,
                           'original_quantity':'10','original_commission':'0','closed_quantity':'5','allocated_commission':'0','closed_slices':1}])
                cases[-1]['broker_settings']=asdict(BrokerSettings(tick_size='1'))
                cases[-1]['expected']={'status':'executed','title':'Partial exits','shorttitle':'','overlay':False,'observation_count':4,
                    'qualifiers':['series','series'],'outputs':[{'title':'Position','type':'float','values':[0.,5.,5.,5.]},
                        {'title':'Equity','type':'float','values':[1000.,1100.,1025.,1050.]}],'simulation_statistics':st,'pending_entries':[]}
        # Long examples include extra flat bars for preparation and research windows.
        make='input.int' if version>=5 else 'input'
        example=protective('qty=part','qty_percent=100').replace('if bar_index==0','if bar_index%4==0')
        example=f'part={make}(5,title="First take",minval=1,maxval=9)\n'+example
        files[f'examples/partial-exits/partial-v{version}.pine']=source(version,example,1).encode()
    files['conformance/partial-exits/cases.json']=encode({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Hand-authored four-bar long/short fills, remaining quantities and exact Decimal fee arithmetic; no broker-generated expectations.','external_pine_execution':False},
        'rule_refs':[],'assertion_refs':[],'cases':cases})
    files['examples/partial-exits/bars.json']=encode(bars(count=360))
    files['examples/partial-exits/contexts.json']=encode({'schema_version':'openpine.context-inputs.1','contexts':[],
        'primary_context':{'context_id':'partial','symbol':'TEST:PARTIAL','timeframe':'1','source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}})
    files['examples/partial-exits/experiment.json']=encode({'name':'Partial exits experiment','grid':{'part':[4,5]},'inputs':{},'timeout_seconds':120})
    common={'budget':2,'inputs':{},'method':'grid','objective':{'direction':'maximize','metric':'net_profit'},'seed':0,'space':{'part':[4,5]},'timeout_seconds':120}
    files['examples/partial-exits/search.json']=encode({**common,'name':'Partial exits search','windows':{'selection':{'warmup_start_ms':0,'score_start_ms':0,'end_ms':14400000},'holdout':{'warmup_start_ms':13920000,'score_start_ms':14400000,'end_ms':21600000}}})
    files['examples/partial-exits/walkforward.json']=encode({**common,'name':'Partial exits walkforward','walkforward':{'folds':2,'max_jobs':6,'mode':'rolling','start_ms':480000,'step_ms':3600000,'test_ms':3600000,'train_ms':7200000,'warmup_ms':480000}})
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        path=ROOT/name
        if args.check:
            if not path.is_file() or path.read_bytes()!=raw:raise ValueError('Partial fixture differs: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Partial-exit independent fixtures verified.');return 0
if __name__=='__main__':raise SystemExit(main())
