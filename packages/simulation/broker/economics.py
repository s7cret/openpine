"""Pure economic components composed into the existing lot broker."""
from dataclasses import replace
from decimal import Decimal,ROUND_FLOOR,localcontext
from packages.contracts.economics import EconomicsSettings
from packages.contracts.trading import decimal_text

class QuantityResolver:
    def __init__(self,policy):self.policy=policy
    def resolve(self,requested,price,equity):
        with localcontext() as c:
            c.prec=80;p=self.policy;price=Decimal(decimal_text(str(price),positive=True));equity=Decimal(equity);step=Decimal(p.settings.quantity_step)
            mode='explicit' if requested is not None else p.quantity_mode;amount=Decimal(requested if requested is not None else p.default_quantity)
            if requested is not None or mode=='fixed':
                decimal_text(str(amount),positive=True)
                if amount%step:raise ValueError('Explicit/fixed quantity must align with the pinned step.')
                raw=quantity=amount;budget=None
            else:
                budget=amount if mode=='cash' else max(Decimal(0),equity)*amount/100
                raw=budget/price;quantity=(raw/step).to_integral_value(rounding=ROUND_FLOOR)*step
            reason=self.minimum(quantity,price)
            from packages.simulation.ledger.lots import LotLedger
            text=LotLedger.text
            return {'mode':mode,'requested':None if requested is None else text(amount),'default_value':p.default_quantity,
              'basis':'confirmed_creation_close','reference_price':text(price),'equity':text(equity),'budget':text(budget) if budget is not None else None,
              'unrounded_quantity':text(raw),'quantity':text(quantity),'quantity_step':p.settings.quantity_step,
              'status':'skipped' if reason else 'resolved','reason':reason}
    def minimum(self,quantity,price):
        s=self.policy.settings
        if quantity<=0:return 'zero_after_rounding'
        if type(s) is EconomicsSettings:
            if s.minimum_quantity is not None and quantity<Decimal(s.minimum_quantity):return 'below_minimum_quantity'
            if s.minimum_notional is not None and quantity*price<Decimal(s.minimum_notional):return 'below_minimum_notional'
        return None

class CommissionCalculator:
    def __init__(self,broker):self.broker=broker;self.paid=set();self.charged_count=0;self.retired_count=0
    def fee(self,order,quantity,price):
        p=self.broker.policy.commission;value=Decimal(p.value)
        if p.kind=='percent':raw=abs(quantity*price)*value/100
        elif p.kind=='cash_per_contract':raw=abs(quantity)*value
        else:
            if order.order_id in self.paid:return Decimal(0)
            self.paid.add(order.order_id);self.charged_count+=1;raw=value
        return self.broker.money(raw)
    def prune(self):
        live={o.order_id for o in self.broker.protections.values()};self.retired_count+=len(self.paid-live);self.paid.intersection_update(live)
    def checkpoint(self):return {'schema_version':'openpine.commission-state.1','charged_count':self.charged_count,'retired_count':self.retired_count,'paid_order_ids':sorted(self.paid)}
    def validate(self,v,protections,fees,fills,orders):
        if type(v) is not dict or v.keys()!=self.checkpoint().keys() or v['schema_version']!='openpine.commission-state.1':raise ValueError('Invalid commission checkpoint.')
        count=v['charged_count'];retired=v['retired_count'];paid=v['paid_order_ids']
        if type(count) is not int or not 0<=count<=fills or type(retired) is not int or not 0<=retired<=count or type(paid) is not list or any(type(x) is not str for x in paid) or paid!=sorted(set(paid)) or not set(paid)<={p.order_id for p in protections.values()} or retired+len(paid)!=count:raise ValueError('Invalid paid order membership.')
        p=self.broker.policy.commission
        if p.kind=='cash_per_order':
            if count>orders or (count==0)!=(fills==0) or fees!=count*self.broker.money(Decimal(p.value)):raise ValueError('Flat fees differ from charged order count.')
        elif count or paid:raise ValueError('Non-flat commission cannot carry paid order state.')
        return count,retired,set(paid)

class Economics:
    def __init__(self,broker):self.broker=broker;self.quantities=QuantityResolver(broker.policy);self.commissions=CommissionCalculator(broker)
    def prepare(self,intent,index,time):
        if intent.kind not in ('entry','order'):return intent
        b=self.broker;q=Decimal(b.position['quantity'])*b.position['direction'] if b.position else Decimal(0)
        value=self.quantities.resolve(intent.quantity,b.mark,b.cash+q*b.mark)
        b.event('quantity_resolved',{'entry_id':intent.entry_id,'command':intent.kind,'call_site_id':intent.call_site_id,**value},index,time)
        return None if value['status']=='skipped' else replace(intent,quantity=value['quantity'])
    def before_entry(self,order,index,time,price,limit):
        b=self.broker;direction=order.intent.direction
        actual=Decimal(price) if limit else b.price(b.text(Decimal(price)),direction)
        reason=self.quantities.minimum(Decimal(order.intent.quantity),actual)
        if reason:
            b.cancel_entry(order.intent.entry_id,index,time,reason)
            b.event('ignored',{'order_id':order.order_id,'entry_id':order.intent.entry_id,'reason':reason,'basis':'actual_fill_price','reference_price':b.text(actual),'quantity':order.intent.quantity},index,time)
            return False
        return True
    def checkpoint(self):return {'schema_version':'openpine.economics-state.1','commission':self.commissions.checkpoint()}
    def validate(self,v,protections,fees,fills,orders):
        if type(v) is not dict or v.keys()!={'schema_version','commission'} or v['schema_version']!='openpine.economics-state.1':raise ValueError('Invalid economics checkpoint.')
        return self.commissions.validate(v['commission'],protections,fees,fills,orders)
