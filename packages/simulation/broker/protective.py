"""One exact-entry bracket, next-bar OHLC execution and explicit ambiguity."""
from dataclasses import asdict,replace
from decimal import Decimal,localcontext
from packages.contracts.protection import PROFILE,ProtectionIntent,ProtectionOrder
from packages.contracts.trading import OrderIntent,MarketOrder,decimal_text
from packages.contracts.wire import digest
from . import Broker


class ProtectiveBroker(Broker):
    def __init__(self,policy,identity):
        if policy.profile_id!=PROFILE:raise ValueError('Protective broker requires its pinned policy.')
        super().__init__(policy,identity)
        self.protection=None;self._limit_fill=False

    def price(self,value,direction):
        return Decimal(decimal_text(value,positive=True)) if self._limit_fill else super().price(value,direction)

    def leg_id(self,order,role):return digest({'bracket':order.order_id,'role':role})

    def cancel_protection(self,index,time,reason):
        if self.protection:
            self.event('cancelled',{'order_id':self.protection.order_id,'exit_id':self.protection.intent.exit_id,'reason':reason},index,time)
            self.protection=None

    def close_position(self,order,price,index,time):
        if self.protection and self.protection.target_kind=='fill' and self.protection.target_id==self.position['fill_id']:
            self.cancel_protection(index,time,'position_closed_or_reversed')
        return super().close_position(order,price,index,time)

    def fill(self,order,direction,quantity,price,index,time):
        fill=super().fill(order,direction,quantity,price,index,time)
        if self.protection and self.protection.target_kind=='pending' and self.protection.target_id==order.order_id:
            self.protection=replace(self.protection,target_kind='fill',target_id=fill.fill_id)
            self.event('protection_activated',{'order_id':self.protection.order_id,'entry_fill_id':fill.fill_id},index,time)
        return fill

    def begin(self,index,time,open_price,close_price,high=None,low=None):
        prices=[Decimal(decimal_text(v,positive=True)) for v in (open_price,high,low,close_price)]
        opening,highest,lowest,closing=prices
        if highest<max(opening,closing) or lowest>min(opening,closing) or lowest>highest:
            raise ValueError('Protective execution requires consistent positive OHLC.')
        super().begin(index,time,open_price,close_price)
        with localcontext() as context:
            context.prec=80
            order=self.protection
            if order and (not self.position or order.target_kind!='fill' or order.target_id!=self.position['fill_id']):
                self.cancel_protection(index,time,'target_not_filled');order=None
            if order:
                if order.created_bar>=index:raise ValueError('Protection cannot fill on its creation bar.')
                stop=Decimal(order.intent.stop) if order.intent.stop is not None else None
                limit=Decimal(order.intent.limit) if order.intent.limit is not None else None
                long=order.direction==1
                stop_gap=stop is not None and (opening<=stop if long else opening>=stop)
                limit_gap=limit is not None and (opening>=limit if long else opening<=limit)
                stop_touch=stop is not None and (lowest<=stop if long else highest>=stop)
                limit_touch=limit is not None and (highest>=limit if long else lowest<=limit)
                role='stop' if stop_gap else 'limit' if limit_gap else 'stop' if stop_touch else 'limit' if limit_touch else None
                if role:
                    gap=stop_gap or limit_gap
                    level=stop if role=='stop' else limit
                    ambiguous=not gap and stop_touch and limit_touch
                    self.event('protection_trigger',{'order_id':order.order_id,'exit_id':order.intent.exit_id,
                        'entry_id':order.intent.entry_id,'role':role,'level':self.text(level),
                        'basis':'open_crossing' if gap else 'ohlc_touch','ambiguous':bool(ambiguous),
                        'policy':PROFILE,'stop':order.intent.stop,'limit':order.intent.limit},index,time)
                    self.protection=None
                    intent=OrderIntent(order.intent.call_site_id,'close',order.intent.entry_id,0,None)
                    leg=MarketOrder(self.leg_id(order,role),order.sequence,order.created_bar,order.created_time,intent)
                    self._limit_fill=role=='limit'
                    try:self.close_position(leg,self.text(opening if gap else level),index,time)
                    finally:self._limit_fill=False
                    other='limit' if role=='stop' else 'stop'
                    if getattr(order.intent,other) is not None:
                        self.event('cancelled',{'order_id':self.leg_id(order,other),'exit_id':order.intent.exit_id,
                            'reason':'bracket_peer_filled','filled_order_id':leg.order_id},index,time)
        return self.views()

    def validate_intent(self,intent):
        if type(intent) is OrderIntent:return super().validate_intent(intent)
        if (type(intent) is not ProtectionIntent or type(intent.call_site_id) is not str or not intent.call_site_id
                or intent.kind not in ('exit','cancel','cancel_all')):raise ValueError('Invalid protective intent.')
        if intent.kind=='exit':
            for name in ('entry_id','exit_id'):
                if type(getattr(intent,name)) is not str or not 0<len(getattr(intent,name))<=128:raise ValueError('Invalid protection ID.')
            if intent.stop is None and intent.limit is None:raise ValueError('Reached protective exit requires a finite stop or limit.')
            with localcontext() as context:
                context.prec=80
                for value in (intent.stop,intent.limit):
                    if value is not None:
                        if type(value) is not str or decimal_text(value,positive=True)!=value:raise ValueError('Invalid protective price.')
                        if self.policy.settings.tick_size and Decimal(value)%Decimal(self.policy.settings.tick_size):
                            raise ValueError('Protective price does not match the pinned tick.')
        elif (intent.entry_id!='' or intent.stop is not None or intent.limit is not None
                or type(intent.exit_id) is not str or (intent.exit_id!='' if intent.kind=='cancel_all' else not 0<len(intent.exit_id)<=128)):
            raise ValueError('Invalid cancellation intent.')

    def validate_pair(self,intent,direction):
        if intent.stop is not None and intent.limit is not None:
            if not (Decimal(intent.stop)<Decimal(intent.limit) if direction==1 else Decimal(intent.stop)>Decimal(intent.limit)):
                raise ValueError('A long bracket requires stop < limit; a short bracket requires stop > limit.')

    def commit(self,intents,index,time):
        if self.phase!='executing' or index!=self.count or len(intents)>16:raise ValueError('Invalid protective commit boundary.')
        with localcontext() as context:
            context.prec=80
            pending={}
            for intent in intents:
                self.validate_intent(intent)
                if type(intent) is OrderIntent:
                    if intent.entry_id in pending:self.event('replaced',{'order_id':pending[intent.entry_id].order_id},index,time)
                    order=MarketOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent)
                    self.order_count+=1;pending[intent.entry_id]=order;self.event('order',asdict(order),index,time)
                elif intent.kind in ('cancel','cancel_all'):
                    for key in list(pending):
                        if intent.kind=='cancel_all' or key==intent.exit_id:
                            self.event('cancelled',{'order_id':pending.pop(key).order_id,'reason':intent.kind},index,time)
                    if self.protection and (intent.kind=='cancel_all' or self.protection.intent.exit_id==intent.exit_id):
                        self.cancel_protection(index,time,intent.kind)
                    self.event('cancel_command',{'id':intent.exit_id,'all':intent.kind=='cancel_all'},index,time)
                else:
                    if self.position and self.position['entry_id']==intent.entry_id:
                        kind,target,direction='fill',self.position['fill_id'],self.position['direction']
                    else:
                        target_order=pending.get(intent.entry_id)
                        if target_order is None or target_order.intent.kind!='entry':
                            self.event('ignored',{'exit_id':intent.exit_id,'reason':'no_matching_entry'},index,time);continue
                        kind,target,direction='pending',target_order.order_id,target_order.intent.direction
                    self.validate_pair(intent,direction)
                    if self.protection:
                        if self.protection.intent.exit_id!=intent.exit_id:raise ValueError('Only one bracket is admitted; cancel the previous exit ID first.')
                        self.cancel_protection(index,time,'modified')
                    order=ProtectionOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent,kind,target,direction)
                    self.order_count+=1;self.protection=order;self.event('order',asdict(order),index,time)
            self.pending=list(pending.values())
            if self.protection and self.protection.target_kind=='pending' and not any(o.order_id==self.protection.target_id for o in self.pending):
                self.cancel_protection(index,time,'target_removed')
            self.event('mark',self.statistics(),index,time)
        self.count+=1;self.last_time=time;self.phase='committed'
        events=self.events;self.events=[]
        return events

    def statistics(self):
        value=super().statistics();order=self.protection
        value['protection']=None if order is None else {'order_id':order.order_id,'exit_id':order.intent.exit_id,
            'from_entry':order.intent.entry_id,'stop':order.intent.stop,'limit':order.intent.limit,
            'created_bar':order.created_bar,'target_kind':order.target_kind,'target_id':order.target_id}
        return value

    def checkpoint(self):
        value=super().checkpoint()
        return {**value,'schema_version':'openpine.broker-state.2','protection':asdict(self.protection) if self.protection else None}

    def restore(self,state,*,count,last_time,sites):
        if type(state) is not dict or state.get('schema_version')!='openpine.broker-state.2' or 'protection' not in state:
            raise ValueError('Protective state .2 required; no implicit migration.')
        # Validate the whole legacy envelope before dereferencing nested targets.
        # A rejected extension must leave the destination broker untouched.
        core={k:v for k,v in state.items() if k!='protection'};core['schema_version']='openpine.broker-state.1'
        verified=Broker(self.policy,self.identity)
        verified.restore(core,count=count,last_time=last_time,sites=sites)
        raw=state['protection'];order=None
        if raw is not None:
            if type(raw) is not dict or raw.keys()!=set(ProtectionOrder.__dataclass_fields__):raise ValueError('Invalid protection state fields.')
            intent=raw['intent']
            if type(intent) is not dict or intent.keys()!=set(ProtectionIntent.__dataclass_fields__):raise ValueError('Invalid saved protection intent.')
            intent=ProtectionIntent(**intent);self.validate_intent(intent)
            if (intent.kind!='exit' or intent.call_site_id not in sites or type(raw['direction']) is not int or raw['direction'] not in (-1,1)
                    or type(raw['sequence']) is not int or not 0<=raw['sequence']<state['order_count']
                    or raw['order_id']!=self.identifier('order',raw['sequence']) or type(raw['created_bar']) is not int
                    or not 0<=raw['created_bar']<count or type(raw['created_time']) is not int
                    or not 0<=raw['created_time']<=last_time or raw['target_kind'] not in ('fill','pending')
                    or any(raw['sequence']==o.sequence for o in verified.pending)):
                raise ValueError('Invalid protection identity, site or boundary.')
            self.validate_pair(intent,raw['direction']);order=ProtectionOrder(**{**raw,'intent':intent})
            if order.target_kind=='fill':
                p=state['position']
                if (not p or order.target_id!=p['fill_id'] or intent.entry_id!=p['entry_id'] or order.direction!=p['direction']):
                    raise ValueError('Protection belongs to another position.')
            else:
                target=next((o for o in state['pending'] if o['order_id']==order.target_id),None)
                if (not target or target['intent']['kind']!='entry' or target['intent']['entry_id']!=intent.entry_id
                        or target['intent']['direction']!=order.direction):raise ValueError('Protection belongs to another pending entry.')
        super().restore(core,count=count,last_time=last_time,sites=sites)
        self.protection=order
