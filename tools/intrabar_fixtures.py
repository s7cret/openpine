"""Original sampled-update programs with explicit arithmetic expectations."""
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.intrabar import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical

FAMILIES=('commands','counter','arrays','protection','cancel')

def source(v,family):
    if family in ('counter','arrays'):
        prefix=f'//@version={v}\n'+('study' if v<5 else 'indicator')+f'("Intrabar {family}")\n'
        if family=='counter':return prefix+('varip' if v>=4 else 'var')+' n=0\nn:=n+1\nplot(n)\n'
        if v<4:raise ValueError('Array varip requires v4 or later.')
        return prefix+'varip a=array.new_float(0)\narray.push(a,close)\nplot(array.size(a))\n'
    prefix=f'//@version={v}\nstrategy("Intrabar {family}",default_qty_type=strategy.fixed,initial_capital=1000'+(',calc_on_every_tick=true' if v>=2 else '')+')\n'
    bodies={
        'commands':'if close<95\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.close("L")\n',
        'protection':'if bar_index==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.exit("X","L",limit=110)\n',
        'cancel':'if close==100\n    strategy.entry("L",LONG,limit=95)\nif close==99\n    strategy.cancel("L")\nif close==94\n    strategy.entry("L",LONG,limit=93)\n'}
    return prefix+bodies[family].replace('LONG','true' if v==1 else 'strategy.long')+'plot(strategy.equity)\nplot(strategy.position_size)\n'

def events(family):
    prices=(99.,94.,92.,101.) if family=='cancel' else (100.,94.,112.,101.)
    result=[MarketEvent(0,'primary','close',0,60000,0,100.,100.,100.,100.,1.)]
    for i,p in enumerate(prices):
        kind='open' if i==0 else 'close' if i==3 else 'update'
        result.append(MarketEvent(i+1,'primary',kind,60000,120000 if i==3 else 60000+i*10000,i,100.,120.,80.,p,float(i+10)))
    return result

def expected(v,family):
    if family in ('counter','arrays'):return {'outputs':[[1,5 if v>=4 else 2]],'passes':5,'confirmed_bars':2}
    prices,equity,position={'commands':(['112','101'],'989','0'),'protection':(['100','112'],'1012','0'),'cancel':(['92'],'1009','1')}[family]
    return {'prices':prices,'quantities':['1']*len(prices),'fees':['0']*len(prices),'equity':equity,'position_size':position,'passes':5,'confirmed_bars':2}

def files():
    context={'contexts':[asdict(MarketContext('primary','TEST:OBSERVED','1','sampled.1'))],'primary_id':'primary','broker_settings':None,'calculation_profile':PROFILE}
    result={'contexts.json':canonical(context)+b'\n'};expectations=[]
    for family in FAMILIES:
        result[family+'.ndjson']=b''.join(canonical(e.payload())+b'\n' for e in events(family))
        for v in range(1,7):
            if family=='arrays' and v<4:continue
            result[f'v{v}-{family}.pine']=source(v,family).encode()
            expectations.append({'version':v,'family':family,**expected(v,family)})
    result['expected.json']=canonical({'qualification':'original sampled-price arithmetic; no external execution oracle','programs':expectations})+b'\n'
    return result
