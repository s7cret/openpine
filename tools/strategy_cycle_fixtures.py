"""Original programs and handwritten known-price phase expectations."""
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.strategy_cycle import PROFILE,FLAGS
from packages.contracts.wire import canonical

FAMILIES=('protection','market','counter','arrays','next-price')

def options(family):return dict(zip(FLAGS,(False,True,family!='next-price')))

def source(v,family):
    flags=(',calc_on_order_fills=true' if v>=2 else '')+(',process_orders_on_close='+str(family!='next-price').lower() if v>=4 else '')
    prefix=f'//@version={v}\nstrategy("Cycle {family}",default_qty_type=strategy.fixed,initial_capital=1000{flags})\n'
    if family=='counter':prefix+=('varip' if v>=4 else 'var')+' n=0\nn:=n+1\n'
    if family=='arrays':
        if v<4:raise ValueError('Array varip requires v4 or later.')
        prefix+='varip a=array.new_float(0)\nb=a\narray.push(b,close)\n'
    body='if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n'
    body+='    strategy.close("L")\n' if family=='market' else '    strategy.exit("X","L",limit=110)\n'
    body+='plot(strategy.equity)\nplot(strategy.position_size)\n'
    if family=='counter':body+='plot(n)\n'
    if family=='arrays':body+='plot(array.size(a))\n'
    return prefix+body.replace('LONG','true' if v==1 else 'strategy.long')

def events():
    result=[MarketEvent(0,'primary','close',0,60000,0,100.,100.,100.,100.,1.)]
    for i,p in enumerate((100.,94.,112.,101.)):
        result.append(MarketEvent(i+1,'primary',('open','update','update','close')[i],60000,120000 if i==3 else 60000+i*10000,i,100.,120.,80.,p,float(i+10)))
    return result

def expected(v,family):
    market=family=='market';equity='1000' if market else '1012'
    outputs=[[1000.,float(equity)],[0. if family=='next-price' else 1.,0.]]
    if family=='counter':outputs.append([2,4] if v>=4 else [1,2])
    if family=='arrays':outputs.append([2,4])
    return {'prices':['100','100' if market else '112'],'quantities':['1','1'],'fees':['0','0'],
        'equity':equity,'position_size':'0','passes':4,'observations':5,'confirmed_bars':2,'outputs':outputs}

def context(family):return {'contexts':[asdict(MarketContext('primary','TEST:OBSERVED','1','sampled.1'))],
    'primary_id':'primary','broker_settings':None,'calculation_profile':PROFILE,'cycle_settings':options(family)}

def files():
    result={'events.ndjson':b''.join(canonical(e.payload())+b'\n' for e in events())};rows=[]
    for family in FAMILIES:
        result[f'contexts-{family}.json']=canonical(context(family))+b'\n'
        for v in range(1,7):
            if family=='arrays' and v<4:continue
            result[f'v{v}-{family}.pine']=source(v,family).encode();rows.append({'version':v,'family':family,**expected(v,family)})
    result['expected.json']=canonical({'qualification':'original explicit phase arithmetic; no external equivalence','programs':rows})+b'\n'
    return result
