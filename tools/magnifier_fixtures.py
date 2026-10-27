"""Two manual paths share one parent OHLC but close a bracket on opposite sides."""
from dataclasses import asdict
from packages.contracts import HistoricalBar,HistoricalData
from packages.contracts.magnifier import PROFILE
from packages.contracts.wire import canonical

def bar(t,o,h,l,c):return HistoricalBar(t,float(c),float(o),float(h),float(l),1.)
def data(reverse=False):
    a=HistoricalData('primary','TEST:MAGNIFIER','2',(bar(0,100,100,100,100),bar(120000,100,120,80,100)),schema_version='openpine.historical-bars.2')
    from dataclasses import replace
    a=replace(a,bars=tuple(replace(b,volume=2.) for b in a.bars))
    tail=(bar(120000,100,100,80,90),bar(180000,90,120,90,100)) if reverse else (bar(120000,100,120,100,110),bar(180000,110,110,80,100))
    b=HistoricalData('lower','TEST:MAGNIFIER','1',(bar(0,100,100,100,100),bar(60000,100,100,100,100),*tail),schema_version='openpine.historical-bars.2')
    return a,b
def configuration(snapshot_id='0'*64):return {'schema_version':PROFILE,'snapshot_id':snapshot_id,'missing':'reject','cycle_settings':{'calc_on_order_fills':True},'checkpoint_every_children':1}
def source(v):
    flag=(',calc_on_order_fills=true' if v>=2 else '')+(',use_bar_magnifier=true' if v>=5 else '')
    direction='true' if v==1 else 'strategy.long'
    return f'//@version={v}\nstrategy("Lower candle bracket",default_qty_type=strategy.fixed,initial_capital=1000{flag})\nif bar_index==0 and strategy.position_size==0\n    strategy.entry("L",{direction})\nif strategy.position_size>0\n    strategy.exit("X","L",stop=90,limit=110)\nplot(strategy.equity,title="Equity")\n'
def files():
    a,b=data();_,other=data(True)
    rows={'parent.json':asdict(a),'lower-high-first.json':asdict(b),'lower-low-first.json':asdict(other),'configuration.json':configuration(),
        'expected.json':{'qualification':'local_four_sample_model','high_first':{'prices':['100','120'],'equity':'1020'},'low_first':{'prices':['100','80'],'equity':'980'},'parent_commits':2,'model_points':16,'normal_passes':2,'after_fill_passes':2}}
    return {**{k:canonical(v)+b'\n' for k,v in rows.items()},**{f'v{v}.pine':source(v).encode() for v in range(1,7)}}
