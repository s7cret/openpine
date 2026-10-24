"""Versioned private-context strategies and replay demonstrations."""
import argparse
from tools.specification import ROOT,encode,read_json

def prepare():
    files={}
    def put(name,value):files['examples/context-graph/'+name]=value.encode() if isinstance(value,str) else encode(value)
    for name in ('bars.json','input.json','higher.json','contexts.json'):
        put(name,read_json(ROOT/'examples/signals'/name))
    for name in ('experiment','search','walkforward'):
        value=read_json(ROOT/f'examples/signals/{name}.json');value['name']='Context graph '+name;put(name+'.json',value)
    for v in range(1,7):
        ta='ta.' if v>=5 else '';req='request.security' if v>=5 else 'security';long='true' if v==1 else 'strategy.long'
        inp='input.int(3,title="Length",minval=1,maxval=100)' if v>=5 else 'input(3,title="Length",minval=1,maxval=100)'
        put(f'context-trend-v{v}.pine',f'''//@version={v}
strategy("Private context trend",default_qty_type=strategy.fixed,initial_capital=1000)
length={inp}
base=(high+low+close)/3
average={ta}wma(base,length)
sample(x) =>
    [x[1],x>x[1]]
remember(x) =>
    var float last=0
    if x>0
        last:=x
    last
[previous,event]={req}("TEST:SIGNAL","4",sample(average))
remembered={req}("TEST:SIGNAL","4",remember(average))
if strategy.position_size==0 and event and close>previous
    strategy.entry("L",{long})
if strategy.position_size>0
    strategy.exit("X","L",stop=strategy.position_avg_price-4*syminfo.mintick,limit=strategy.position_avg_price+6*syminfo.mintick)
if strategy.position_size>0 and not event
    strategy.close("L")
plot(average,title="Primary WMA")
plot(previous,title="Higher previous WMA")
plot(event ? 1 : 0,title="Higher rising")
plot(remembered,title="Higher remembered")
plot(strategy.equity,title="Equity")
''')
    for v in (1,6):
        decl='study' if v==1 else 'indicator';req='security' if v==1 else 'request.security'
        put(f'phase-v{v}.pine',f'''//@version={v}
{decl}("Private context phases")
base=close*2
accumulate(x) =>
    var float total=0
    total:=total+x
    total
[previous,event]={req}("TEST:ENV","1",[accumulate(base)[1],close>close[1]])
plot(previous,title="Previous cumulative")
plot(event ? 1 : 0,title="Rising")
''')
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in prepare().items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Context graph example differs: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Context graph examples verified.');return 0

if __name__=='__main__':raise SystemExit(main())
