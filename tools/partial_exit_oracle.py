"""Closed-form four-bar-cycle quantities and account values, independent of broker code."""
from decimal import Decimal

def expected(part=5,count=360):
    net=Decimal('14.85')*part-Decimal('69.5');position=[];equity=[]
    for i in range(count):
        cycle,phase=divmod(i,4)
        position.append(float(10-part) if phase==1 else 0.)
        value=Decimal(1000)+net*cycle
        if phase==1:value+=Decimal(90)-Decimal('1.1')*part
        elif phase>=2:value+=net
        equity.append(float(value))
    return position,equity
