"""Shared pure sizing, intent validation and entry crossing rules."""
from decimal import Decimal,ROUND_FLOOR
from dataclasses import asdict
from packages.contracts.trading import OrderIntent,decimal_text
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.contracts.protection import ProtectionIntent
from . import Broker

def units(self,value):
    return int((Decimal(value)/Decimal(self.policy.settings.quantity_step)).to_integral_value(rounding=ROUND_FLOOR))


def quantity(self,units):return self.text(Decimal(units)*Decimal(self.policy.settings.quantity_step))


def selected(self,intent,available):
    qty=getattr(intent,'quantity',None);percent=getattr(intent,'percent',None)
    if qty is None:
        percent=Decimal(percent) if percent is not None else Decimal(100)
        if not 0<=percent<=100:raise ValueError('Partial percent requires 0..100.')
        value=Decimal(available)*percent/100
    else:
        value=Decimal(qty)
        if value<0:raise ValueError('Partial quantity cannot be negative.')
    return min(self.units(available),self.units(value))


def validate_partial_intent(self,intent):
    if type(intent) is OrderIntent:return Broker.validate_intent(self,intent)
    if type(intent) is ProtectionIntent:
        intent=PartialProtectionIntent(**asdict(intent))
    if type(intent) not in (PartialCloseIntent,PartialProtectionIntent):raise ValueError('Invalid partial intent type.')
    if type(intent.call_site_id) is not str or not intent.call_site_id:raise ValueError('Invalid partial call site.')
    if type(intent) is PartialCloseIntent:
        if intent.kind!='close' or intent.direction!=0 or type(intent.direction) is not int:raise ValueError('Invalid partial close.')
    elif intent.kind not in ('exit','cancel','cancel_all'):raise ValueError('Invalid partial protection kind.')
    cancellation=type(intent) is PartialProtectionIntent and intent.kind!='exit'
    if not cancellation and (type(intent.entry_id) is not str or not 0<len(intent.entry_id)<=128):raise ValueError('Invalid partial entry ID.')
    if type(intent) is PartialProtectionIntent:
        if type(intent.exit_id) is not str or (intent.exit_id!='' if intent.kind=='cancel_all' else not 0<len(intent.exit_id)<=128):raise ValueError('Invalid partial exit ID.')
        if cancellation:
            if intent.entry_id!='' or any(getattr(intent,n) is not None for n in ('stop','limit','quantity','percent')):raise ValueError('Invalid partial cancellation.')
        else:
            if intent.stop is None and intent.limit is None:raise ValueError('Exit requires stop or limit.')
            for level in (intent.stop,intent.limit):
                if level is not None and (type(level) is not str or decimal_text(level,positive=True)!=level or self.policy.settings.tick_size and Decimal(level)%Decimal(self.policy.settings.tick_size)):
                    raise ValueError('Invalid partial price or tick alignment.')
    for value in (intent.quantity,intent.percent):
        if value is not None and (type(value) is not str or decimal_text(value)!=value):raise ValueError('Invalid partial quantity scalar.')
    if not cancellation:self.selected(intent,'1')


def entry_crossing(self,order,index,time,opening,highest,lowest,*,at_open):
    intent=order.intent;long=intent.direction==1;kind=order.order_kind
    if kind=='market':return (opening,False) if at_open else None
    if kind=='stop_limit' and order.activated_bar is None:
        stop=Decimal(intent.stop)
        touched=(opening>=stop if long else opening<=stop) if at_open else (highest>=stop if long else lowest<=stop)
        if not touched:return None
        order=self.activate(order,index,time,'open_crossing' if at_open else 'ohlc_touch')
    if kind=='stop_limit' and order.activated_bar==index and order.activation_basis=='ohlc_touch':return None
    limit=intent.limit is not None
    level=Decimal(intent.limit if limit else intent.stop)
    touched=((opening<=level if long else opening>=level) if limit else (opening>=level if long else opening<=level)) if at_open else ((lowest<=level if long else highest>=level) if limit else (highest>=level if long else lowest<=level))
    return (opening if at_open else level,limit) if touched else None

