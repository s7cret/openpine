"""Independent cycle arithmetic; imports no compiler, runtime or broker."""
from decimal import Decimal
from tools.order_group_oracle import statistics,text
FAMILIES=('partial','cooldown','duration')
TITLES=('Equity','Net','Open','Closed','Active','Wins','Losses','Win count','Loss count')
def expected(family,units=2,cycles=20,version=6):
 if family not in FAMILIES or units not in (1,2,3) or type(cycles) is not int or cycles<1:raise ValueError('Unknown performance oracle input.')
 q=Decimal(units);net=positive=negative=Decimal(0);count=wins=losses=opened=orders=fills=0;gross=entryfee=exitfee=Decimal(0);last=None;last_exit=None
 values={k:[] for k in TITLES}
 if version>=5:values.update({'Last trade':[],'Last exit':[]})
 for cycle in range(cycles):
  active=family!='cooldown' or cycle%2==0
  for phase in range(8):
   index=cycle*8+phase;remaining=Decimal(0);openprofit=Decimal(0)
   if active:
    if phase==1:opened+=1;orders+=1;fills+=1;entryfee+=1
    if family=='partial':
     if phase==1:remaining=q;openprofit=Decimal(-1)
     elif phase==2:remaining=q/2;openprofit=5*q-Decimal('.5')
     if phase in (2,3):
      profit=5*q-Decimal('1.5');net+=profit;positive+=profit;wins+=1;count+=1;orders+=1;fills+=1;gross+=5*q;exitfee+=1;last=profit;last_exit=index
    elif family=='cooldown':
     if phase==1:remaining=q;openprofit=Decimal(-1)
     if phase==2:
      profit=-10*q-2;net+=profit;negative-=profit;losses+=1;count+=1;orders+=1;fills+=1;gross-=10*q;exitfee+=1;last=profit;last_exit=index
    else:
     if phase in (1,2,3):remaining=q;openprofit=Decimal(-1) if phase==1 else 10*q-1
     if phase==4:
      profit=5*q-2;net+=profit;positive+=profit;wins+=1;count+=1;orders+=1;fills+=1;gross+=5*q;exitfee+=1;last=profit;last_exit=index
   row=(float(1000+net+openprofit),float(net),float(openprofit),count,int(remaining>0),float(positive),float(negative),wins,losses)
   for name,value in zip(TITLES,row,strict=True):values[name].append(value)
   if version>=5:values['Last trade'].append(None if last is None else float(last));values['Last exit'].append(last_exit)
 stats=statistics(gross,entryfee,exitfee,orders=orders,fills=fills,trades=count,closed_lots=opened,closed_positions=opened)
 stats.update(exit_states=[],economics={'quantity_mode':'fixed','quantity_basis':'confirmed_creation_close','commission':{'kind':'cash_per_order','value':'1','schema_version':'openpine.commission-policy.1'},'schema_version':'openpine.commission-state.1','charged_count':orders,'retired_count':orders,'paid_order_ids':[]},
  performance={'schema_version':'openpine.performance-state.1','archive':{'count':count},'wins':wins,'losses':losses,'evens':0,'grossprofit':text(positive),'grossloss':text(negative),'netprofit':text(net),'closed_commission':text(entryfee+exitfee),
   'observations':{'netprofit':float(net),'openprofit':0.,'grossprofit':float(positive),'grossloss':float(negative),'wintrades':wins,'losstrades':losses,'opentrades':0,'closedtrades':count}})
 return values,stats
