"""Independent phase accounting; no compiler, runtime or broker expectations."""
from decimal import Decimal,ROUND_FLOOR
from tools.order_group_oracle import text,statistics as common
KINDS={'cash':('cash','percent','1'),'equity':('percent_of_equity','cash_per_contract','0.5'),'flat':('fixed','cash_per_order','3')}

def expected(family,manual=0,cycles=60,short=False):
    if family not in KINDS or manual not in (0,1,2):raise ValueError('Unknown economic oracle input.')
    balance=Decimal(1000);position=[];equity=[];average=[];gross=entry_fees=exit_fees=Decimal(0);orders=fills=trades=closed=0
    for cycle in range(cycles):
        q=Decimal(manual) if manual else Decimal(1) if family=='cash' else (balance/500).to_integral_value(rounding=ROUND_FLOOR) if family=='equity' else Decimal(2)
        if family=='cash':
            ef=q;xf=q*(Decimal('.9') if short else Decimal('1.1'));g=10*q;net=g-ef-xf;exit_bar=2
        elif family=='equity':ef=xf=q/2;g=9*q;net=8*q;exit_bar=4
        else:ef=Decimal(3);xf=Decimal(3)*(1+(q>1));g=5+9*(q-1);net=g-ef-xf;exit_bar=4 if q>1 else 2
        for phase in range(8):
            qty=q if phase==1 else q if family=='equity' and phase in (2,3) else q-1 if family=='flat' and phase in (2,3) else Decimal(0)
            gain=net if phase>=exit_bar else (3*q-ef if phase==1 else 8*q-ef if phase==2 and family=='equity' else 11*q-ef if phase==3 and family=='equity' else (5-3-ef+(q-1)*(8 if phase==2 else 11)) if family=='flat' and phase in (2,3) else Decimal(0))
            position.append(float(qty*(-1 if short else 1)) if qty else 0.);average.append(100. if qty else None);equity.append(float(balance+gain))
        balance+=net;gross+=g;entry_fees+=ef;exit_fees+=xf;orders+=3 if family=='flat' and q>1 else 2;fills+=3 if family=='flat' and q>1 else 2;trades+=2 if family=='flat' and q>1 else 1;closed+=1
    stats=common(gross,entry_fees,exit_fees,orders=orders,fills=fills,trades=trades,closed_lots=closed,closed_positions=closed)
    stats.update(exit_states=[],economics={'quantity_mode':KINDS[family][0],'quantity_basis':'confirmed_creation_close',
        'commission':{'kind':KINDS[family][1],'value':KINDS[family][2],'schema_version':'openpine.commission-policy.1'},
        'schema_version':'openpine.commission-state.1','charged_count':orders if family=='flat' else 0,'retired_count':orders if family=='flat' else 0,'paid_order_ids':[]})
    return (tuple(position),tuple(equity),tuple(average)),stats
