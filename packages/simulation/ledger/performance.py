"""Incremental closed-slice statistics and read-only current trade observations."""
from decimal import Decimal,localcontext
from .lots import LotLedger
RECORD_FIELDS=('lot_id','entry_fill_id','exit_fill_id','entry_id','direction','quantity','entry_price','exit_price','gross_profit','entry_commission','exit_commission','commission','net_profit','entry_bar','exit_bar','remaining_quantity','lot_closed','entry_time')

class TradePerformance:
 def __init__(self,broker):
  self.track_extremes=False;self.extremes={}
  self.comments={};self.identity_enabled=False
  self.broker=broker;self.history=None;self.wins=self.losses=self.evens=0
  self.positive=self.negative=self.net=self.fees=Decimal(0);self.reads=0
 def observe(self,lot,*prices):
  key=lot['fill_id'];entry=Decimal(lot['price'])
  bounds=self.extremes.get(key,(entry,entry))
  if bounds is not None:
   self.extremes[key]=(min(bounds[0],*prices),max(bounds[1],*prices))
 def excursion_amounts(self,lot,quantity):
  bounds=self.extremes.get(lot['fill_id'])
  if bounds is None:return {}
  entry=Decimal(lot['price']);low,high=bounds
  up,down=(high-entry,entry-low) if lot['direction']==1 else (entry-low,high-entry)
  return {'max_runup':LotLedger.text(up*quantity),'max_drawdown':LotLedger.text(down*quantity)}
 def restore_extremes(self,payload,ledger):
  if payload is None:
   return dict.fromkeys(ledger.lots)
  if type(payload) is not dict or payload.keys()!=ledger.lots.keys():raise ValueError('Trade range membership differs from the ledger.')
  restored={}
  for key,raw in payload.items():
   if raw is None:restored[key]=None;continue
   if type(raw) is not list or len(raw)!=2 or not all(type(x) is str for x in raw):raise ValueError('Invalid trade range.')
   from packages.contracts.trading import decimal_text
   if any(decimal_text(x,positive=True)!=x for x in raw):raise ValueError('Trade range prices must be canonical.')
   low,high=map(Decimal,raw)
   if not (low.is_finite() and high.is_finite() and 0<low<=Decimal(ledger.lots[key]['price'])<=high):raise ValueError('Invalid trade range bounds.')
   restored[key]=(low,high)
  return restored
 def closed(self,value,index,time):
  if self.history is None:raise ValueError('Performance requires its history persistence port.')
  row={**{k:value[k] for k in RECORD_FIELDS},'closed_index':self.history.count,'exit_time':time}
  row.update({k:value[k] for k in ('exit_id','exit_comment','entry_comment','max_runup','max_drawdown') if k in value})
  if 'closed_index' in value and (type(value['closed_index']) is not int or value['closed_index']!=self.history.count):raise ValueError('Trade journal index is not sequential.')
  self.history.append(row)
  profit=Decimal(row['net_profit']);self.net+=profit;self.fees+=Decimal(row['commission'])
  if profit>0:self.wins+=1;self.positive+=profit
  elif profit<0:self.losses+=1;self.negative-=profit
  else:self.evens+=1
  return row
 def snapshot(self):return {'schema_version':'openpine.performance-state.1','archive':self.history.identity(),'wins':self.wins,'losses':self.losses,'evens':self.evens,
  'grossprofit':LotLedger.text(self.positive),'grossloss':LotLedger.text(self.negative),'netprofit':LotLedger.text(self.net),'closed_commission':LotLedger.text(self.fees)}
 def values(self):
  b=self.broker;p=b.ledger.position();q=Decimal(p['quantity'])*p['direction'] if p else Decimal(0)
  remaining=sum((Decimal(l['commission']) for l in b.ledger.ordered()),Decimal(0))
  unrealized=q*(b.mark or Decimal(0))-Decimal(p['cost_basis'])*p['direction'] if p else Decimal(0)
  return {'netprofit':float(self.net),'openprofit':float(unrealized-remaining),'grossprofit':float(self.positive),'grossloss':float(self.negative),
   'wintrades':self.wins,'losstrades':self.losses,'opentrades':len(b.ledger.lots),'closedtrades':self.history.count}
 def read(self,group,field,number):
  self.reads+=1
  if self.reads>4096:raise ValueError('Trade reads exceed the per-bar budget.')
  if number is None or type(number) is not int or number<0:return None
  if group=='closedtrades':
   row=self.history.read(number)
   if row is None:return None
   if field in ('entry_id','exit_id','entry_comment','exit_comment'):return row.get(field)
   value={'size':Decimal(row['quantity'])*row['direction'],'profit':Decimal(row['net_profit']),'commission':Decimal(row['commission']),
    **{n:row[n] for n in ('entry_price','exit_price','entry_bar','exit_bar','entry_time','exit_time')}}
  else:
   lots=self.broker.ledger.ordered()
   if number>=len(lots):return None
   p=lots[number]
   if field=='entry_id':return p['entry_id']
   if field=='entry_comment':return self.comments.get(p['order_id'],{}).get('comment')
   q=Decimal(p['quantity'])*p['direction'];fee=Decimal(p['commission'])
   value={'size':q,'profit':q*((self.broker.mark or Decimal(0))-Decimal(p['price']))-fee,'commission':fee,'entry_price':p['price'],'entry_bar':p['bar_index'],'entry_time':p['time']}
  if field=='profit_percent':return float(100*value['profit']/(abs(value['size'])*Decimal(value['entry_price'])))
  if field.startswith(('max_runup','max_drawdown')):
   amounts=row if group=='closedtrades' else self.excursion_amounts(p,abs(q))
   amount=amounts.get(field.removesuffix('_percent'))
   if amount is None:return None
   if field.endswith('_percent'):return float(100*Decimal(amount)/(abs(value['size'])*Decimal(value['entry_price'])))
   return float(amount)
  if field.endswith('_bar_index'):return int(value[field.removesuffix('_index')])
  if field.endswith('_time'):return int(value[field])
  return float(value[field])
 def validate(self,value,ledger,numbers,trade_count):
  if value!=self.snapshot() or self.history.count!=trade_count:raise ValueError('Trade statistics differ from journal history.')
  if self.net!=numbers['realized']-ledger.allocated_entry_fees-ledger.exit_fees or self.fees!=ledger.allocated_entry_fees+ledger.exit_fees:raise ValueError('Trade statistics differ from the ledger allocations.')
