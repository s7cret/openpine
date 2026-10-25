"""Independent phase arithmetic for risk stops; no compiler/engine/broker imports."""
from decimal import Decimal
from tools.order_group_oracle import text,statistics
FAMILIES=('entry','daily','lifetime')
DAY=86400000
def expected(family,manual=0,cycles=20):
 if family not in FAMILIES or manual not in (0,1) or cycles<1:raise ValueError('Unknown risk oracle input.')
 balance=Decimal(1000);position=[];equity=[];average=[];gross=entry_fee=exit_fee=Decimal(0)
 orders=fills=trades=closed=0;halt=None;trigger_count=0
 for day in range(cycles):
  if family=='entry':
   q=Decimal(1 if manual else 3);g=15*q+40;ef=Decimal(2);xf=Decimal(3);net=g-5
   qs={1:q,3:Decimal(-4),4:q};prices={1:100.,3:100.,4:90.}
   gains={1:Decimal(-1),2:10*q-2,3:10*q-3,4:10*q+36,5:net,6:net,7:net}
   no,nf,nt=5,6,3
  elif family=='daily':
   q=Decimal(1 if manual else 2);g=Decimal(-12) if manual else Decimal(-24);ef=xf=Decimal(3 if manual else 1);net=g-ef-xf
   qs={1:q,4:Decimal(1)} if manual else {1:q};prices={k:100. for k in qs}
   gains={1:-10*q-1,2:Decimal(-14) if manual else net,3:Decimal(-14) if manual else net,
    4:Decimal(-15) if manual else net,5:Decimal(-16) if manual else net,6:net,7:net}
   no=nf=6 if manual else 2;nt=3 if manual else 1;trigger_count+=1
   phase=6 if manual else 1
   halt={'rule':'max_intraday_filled_orders' if manual else 'max_intraday_loss','period':day*DAY,'bar_index':day*8+phase,'time':day*DAY+phase*10800000,'basis':'fill' if manual else 'close',
    'equity':text(balance-17 if manual else balance-21),'reference':'0' if manual else text(balance),'observed':'5' if manual else '21','threshold':'5' if manual else '20','permanent':False,'alert_message':''}
  elif day<2:
   q=Decimal(1 if manual else 3);g=-12*q;ef=xf=Decimal(1);net=g-2;no=nf=2;nt=1
   qs={1:q};prices={1:100.};gains={1:-10*q-1,**{k:net for k in range(2,8)}}
   if not manual and day==1:
    halt={'rule':'max_drawdown','period':DAY,'bar_index':9,'time':DAY+10800000,'basis':'close','equity':'931','reference':'1000','observed':'69','threshold':'50','permanent':True,'alert_message':''};trigger_count=1
  else:
   q=g=ef=xf=net=Decimal(0);no=nf=nt=0;qs={};prices={};gains={}
   if manual and day==2:
    halt={'rule':'max_cons_loss_days','period':2*DAY,'bar_index':16,'time':2*DAY,'basis':'open','equity':'972','reference':'0','observed':'2','threshold':'2','permanent':True,'alert_message':''};trigger_count=1
  for phase in range(8):position.append(float(qs.get(phase,0)));equity.append(float(balance+gains.get(phase,0)));average.append(prices.get(phase))
  balance+=net;gross+=g;entry_fee+=ef;exit_fee+=xf;orders+=no;fills+=nf;trades+=nt;closed+=nt
 stats=statistics(gross,entry_fee,exit_fee,orders=orders,fills=fills,trades=trades,closed_lots=closed,closed_positions=closed)
 period_start=Decimal(1000)+(cycles-1)*(Decimal(50 if manual else 80) if family=='entry' else Decimal(-18 if manual else -26)) if family!='lifetime' else Decimal(1000)+min(2,cycles-1)*Decimal(-14 if manual else -38)
 peak=balance+1 if family=='entry' else Decimal(1000);period_peak=peak if family=='entry' else period_start
 loss_days=0 if family=='entry' else cycles-1 if family=='daily' else min(cycles-1,2) if cycles<=3 else 0
 current_orders=5 if family=='entry' else 6 if manual else 2
 if family=='lifetime':current_orders=2 if cycles<=2 else 0
 stats.update(exit_states=[],economics={'quantity_mode':'percent_of_equity' if family=='entry' else 'cash' if family=='lifetime' else 'fixed','quantity_basis':'confirmed_creation_close',
  'commission':{'kind':'cash_per_order','value':'1','schema_version':'openpine.commission-policy.1'},'schema_version':'openpine.commission-state.1','charged_count':orders,'retired_count':orders,'paid_order_ids':[]},
  risk={'schema_version':'openpine.risk-state.1','period':(cycles-1)*DAY,'period_start_net':text(period_start-1000),'peak':text(peak),'period_peak':text(period_peak),
   'loss_days':loss_days,'filled_orders':current_orders,'retired_orders':current_orders,'seen_orders':[],'observed_fills':fills,'halt':halt,'needs_close':False,'trigger_count':trigger_count,'blocked':halt is not None,'period_kind':'utc_day'})
 return (tuple(position),tuple(equity),tuple(average)),stats
