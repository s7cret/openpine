"""Small original strategies and explicit arithmetic oracles for sampled prices."""
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.observed_broker import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical

FAMILIES=('limit','stop-limit','partial','trailing','oca','risk')
EXPECTED={
 'limit':{'prices':['94','112'],'quantities':['2','2'],'fees':['1.88','2.24'],'equity':'1031.88','position_size':'0'},
 'stop-limit':{'prices':['103','118'],'quantities':['1','1'],'fees':['0','0'],'equity':'1015','position_size':'0'},
 'partial':{'prices':['100','112','88'],'quantities':['2','1','1'],'fees':['0','0','0'],'equity':'1000','position_size':'0'},
 'trailing':{'prices':['100','105'],'quantities':['1','1'],'fees':['0','0'],'equity':'1005','position_size':'0'},
 'oca':{'prices':['94','112'],'quantities':['1','1'],'fees':['0','0'],'equity':'1018','position_size':'0'},
 'risk':{'prices':['100','80'],'quantities':['2','2'],'fees':['0','0'],'equity':'960','position_size':'0'}}

def source(v,family):
    direction='true' if v==1 else 'strategy.long'
    commands={
      'limit':['strategy.entry("L",LONG,limit=95)','strategy.exit("X","L",stop=90,limit=110)'],
      'stop-limit':['strategy.entry("L",LONG,stop=105,limit=104)','strategy.exit("X","L",limit=116)'],
      'partial':['strategy.entry("L",LONG)','strategy.exit("A","L",limit=110,qty=1)','strategy.exit("B","L",stop=90,qty=1)'],
      'trailing':['strategy.entry("L",LONG)','strategy.exit("X","L",trail_points=5,trail_offset=3)'],
      'oca':['strategy.entry("L",LONG,limit=95,oca_name="choice",oca_type=strategy.oca.cancel)',
             'strategy.entry("H",LONG,stop=105,oca_name="choice",oca_type=strategy.oca.cancel)','strategy.exit("X","L",limit=110)'],
      'risk':['strategy.entry("L",LONG)']}[family]
    qty=2 if family in ('limit','partial','risk') else 1;fee=1 if family=='limit' else 0
    extra='strategy.risk.max_drawdown(30,strategy.cash)\n' if family=='risk' else ''
    return f'//@version={v}\nstrategy("Observed {family}",default_qty_type=strategy.fixed,default_qty_value={qty},initial_capital=1000,commission_value={fee})\n{extra}if bar_index==0\n'+''.join('    '+c.replace('LONG',direction)+'\n' for c in commands)+'plot(strategy.equity)\nplot(strategy.position_size)\n'

def events(family):
    prices={'limit':(100,94,112,101),'stop-limit':(100,106,103,118,101),'partial':(100,112,88,100),
        'trailing':(100,106,110,105,101),'oca':(100,94,112,101),'risk':(100,80,101)}[family]
    result=[MarketEvent(0,'primary','close',0,60000,0,100.,100.,100.,100.,1.)]
    for i,raw in enumerate(prices):
        kind='open' if i==0 else 'close' if i==len(prices)-1 else 'update'
        at=120000 if kind=='close' else 60000+i*10000
        result.append(MarketEvent(i+1,'primary',kind,60000,at,i,100.,120.,80.,float(raw),float(10+i)))
    return result

def files():
    context={'contexts':[asdict(MarketContext('primary','TEST:OBSERVED','1','sampled.1'))],'primary_id':'primary',
        'broker_settings':asdict(BrokerSettings(tick_size='1')),'broker_profile':PROFILE}
    result={'contexts.json':canonical(context)+b'\n','expected.json':canonical({'qualification':'original explicit sampled-price arithmetic, not external execution','families':EXPECTED})+b'\n'}
    for family in FAMILIES:
        result[family+'.ndjson']=b''.join(canonical(e.payload())+b'\n' for e in events(family))
        for v in range(1,7):result[f'v{v}-{family}.pine']=source(v,family).encode()
    return result
