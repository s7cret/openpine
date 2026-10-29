"""Independent finite strategy arithmetic and sampled-event corpus fixtures."""
from packages.contracts.wire import canonical
from tools.specification import ROOT

def sources():
    files={}
    for v in range(1,7):
        decl='study' if v<5 else 'indicator';api='security' if v<5 else 'request.security'
        files[f'scope-v{v}.pine']=f'//@version={v}\n{decl}("Mutable scope")\nvar n=bar_index\nplot({api}("TEST:MTF","60",n))\n'.encode()
        header=f'//@version={v}\nstrategy("Active lifecycle",initial_capital=1000,default_qty_type=strategy.fixed,default_qty_value=2,commission_type=strategy.commission.percent,commission_value=1)\n'
        direction='true' if v==1 else 'strategy.long'
        entry=f'if bar_index==0\n    strategy.entry("L",{direction}'
        for family,body in {
            'roundtrip':entry+')\nif bar_index==3\n    strategy.close("L")\n',
            'pending':entry+',limit=9)\nif bar_index==5\n    strategy.close("L")\n',
            'partial':entry+')\nif bar_index==2\n    strategy.close("L",qty=1)\nif bar_index==5\n    strategy.close("L")\n',
        }.items():files[f'{family}-v{v}.pine']=(header+body+'plot(strategy.equity)\n').encode()
        # Ordinary variables roll back on previews; varip explicitly available v4+.
        mode='varip' if v>=4 else 'var'
        files[f'intrabar-v{v}.pine']=(header+f'{mode} n=0\nn:=n+1\nplot(n)\n').encode()
    primary={'schema_version':'openpine.historical-bars.2','context_id':'lower.primary','symbol':'TEST:LOWER','timeframe':'2',
        'bars':[dict(time=i*120000,open=10.,high=11.,low=9.,close=10.,volume=2.) for i in range(4)]}
    lower={**primary,'context_id':'lower.child','timeframe':'1','bars':[dict(time=i*60000,open=10.,high=11.,low=9.,close=10.,volume=1.) for i in range(8)]}
    def context(d):return {k:d[k] for k in ('context_id','symbol','timeframe')}|{'source':'uploaded-file.1','session':'24x7','timezone':'UTC','time_rule':'fixed-duration-utc.1'}
    files.update({'lower/bars.json':canonical(primary)+b'\n','lower/higher.json':canonical(lower)+b'\n',
        'lower/contexts.json':canonical({'primary_context':context(primary),'contexts':[{'context':context(lower),'bars':'higher.json'}]})+b'\n'})
    for v in (5,6):files[f'lower-v{v}.pine']=f'//@version={v}\nindicator("Lower counter")\nx=request.security_lower_tf("TEST:LOWER","1",bar_index,calc_bars_count=8)\nplot(array.sum(x))\nplot(array.size(x))\n'.encode()
    return files

def scenarios(record):
    cases=[]
    def add(family,v,outputs,execution,reason):
        files={'source':record(f'examples/corpus/{family}-v{v}.pine')}
        if outputs is not None:files['bars']=record('examples/strategies/wave.json')
        cases.append({'id':f'{family}-v{v}','version':v,'family':family,'files':files,'inputs':{},
            'expected':{'status':'compiled'} if outputs is not None else {'status':'rejected','category':'PROFILE_UNCOVERED','code':'REQUEST_SCOPE'},
            'oracle':{'reason':reason,'outputs':outputs} if outputs is not None else None,'execution':execution,
            'history_replay':{'equivalent':family=='lower','reason':'Matching confirmed lower windows.' if family=='lower' else 'Historical next-open and observed-event broker eligibility differ; each route and restored native state are checked separately.'}})
    from packages.contracts.intrabar import PROFILE
    from packages.contracts.observed_broker import PROFILE as BROKER
    for v in range(1,7):
        add('scope',v,None,{},'Mutable captures remain excluded.')
        add('roundtrip',v,[[1000.,1003.8,1007.8,999.8,999.6,999.6,999.6,999.6]],
            {'pause_after':2,'checkpoint':'position','statistics':{'equity':'999.6','commission':'0.4'}},'2 at10, exit2 at10; one-percent fee .2 each side; independent cash plus position*close.')
        add('pending',v,[[1000.,1000.,1000.,1000.,997.82,1005.82,1005.58,1005.58]],
            {'pause_after':4,'checkpoint':'pending','replay_options':{'broker_profile':BROKER},'statistics':{'equity':'1005.58','commission':'0.42'}},'Limit2 at9 fills bar4, close2 at12 bar6; fees .18+.24; cash and mark-to-market.')
        add('partial',v,[[1000.,1003.8,1007.8,1003.66,1001.66,1005.66,1005.54,1005.54]],
            {'pause_after':4,'checkpoint':'partial','replay_options':{'broker_profile':BROKER},'statistics':{'equity':'1005.54','commission':'0.46'}},'Buy2 at10 fee.2; sell1 at14 fee.14, sell1 at12 fee.12; partial lot remains at checkpoint.')
        add('intrabar',v,[[i+1 for i in range(8)]],{'tape':'ohlc3','split_after':5,
            'replay_options':{'calculation_profile':PROFILE},'replay_outputs':[[3*(i+1) if v>=4 else i+1 for i in range(8)]]},
            'One historical pass/bar; three sampled passes/bar; varip escapes rollback only v4-v6, ordinary var v1-v3 rolls back.')
    for v in (5,6):
        add('lower',v,[[1,5,9,13],[2,2,2,2]],{'split_after':4,'replay_options':{'lower_tf':{
            'schema_version':'openpine.lower-events.1','late':'reject','warmup':{'lower.child':{'count':8,'end_ms':480000}}}}},
            'Four parent windows contain counter pairs (0,1), (2,3), (4,5), (6,7); sums and lengths independently counted.')
        cases[-1]['files'].update({key:record('examples/corpus/lower/'+name) for key,name in [('bars','bars.json'),('higher','higher.json'),('context_config','contexts.json')]})
    return cases

def write():
    for name,raw in sources().items():
        path=ROOT/'examples/corpus'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
