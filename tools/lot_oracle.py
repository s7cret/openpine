"""Closed-form independent six/eight-bar accounting for the lot examples."""
from decimal import Decimal
from fractions import Fraction

def expected(family,part=2,cycles=None):
    if family not in ('fifo','any','conditional') or type(part) is not int or part not in (1,2):raise ValueError('Unknown lot oracle input.')
    length=8 if family=='conditional' else 6;cycles=cycles if cycles is not None else 50 if length==8 else 60
    q=part;position=[];equity=[];average=[]
    net=Decimal('80.11')-Decimal('9.9')*q if family=='conditional' else Decimal('9.2')+Decimal('9.9')*q
    for i in range(length*cycles):
        cycle,phase=divmod(i,length);base=Decimal(1000)+cycle*net
        if family=='conditional':
            qty={1:2,2:2,3:5,4:5-q,5:3-q}.get(phase,0)
            price={1:95.,2:95.,3:99.8,4:float(Fraction(499-103*q,5-q)),5:103.}.get(phase)
            increment={1:Decimal('8.1'),2:Decimal('16.1'),3:Decimal('16.01'),4:Decimal('46.01')-Decimal('1.1')*q,
                5:Decimal('68.71')-Decimal('6.1')*q,6:net,7:net}.get(phase,Decimal(0))
        else:
            qty={1:2,2:5,3:5-q}.get(phase,0)
            price={1:100.,2:106.,3:float(Fraction(530-(100 if family=='fifo' else 110)*q,5-q))}.get(phase)
            increment={1:Decimal('-2'),2:Decimal('14.7'),3:Decimal('64.7')-Decimal('1.2')*q,4:net,5:net}.get(phase,Decimal(0))
        position.append(float(qty));equity.append(float(base+increment));average.append(price)
    return tuple(position),tuple(equity),tuple(average)

def cycle_totals(family,part=2):
    if family=='conditional':return {'gross':Decimal(91)-10*part,'entry_fee':Decimal('4.99'),'exit_fee':Decimal('5.9')-Decimal('.1')*part,'orders':7,'fills':5,'trades':3,'closed_lots':2,'closed_positions':1}
    n=4 if family=='fifo' and part==2 else 5
    return {'gross':Decimal(20)+10*part,'entry_fee':Decimal('5.3'),'exit_fee':Decimal('5.5')+Decimal('.1')*part,'orders':n,'fills':n,'trades':2 if n==4 else 3,'closed_lots':2,'closed_positions':1}
