"""Closed-form phase expectations, independent of compiler, broker and matcher."""
from decimal import Decimal

def text(n):return format(Decimal(n).normalize(),'f') if n else '0'

def cycle_totals(family,distance=3,short=False):
    d=Decimal(distance)
    price=100+d+2 if family=='relative' else Decimal(108) if family=='points' and distance==2 else Decimal(109) if family=='points' else 110-d
    actual=200-price if short else price
    return {'gross':2*(price-100),'entry_fee':Decimal(2),'exit_fee':actual*Decimal('.02'),'orders':2,'fills':2,'trades':1,'closed_lots':1,'closed_positions':1,'exit_price':actual}

def expected(family,distance=3,cycles=60,short=False):
    t=cycle_totals(family,distance,short);net=t['gross']-t['entry_fee']-t['exit_fee'];position=[];equity=[];average=[]
    closes={'relative':(100,102,105,100,100,100,100,100),'points':(100,103,108,111,109,100,100,100),'price':(100,103,108,100,100,100,100,100)}[family]
    exit_bar=2 if family in ('relative','price') else 3 if distance==2 else 4
    for i in range(cycles*8):
        phase=i%8;base=1000+(i//8)*net;open_=1<=phase<exit_bar;q=2*(-1 if short else 1) if open_ else 0
        position.append(float(q));average.append(100. if open_ else None)
        gain=2*(closes[phase]-100)-2 if open_ else net if phase>=exit_bar else Decimal(0)
        equity.append(float(base+gain))
    return tuple(position),tuple(equity),tuple(average)

def statistics(gross=0,entry_fee=0,exit_fee=0,**kw):
    # Only common accounting field construction is shared with the earlier pure
    # oracle; all new trigger prices and phase values above are independent.
    from tools.order_group_oracle import statistics as common
    return {**common(gross,entry_fee,exit_fee,**kw),'exit_states':[]}
