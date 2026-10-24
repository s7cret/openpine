"""Persistent entry orders; shared partial-exit accounting and reservation engine."""
from dataclasses import asdict,replace
from decimal import Decimal,localcontext
from packages.contracts.pending_entries import PROFILE,EntryIntent,EntryOrder
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent,PartialMarketOrder
from packages.contracts.protection import ProtectionIntent
from packages.contracts.trading import OrderIntent,decimal_text
from .partial import PartialBroker


class PendingEntryBroker(PartialBroker):
    PROFILE_ID=PROFILE
    STATE_SCHEMA='openpine.broker-state.4'

    def __init__(self,policy,identity):
        super().__init__(policy,identity);self.entries={}

    def validate_intent(self,intent):
        if type(intent) is not EntryIntent:return super().validate_intent(intent)
        super().validate_intent(OrderIntent(intent.call_site_id,intent.kind,intent.entry_id,intent.direction,intent.quantity))
        if intent.kind!='entry':raise ValueError('Entry intent requires entry kind.')
        for level in (intent.limit,intent.stop):
            if level is not None and (type(level) is not str or decimal_text(level,positive=True)!=level or self.policy.settings.tick_size and Decimal(level)%Decimal(self.policy.settings.tick_size)):
                raise ValueError('Entry level requires a positive canonical price aligned to the pinned tick.')

    def describe_entry(self,order):
        return {**asdict(order),'order_kind':order.order_kind,
            'status':'limit_active' if order.order_kind=='stop_limit' and order.activated_bar is not None else 'pending'}

    def activate(self,order,index,time,basis):
        order=replace(order,activated_bar=index,activated_time=time,activation_basis=basis)
        self.entries[order.intent.entry_id]=order
        self.event('entry_activated',{**self.describe_entry(order),'limit_eligible_bar':index if basis=='open_crossing' else index+1},index,time)
        return order

    def crossing(self,order,index,time,opening,highest,lowest,*,at_open):
        from .order_helpers import entry_crossing
        return entry_crossing(self,order,index,time,opening,highest,lowest,at_open=at_open)

    def execute_entry(self,order,index,time,price,limit,basis):
        self.entries.pop(order.intent.entry_id)
        self.event('entry_trigger',{**self.describe_entry(order),'basis':basis,'reference_price':self.text(price),
            'protection_eligible_bar':index if basis=='open_crossing' else index+1},index,time)
        self._limit_fill=limit
        try:return self.open_position(order,self.text(price),index,time)
        finally:self._limit_fill=False

    def _begin(self,index,time,open_price,close_price,high=None,low=None):
        if self.phase not in ('ready','committed') or type(index) is not int or index!=self.count or type(time) is not int or (self.last_time is not None and time<=self.last_time):raise ValueError('Invalid pending-entry bar boundary.')
        with localcontext() as context:
            context.prec=80
            opening,highest,lowest,closing=[Decimal(decimal_text(v,positive=True)) for v in (open_price,high,low,close_price)]
            if highest<max(opening,closing) or lowest>min(opening,closing) or lowest>highest:raise ValueError('Pending entries require consistent positive OHLC.')
            self.events=[];self.phase='executing';self.mark=closing;actions=[]
            for order in sorted(self.entries.values(),key=lambda o:o.sequence):
                if order.created_bar>=index:raise ValueError('Entry cannot execute on its creation bar.')
                match=self.crossing(order,index,time,opening,highest,lowest,at_open=True)
                if match:actions.append((order.sequence,'entry',self.entries[order.intent.entry_id],match))
            actions.extend((o.sequence,'close',o,None) for o in self.pending)
            for _,kind,order,match in sorted(actions,key=lambda row:row[0]):
                if kind=='entry':self.execute_entry(order,index,time,*match,'open_crossing')
                elif self.position and self.position['fill_id']==order.target_id:
                    self.close_position(order,open_price,index,time,order.intent.quantity)
                else:self.event('ignored',{'order_id':order.order_id,'reason':'no_matching_position'},index,time)
            self.pending=[];self.shrink(index,time)
            self.execute_protections(index,time,opening,highest,lowest)
            filled=False
            for order in sorted(list(self.entries.values()),key=lambda o:o.sequence):
                match=self.crossing(order,index,time,opening,highest,lowest,at_open=False)
                if match:
                    current=self.entries[order.intent.entry_id]
                    if filled:
                        self.event('entry_deferred',{'order_id':order.order_id,'entry_id':order.intent.entry_id,'reason':'unknown_intrabar_entry_order'},index,time)
                    else:filled=self.execute_entry(current,index,time,*match,'ohlc_touch')
            return self.views()

    def cancel_entry(self,name,index,time,reason):
        order=self.entries.pop(name,None)
        if order:
            self.cancel_target(order.order_id,index,time,'target_removed')
            self.event('cancelled',{**self.describe_entry(order),'reason':reason},index,time)

    def _commit(self,intents,index,time):
        if self.phase!='executing' or index!=self.count or len(intents)>16:raise ValueError('Invalid pending-entry commit boundary.')
        with localcontext() as context:
            context.prec=80;pending={}
            for original in intents:
                self.validate_intent(original)
                intent=PartialProtectionIntent(**asdict(original)) if type(original) is ProtectionIntent else original
                if intent.kind=='entry':
                    if type(intent) is OrderIntent:intent=EntryIntent(**asdict(intent))
                    self.cancel_entry(intent.entry_id,index,time,'modified_entry')
                    if len(self.entries)>=16:raise ValueError('At most 16 pending entry IDs.')
                    order=EntryOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent)
                    self.order_count+=1;self.entries[intent.entry_id]=order
                    self.event('order',self.describe_entry(order),index,time)
                elif type(intent) in (OrderIntent,PartialCloseIntent):
                    if not self.position or self.position['entry_id']!=intent.entry_id:
                        self.event('ignored',{'entry_id':intent.entry_id,'reason':'no_matching_position'},index,time);continue
                    units=self.selected(intent,self.position['quantity'])
                    if not units:self.event('ignored',{'entry_id':intent.entry_id,'reason':'zero_after_rounding'},index,time);continue
                    key=('close',intent.entry_id)
                    if key in pending:self.event('replaced',{'order_id':pending[key].order_id},index,time)
                    selected=PartialCloseIntent(intent.call_site_id,'close',intent.entry_id,0,self.quantity(units),None)
                    order=PartialMarketOrder(self.identifier('order',self.order_count),self.order_count,index,time,selected,self.position['fill_id'])
                    self.order_count+=1;pending[key]=order;self.event('order',asdict(order),index,time)
                elif intent.kind in ('cancel','cancel_all'):
                    for name in list(self.entries):
                        if intent.kind=='cancel_all' or name==intent.exit_id:self.cancel_entry(name,index,time,intent.kind)
                    for key,order in list(pending.items()):
                        if intent.kind=='cancel_all' or key[1]==intent.exit_id:
                            pending.pop(key);self.event('cancelled',{'order_id':order.order_id,'reason':intent.kind},index,time)
                    for name in list(self.protections):
                        if intent.kind=='cancel_all' or name==intent.exit_id:self.cancel_exit(name,index,time,intent.kind)
                    self.event('cancel_command',{'id':intent.exit_id,'all':intent.kind=='cancel_all'},index,time)
                else:self.reserve_exit(intent,{('entry',k):v for k,v in self.entries.items()},index,time)
            self.pending=sorted(pending.values(),key=lambda o:o.sequence)
            self.event('mark',self.statistics(),index,time)
        self.count+=1;self.last_time=time;self.phase='committed';events=self.events;self.events=[];return events

    def _statistics(self):
        return {**super()._statistics(),'entries':[self.describe_entry(o) for o in sorted(self.entries.values(),key=lambda o:o.sequence)]}

    def checkpoint(self):
        return {**super().checkpoint(),'entry_orders':[asdict(o) for o in sorted(self.entries.values(),key=lambda o:o.sequence)]}

    def restore_entry_targets(self,state,count,last_time,sites):
        def require(value):
            if not value:raise ValueError('Invalid pending-entry checkpoint.')
        raw_entries=state['entry_orders'];require(type(raw_entries) is list and len(raw_entries)<=16)
        require(type(state['pending']) is list and all(type(o) is dict and type(o.get('intent')) is dict and o['intent'].get('kind')=='close' for o in state['pending']))
        targets=[];names=set();previous=-1
        for raw in raw_entries:
            require(type(raw) is dict and raw.keys()==set(EntryOrder.__dataclass_fields__))
            payload=raw['intent'];require(type(payload) is dict and payload.keys()==set(EntryIntent.__dataclass_fields__))
            intent=EntryIntent(**payload);self.validate_intent(intent)
            require(intent.call_site_id in sites and intent.entry_id not in names);names.add(intent.entry_id)
            require(type(raw['sequence']) is int and raw['sequence']>previous);previous=raw['sequence']
            order=EntryOrder(**{**raw,'intent':intent});activation=(order.activated_bar,order.activated_time,order.activation_basis)
            if activation!=(None,None,None):
                require(order.order_kind=='stop_limit' and type(order.activated_bar) is int and type(order.created_bar) is int and order.created_bar<order.activated_bar<count)
                require(type(order.activated_time) is int and type(order.created_time) is int and order.created_time<order.activated_time<=last_time)
                require(order.activation_basis in ('open_crossing','ohlc_touch'))
            if order.order_kind=='market':require(order.created_bar==count-1 and order.created_time==last_time)
            targets.append(order)
        return tuple(targets)

    def restore_entry_targets_commit(self,targets):self.entries={o.intent.entry_id:o for o in targets}

    @staticmethod
    def checkpoint_fields():return (*PartialBroker.checkpoint_fields(),'entry_orders')
