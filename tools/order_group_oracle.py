"""Independent closed-form netting, cancel and reduce accounting; no broker imports."""
from decimal import Decimal


def text(value):
    n=Decimal(value)
    if not n:return '0'
    value=format(n,'f');return value.rstrip('0').rstrip('.') if '.' in value else value


def cycle_totals(family,part=2):
    p=Decimal(part)
    if family=='netting':return {'gross':190+20*p,'entry_fee':Decimal('8.6')+Decimal('1.2')*p,'exit_fee':Decimal('8.7')+Decimal('.8')*p,'orders':4,'fills':5,'trades':3,'closed_lots':2,'closed_positions':2}
    if family=='cancel':return {'gross':15*(p+1),'entry_fee':Decimal('1.05')*(p+1),'exit_fee':Decimal('1.2')*(p+1),'orders':3,'fills':2,'trades':1,'closed_lots':1,'closed_positions':1}
    if family=='reduce':return {'gross':150-30*p,'entry_fee':Decimal('4.5'),'exit_fee':6-Decimal('.3')*p,'orders':5,'fills':5,'trades':3,'closed_lots':2,'closed_positions':1}
    raise ValueError('Unknown order group oracle family.')


def expected(family,part=2,cycles=None):
    if family not in ('netting','cancel','reduce') or type(part) is not int or part not in (1,2):raise ValueError('Unknown grouped order oracle input.')
    length=8 if family=='reduce' else 6;cycles=cycles if cycles is not None else 50 if length==8 else 60
    totals=cycle_totals(family,part);net=totals['gross']-totals['entry_fee']-totals['exit_fee'];p=Decimal(part)
    position=[];equity=[];average=[]
    for i in range(length*cycles):
        cycle,phase=divmod(i,length);base=Decimal(1000)+cycle*net
        if family=='netting':
            qty={1:5,2:5-part,3:-3-part}.get(phase,0);price=100. if phase in (1,2) else 120. if phase==3 else None
            gain={1:Decimal('-5'),2:45-Decimal('1.1')*p,3:Decimal('85.4')-Decimal('11.1')*p,4:net,5:net}.get(phase,Decimal(0))
        elif family=='cancel':
            qty=part+1 if phase==1 else 0;price=105. if phase==1 else None
            gain=-Decimal('6.05')*(p+1) if phase==1 else net if phase>=2 else Decimal(0)
        else:
            qty={2:5,3:5-part,4:part}.get(phase,0);price=90. if qty else None
            gain={2:Decimal('20.5'),3:Decimal('45.5')+Decimal('8.9')*p,4:Decimal('139.5')-Decimal('18.7')*p,5:net,6:net,7:net}.get(phase,Decimal(0))
        position.append(float(qty));equity.append(float(base+gain));average.append(price)
    return tuple(position),tuple(equity),tuple(average)


def statistics(gross=0,entry_fee=0,exit_fee=0,*,orders=0,fills=0,trades=0,closed_lots=0,closed_positions=0):
    gross=Decimal(gross);entry_fee=Decimal(entry_fee);exit_fee=Decimal(exit_fee);fees=entry_fee+exit_fee;cash=1000+gross-fees
    return {'cash':text(cash),'equity':text(cash),'position_size':'0','position_avg_price':None,'gross_realized':text(gross),'unrealized':'0',
        'commission':text(fees),'orders':orders,'fills':fills,'trades':trades,'cash_exact':text(cash),'equity_exact':text(cash),'gross_realized_exact':text(gross),
        'unrealized_exact':'0','allocated_entry_commission':text(entry_fee),'entry_commission':text(entry_fee),'exit_commission':text(exit_fee),
        'closed_positions':closed_positions,'protections':[],'entries':[],'lots':[],'open_lots':0,'closed_lots':closed_lots,'order_groups':[]}


def waiting(entry_id,qty,limit=None,stop=None):
    return {'entry_id':entry_id,'direction':1,'quantity':str(qty),'limit':str(limit) if limit is not None else None,'stop':str(stop) if stop is not None else None,
        'order_kind':'stop_limit' if limit is not None and stop is not None else 'limit' if limit is not None else 'stop' if stop is not None else 'market',
        'status':'pending','created_bar':0,'activated_bar':None,'activation_basis':None}
