"""Hand-derived five-bar cycles; independent of compiler, broker and fixtures."""
from decimal import Decimal

def expected(family,part=8,count=400):
    q=Decimal(part);position=[];equity=[];prices=[]
    net={'limit':Decimal('19.8')*q-Decimal('68.5'),'stop':Decimal('19.8')*q-120,'stop-limit':Decimal('14.85')*q-Decimal('99.8')}[family]
    for i in range(count):
        cycle,phase=divmod(i,5);value=Decimal(1000)+net*cycle;qty=0;price=None
        if family in ('limit','stop'):
            if phase==1:
                qty=10;price=95 if family=='limit' else 105;value+=Decimal('40.5') if family=='limit' else Decimal('-.5')
            elif phase==2:
                qty=10-part;price=95 if family=='limit' else 105
                value+=(Decimal('140.5')-Decimal('1.1')*q) if family=='limit' else (Decimal('89.5')-Decimal('1.15')*q)
            elif phase>=3:value+=net
        else:
            if phase==2:qty=10;price=103;value-=Decimal('.3')
            elif phase==3:qty=10-part;price=103;value+=Decimal('59.7')-Decimal('1.1')*q
            elif phase==4:value+=net
        position.append(float(qty));equity.append(float(value));prices.append(float(price) if price is not None else None)
    return position,equity,prices
