"""Risk state observes the existing ledger; it never synthesizes an OHLC tick path."""
from dataclasses import replace
from decimal import Decimal
from packages.contracts.calendar import floor_open
from packages.contracts.lots import LotTarget,LotCloseOrder
from packages.contracts.partial_exits import PartialCloseIntent
from packages.contracts.risk import KINDS

PRIORITY=('max_drawdown','max_cons_loss_days','max_intraday_loss','max_intraday_filled_orders')

class RiskController:
 def __init__(self,broker):
  self.broker=broker;self.enabled=True;self.period=None;self.period_start_net=Decimal(0)
  self.peak=Decimal(broker.policy.initial_capital);self.period_peak=self.peak
  self.loss_days=0;self.filled_orders=0;self.retired_orders=0;self.seen_orders=set();self.observed_fills=0
  self.halt=None;self.needs_close=False;self.trigger_count=0;self.busy=False;self.last_price=None
 @property
 def blocked(self):return self.halt is not None and (self.halt['permanent'] or self.halt['period']==self.period)
 def period_key(self,time):return floor_open(time,self.broker.policy.timeframe if self.broker.policy.timeframe in ('W','M') else 'D')
 def equity(self,price):
  b=self.broker;p=b.ledger.position();q=Decimal(p['quantity'])*p['direction'] if p else Decimal(0)
  return b.cash+q*Decimal(price)
 def start(self,index,time,price):
  if not self.enabled:return
  b=self.broker;period=self.period_key(time);net=b.realized-b.fees
  if period!=self.period:
   if self.period is not None:
    profit=net-self.period_start_net;self.loss_days=self.loss_days+1 if profit<0 else 0
    b.event('risk_period',{'action':'closed','period':self.period,'net_profit':b.text(profit),'consecutive_loss_days':self.loss_days,'filled_orders':self.filled_orders},index,time)
   self.period=period;self.period_start_net=net;self.period_peak=self.equity(price)
   self.filled_orders=self.retired_orders=0;self.seen_orders.clear()
   b.event('risk_period',{'action':'opened','period':period,'kind':'chart_bar' if b.policy.timeframe in ('W','M') else 'utc_day'},index,time)
  # A deferred liquidation survives a new period before fresh orders are admitted.
  self.drain(price,index,time,basis='open')
  self.observe(price,'open',index,time);self.drain(price,index,time,basis='open')
 def record_fill(self,order):
  if not self.enabled or self.period is None:raise ValueError('A risk broker cannot fill during warmup.')
  self.observed_fills+=1
  if order.order_id not in self.seen_orders:self.seen_orders.add(order.order_id);self.filled_orders+=1
 def observe(self,price,basis,index,time):
  if not self.enabled:return
  b=self.broker;price=Decimal(price);self.last_price=price;equity=self.equity(price)
  self.peak=max(self.peak,equity);self.period_peak=max(self.period_peak,equity)
  if self.halt is not None and self.halt['permanent']:return
  for kind in PRIORITY:
   rule=getattr(b.policy.limits,kind)
   if rule is None:continue
   permanent=kind in ('max_drawdown','max_cons_loss_days')
   if self.blocked and not permanent:continue
   reference=self.peak if kind=='max_drawdown' else self.period_peak if kind=='max_intraday_loss' else Decimal(0)
   observed=reference-equity if kind in ('max_drawdown','max_intraday_loss') else Decimal(self.loss_days if kind=='max_cons_loss_days' else self.filled_orders)
   threshold=Decimal(rule.value)*reference/100 if rule.unit=='percent_of_equity' else Decimal(rule.value)
   insolvent=rule.unit=='percent_of_equity' and equity<=0
   if observed<threshold and not insolvent:continue
   self.halt={'rule':kind,'call_site_id':rule.call_site_id,'period':self.period,'bar_index':index,'time':time,'basis':basis,
    'equity':b.text(equity),'reference':b.text(reference),'observed':b.text(observed),'threshold':b.text(threshold),
    'permanent':bool(permanent or insolvent),'alert_message':rule.alert_message}
   self.trigger_count+=1;self.needs_close=b.position is not None
   b.event('risk_triggered',dict(self.halt),index,time);return
 def cancel(self,index,time):
  b=self.broker
  for name in list(b.entries):b.cancel_entry(name,index,time,'risk_halt')
  for name in list(b.protections):b.cancel_exit(name,index,time,'risk_halt')
  for order in b.pending:b.event('cancelled',{'order_id':order.order_id,'reason':'risk_halt'},index,time)
  b.pending=[]
 def drain(self,price,index,time,*,basis,defer=False):
  if not self.enabled or self.busy:return
  b=self.broker
  if self.blocked or self.needs_close:self.cancel(index,time)
  if not self.needs_close or defer:return
  if b.position is None:self.needs_close=False;return
  self.busy=True
  try:
   targets=tuple(LotTarget('lot',p['fill_id'],p['quantity']) for p in b.ledger.ordered())
   qty=b.text(b.ledger.quantity());intent=PartialCloseIntent(self.halt['call_site_id'],'close_all','',0,qty,None)
   order=LotCloseOrder(b.identifier('order',b.order_count),b.order_count,index,time,intent,targets);b.order_count+=1
   b.event('risk_liquidation',{'order_id':order.order_id,'rule':self.halt['rule'],'basis':basis,'reference_price':b.text(Decimal(price)),'quantity':qty,'targets':[{'kind':t.kind,'target_id':t.target_id,'quantity':t.quantity} for t in targets]},index,time)
   limited=b._limit_fill;b._limit_fill=False
   try:b.close_targets(order,targets,b.text(Decimal(price)),index,time)
   finally:b._limit_fill=limited
   self.needs_close=False
  finally:self.busy=False
 def accept_command(self,intent,index,time):
  if not self.blocked:return True
  self.broker.event('risk_blocked',{'command':intent.kind,'entry_id':getattr(intent,'entry_id',''),'rule':self.halt['rule'],'basis':'creation_close'},index,time);return False
 def before_entry(self,order,index,time):
  b=self.broker
  if self.blocked:
   b.cancel_entry(order.intent.entry_id,index,time,'risk_halt');return None,False
  if b.groups.is_net(order):return order,False
  direction=b.policy.limits.allow_entry_in;cap=b.policy.limits.max_position_size
  if direction is not None and direction.value not in ('all','long' if order.intent.direction==1 else 'short'):
   opposite=b.position is not None and b.position['direction']!=order.intent.direction
   b.event('risk_adjusted',{'rule':direction.kind,'call_site_id':direction.call_site_id,'order_id':order.order_id,'entry_id':order.intent.entry_id,'action':'close_only' if opposite else 'blocked','requested_quantity':order.intent.quantity,'quantity':'0'},index,time)
   if opposite:return order,True
   b.cancel_entry(order.intent.entry_id,index,time,'risk_direction');return None,False
  if cap is not None:
   current=b.ledger.quantity() if b.position and b.position['direction']==order.intent.direction else Decimal(0)
   step=Decimal(b.policy.settings.quantity_step);units=max(0,int((Decimal(cap.value)-current)//step))
   qty=min(Decimal(order.intent.quantity),step*units)
   if qty<Decimal(order.intent.quantity):
    b.event('risk_adjusted',{'rule':cap.kind,'call_site_id':cap.call_site_id,'order_id':order.order_id,'entry_id':order.intent.entry_id,'action':'reduced' if qty else 'blocked','requested_quantity':order.intent.quantity,'quantity':b.text(qty)},index,time)
    if not qty:b.cancel_entry(order.intent.entry_id,index,time,'risk_position_size');return None,False
    order=replace(order,intent=replace(order.intent,quantity=b.text(qty)));b.entries[order.intent.entry_id]=order;b.shrink(index,time)
  return order,False
 def prune(self):
  live={p.order_id for p in self.broker.protections.values()};self.retired_orders+=len(self.seen_orders-live);self.seen_orders.intersection_update(live)
 def checkpoint(self):
  b=self.broker
  return {'schema_version':'openpine.risk-state.1','period':self.period,'period_start_net':b.text(self.period_start_net),'peak':b.text(self.peak),'period_peak':b.text(self.period_peak),
   'loss_days':self.loss_days,'filled_orders':self.filled_orders,'retired_orders':self.retired_orders,'seen_orders':sorted(self.seen_orders),'observed_fills':self.observed_fills,
   'halt':dict(self.halt) if self.halt is not None else None,'needs_close':self.needs_close,'trigger_count':self.trigger_count}
 def statistics(self):return {**self.checkpoint(),'blocked':self.blocked,'period_kind':'chart_bar' if self.broker.policy.timeframe in ('W','M') else 'utc_day'}
 def validate(self,value,*,count,last_time,ledger,numbers,protections,fills,observations=None):
  def require(v):
   if not v:raise ValueError('Invalid risk checkpoint.')
  b=self.broker;require(type(value) is dict and value.keys()==self.checkpoint().keys() and value['schema_version']=='openpine.risk-state.1')
  result={k:value[k] for k in value if k!='schema_version'}
  for n in ('period_start_net','peak','period_peak'):
   raw=value[n];require(type(raw) is str);v=Decimal(raw);require(v.is_finite() and abs(v)<=Decimal('1e60') and v.as_tuple().exponent>=-48 and b.text(v)==raw);result[n]=v
  for n in ('loss_days','filled_orders','retired_orders','observed_fills','trigger_count'):require(type(value[n]) is int and 0<=value[n]<=(observations or count)*4096)
  require(type(value['needs_close']) is bool and value['observed_fills']==fills)
  period=value['period'];require(period is None or type(period) is int and period==self.period_key(last_time))
  require(value['loss_days']<=count and value['trigger_count']<=count*4)
  seen=value['seen_orders'];require(type(seen) is list and all(type(x) is str for x in seen) and seen==sorted(set(seen)) and set(seen)<={p.order_id for p in protections.values()})
  require(value['filled_orders']==value['retired_orders']+len(seen)<=fills);result['seen_orders']=set(seen)
  initial=Decimal(b.policy.initial_capital);position=ledger.position();q=Decimal(position['quantity'])*position['direction'] if position else Decimal(0);equity=numbers['cash']+q*numbers['mark']
  require(result['peak']>=max(initial,equity) and result['peak']>=result['period_peak']>=equity)
  halt=value['halt']
  if halt is not None:
   require(type(halt) is dict and halt.keys()=={'rule','call_site_id','period','bar_index','time','basis','equity','reference','observed','threshold','permanent','alert_message'})
   rule=getattr(b.policy.limits,halt['rule'],None);require(rule is not None and halt['rule'] in PRIORITY)
   require(halt['call_site_id']==rule.call_site_id and halt['alert_message']==rule.alert_message and type(halt['permanent']) is bool)
   require(type(halt['bar_index']) is int and 0<=halt['bar_index']<count and type(halt['time']) is int and 0<=halt['time']<=last_time and halt['basis'] in ('open','fill','close'))
   require(type(halt['period']) is int and halt['period']==self.period_key(halt['time']) and halt['period']<=period)
   amounts={}
   for n in ('equity','reference','observed','threshold'):
    raw=halt[n];require(type(raw) is str);d=Decimal(raw);require(d.is_finite() and abs(d)<=Decimal('1e60') and d.as_tuple().exponent>=-48 and b.text(d)==raw);amounts[n]=d
   target=Decimal(rule.value)*amounts['reference']/100 if rule.unit=='percent_of_equity' else Decimal(rule.value)
   insolvent=rule.unit=='percent_of_equity' and amounts['equity']<=0
   require(amounts['threshold']==target and (amounts['observed']>=target or insolvent))
   require(halt['permanent']==(halt['rule'] in ('max_drawdown','max_cons_loss_days') or insolvent))
   if halt['rule'] in ('max_drawdown','max_intraday_loss'):require(amounts['reference']-amounts['equity']==amounts['observed'] and amounts['reference']<=result['peak'])
   else:require(amounts['reference']==0 and amounts['observed']==amounts['observed'].to_integral_value())
   active=halt['permanent'] or halt['period']==period
   require(not active or not protections)
   require(value['needs_close']==(position is not None and active))
   if value['needs_close']:require(halt['basis']=='close')
   result['halt']=dict(halt)
  else:require(not value['needs_close'] and value['trigger_count']==0)
  require((halt is not None)==(value['trigger_count']>0))
  if period is None:require(fills==value['loss_days']==value['filled_orders']==value['trigger_count']==0 and result['peak']==result['period_peak']==initial and result['period_start_net']==0)
  return result
 def restore_values(self,result):
  for n,v in result.items():setattr(self,n,v)
