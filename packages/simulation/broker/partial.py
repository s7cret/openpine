"""Bounded exact-target reservations and partial slices for one historical entry."""
from dataclasses import asdict,replace
from decimal import Decimal,localcontext,ROUND_FLOOR,InvalidOperation
from packages.contracts.partial_exits import PROFILE,PartialCloseIntent,PartialProtectionIntent,PartialMarketOrder,ReservedExit
from packages.contracts.protection import ProtectionIntent
from packages.contracts.trading import BrokerPolicy,OrderIntent,Trade,decimal_text
from packages.contracts.wire import digest
from . import Broker

class PartialBroker(Broker):
    PROFILE_ID=PROFILE
    STATE_SCHEMA='openpine.broker-state.3'

    def __init__(self,policy,identity):
        if type(policy) is not BrokerPolicy or policy.profile_id!=self.PROFILE_ID:raise ValueError('Partial broker requires its pinned policy.')
        super().__init__(policy,identity)
        self.protections={};self._limit_fill=False
        self.entry_count=0;self.closed_positions=0
        self.entry_fees=Decimal(0);self.exit_fees=Decimal(0);self.allocated_entry_fees=Decimal(0)

    def price(self,value,direction):
        return Decimal(decimal_text(value,positive=True)) if self._limit_fill else super().price(value,direction)

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

    def cancel_exit(self,name,index,time,reason):
        order=self.protections.pop(name,None)
        if order:self.event('cancelled',{'order_id':order.order_id,'exit_id':name,'quantity':order.quantity,'reason':reason},index,time)

    def cancel_target(self,target,index,time,reason):
        for name,order in list(self.protections.items()):
            if order.target_id==target:self.cancel_exit(name,index,time,reason)

    def shrink(self,index,time):
        available=self.units(self.position['quantity']) if self.position else 0
        target=self.position['fill_id'] if self.position else None
        for name,order in sorted(list(self.protections.items()),key=lambda item:item[1].sequence):
            if order.target_kind!='fill':continue
            if order.target_id!=target:self.cancel_exit(name,index,time,'position_closed_or_reversed');continue
            units=min(available,self.units(order.quantity));available-=units
            if not units:self.cancel_exit(name,index,time,'reservation_exhausted')
            elif units!=self.units(order.quantity):
                self.protections[name]=replace(order,quantity=self.quantity(units))
                self.event('reservation_reduced',{'order_id':order.order_id,'exit_id':name,'quantity':self.quantity(units)},index,time)

    def close_position(self,order,price,index,time,quantity=None):
        entry=self.position
        if entry is None:return
        qty=min(Decimal(entry['quantity']),Decimal(quantity or entry['quantity']))
        if qty<=0:return
        exit_fill=super().fill(order,-entry['direction'],qty,price,index,time)
        self.exit_fees+=Decimal(exit_fill.commission)
        closed=Decimal(entry['closed_quantity'])+qty;original=Decimal(entry['original_quantity'])
        allocated=self.money(Decimal(entry['original_commission'])*closed/original)
        share=allocated-Decimal(entry['allocated_commission']);self.allocated_entry_fees+=share
        gross=(Decimal(exit_fill.price)-Decimal(entry['price']))*qty*entry['direction']
        commission=share+Decimal(exit_fill.commission)
        trade=Trade(self.identifier('trade',self.trade_count),entry['fill_id'],exit_fill.fill_id,entry['entry_id'],entry['direction'],
            self.text(qty),entry['price'],exit_fill.price,self.text(gross),self.text(commission),self.text(gross-commission),entry['bar_index'],index)
        self.trade_count+=1;self.realized+=gross
        remaining=original-closed
        self.event('trade',{**asdict(trade),'entry_commission':self.text(share),'exit_commission':exit_fill.commission,
            'remaining_quantity':self.text(remaining),'position_closed':remaining==0},index,time)
        if remaining:
            self.position={**entry,'quantity':self.text(remaining),'commission':self.text(Decimal(entry['original_commission'])-allocated),
                'closed_quantity':self.text(closed),'allocated_commission':self.text(allocated),'closed_slices':entry['closed_slices']+1}
        else:
            self.position=None;self.closed_positions+=1
        self.shrink(index,time)

    def begin(self,index,time,open_price,close_price,high=None,low=None):
        try:return self._begin(index,time,open_price,close_price,high,low)
        except BaseException:
            self.phase='failed'
            raise

    def _begin(self,index,time,open_price,close_price,high=None,low=None):
        if self.phase not in ('ready','committed') or type(index) is not int or index!=self.count or type(time) is not int or (self.last_time is not None and time<=self.last_time):raise ValueError('Invalid partial broker bar boundary.')
        with localcontext() as context:
            context.prec=80
            opening,highest,lowest,closing=[Decimal(decimal_text(v,positive=True)) for v in (open_price,high,low,close_price)]
            if highest<max(opening,closing) or lowest>min(opening,closing) or lowest>highest:raise ValueError('Partial broker requires consistent positive OHLC.')
            self.events=[];self.phase='executing';self.mark=closing
            for order in self.pending:
                if order.created_bar>=index:raise ValueError('Order cannot fill on its creation bar.')
                intent=order.intent
                if intent.kind=='entry':
                    self.open_position(order,open_price,index,time)
                elif not self.position or self.position['fill_id']!=order.target_id:
                    self.event('ignored',{'order_id':order.order_id,'reason':'no_matching_position'},index,time)
                else:self.close_position(order,open_price,index,time,intent.quantity)
            self.pending=[];self.shrink(index,time)
            self.execute_protections(index,time,opening,highest,lowest)
            return self.views()

    def open_position(self,order,price,index,time):
        intent=order.intent
        if self.position and self.position['direction']==intent.direction:
            self.cancel_target(order.order_id,index,time,'target_not_filled')
            self.event('ignored',{'order_id':order.order_id,'reason':'pyramiding_disabled'},index,time);return False
        if self.position:self.close_position(order,price,index,time)
        fill=Broker.fill(self,order,intent.direction,Decimal(intent.quantity),price,index,time)
        self.entry_count+=1;self.entry_fees+=Decimal(fill.commission)
        self.position={**asdict(fill),'original_quantity':fill.quantity,'original_commission':fill.commission,
            'closed_quantity':'0','allocated_commission':'0','closed_slices':0,'entry_sequence':self.fill_count-1,'entry_order_sequence':order.sequence}
        for name,protection in list(self.protections.items()):
            if protection.target_kind=='pending' and protection.target_id==order.order_id:
                self.protections[name]=replace(protection,target_kind='fill',target_id=fill.fill_id)
                self.event('protection_activated',{'order_id':protection.order_id,'exit_id':name,'entry_fill_id':fill.fill_id,'quantity':protection.quantity},index,time)
        return True

    def execute_protections(self,index,time,opening,highest,lowest):
        candidates=[]
        for order in self.protections.values():
            if order.target_kind!='fill':continue
            if order.created_bar>=index:raise ValueError('Protection cannot fill on its creation bar.')
            stop=Decimal(order.intent.stop) if order.intent.stop is not None else None
            limit=Decimal(order.intent.limit) if order.intent.limit is not None else None;long=order.direction==1
            sg=stop is not None and (opening<=stop if long else opening>=stop)
            lg=limit is not None and (opening>=limit if long else opening<=limit)
            st=stop is not None and (lowest<=stop if long else highest>=stop)
            lt=limit is not None and (highest>=limit if long else lowest<=limit)
            role='stop' if sg else 'limit' if lg else 'stop' if st else 'limit' if lt else None
            if role:
                gap=sg or lg;priority=0 if gap else 1 if role=='stop' else 2
                candidates.append((priority,order.sequence,order.intent.exit_id,role,gap,bool(not gap and st and lt)))
        for _,_,name,role,gap,ambiguous in sorted(candidates):
            order=self.protections.pop(name,None)
            if order is None or not self.position or order.target_id!=self.position['fill_id']:continue
            qty=self.quantity(min(self.units(order.quantity),self.units(self.position['quantity'])))
            level=getattr(order.intent,role)
            self.event('protection_trigger',{'order_id':order.order_id,'exit_id':name,'entry_id':order.intent.entry_id,
                'role':role,'level':level,'basis':'open_crossing' if gap else 'ohlc_touch','ambiguous':ambiguous,
                'policy':self.policy.profile_id,'stop':order.intent.stop,'limit':order.intent.limit,'quantity':qty},index,time)
            leg=PartialMarketOrder(digest({'bracket':order.order_id,'role':role}),order.sequence,order.created_bar,order.created_time,
                PartialCloseIntent(order.intent.call_site_id,'close',order.intent.entry_id,0,qty,None),order.target_id)
            self._limit_fill=role=='limit'
            try:self.close_position(leg,self.text(opening if gap else Decimal(level)),index,time,qty)
            finally:self._limit_fill=False
            other='limit' if role=='stop' else 'stop'
            if getattr(order.intent,other) is not None:self.event('cancelled',{'order_id':digest({'bracket':order.order_id,'role':other}),'exit_id':name,'reason':'bracket_peer_filled'},index,time)


    def validate_intent(self,intent):
        if type(intent) is OrderIntent:return super().validate_intent(intent)
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

    def commit(self,intents,index,time):
        try:return self._commit(intents,index,time)
        except BaseException:
            self.phase='failed'
            raise

    def _commit(self,intents,index,time):
        if self.phase!='executing' or index!=self.count or len(intents)>16:raise ValueError('Invalid partial commit boundary.')
        with localcontext() as context:
            context.prec=80;pending={}
            for original in intents:
                self.validate_intent(original)
                intent=PartialProtectionIntent(**asdict(original)) if type(original) is ProtectionIntent else original
                if type(intent) is OrderIntent and intent.kind=='entry':
                    key=('entry',intent.entry_id)
                    if key in pending:
                        old=pending[key];self.cancel_target(old.order_id,index,time,'target_removed')
                        self.event('replaced',{'order_id':old.order_id},index,time)
                    order=PartialMarketOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent)
                    self.order_count+=1;pending[key]=order;self.event('order',asdict(order),index,time)
                elif type(intent) in (OrderIntent,PartialCloseIntent):
                    if not self.position or self.position['entry_id']!=intent.entry_id:
                        self.event('ignored',{'entry_id':intent.entry_id,'reason':'no_matching_position'},index,time);continue
                    units=self.selected(intent,self.position['quantity'])
                    if not units:self.event('ignored',{'entry_id':intent.entry_id,'reason':'zero_after_rounding'},index,time);continue
                    selected=PartialCloseIntent(intent.call_site_id,'close',intent.entry_id,0,self.quantity(units),None)
                    key=('close',intent.entry_id)
                    if key in pending:self.event('replaced',{'order_id':pending[key].order_id},index,time)
                    order=PartialMarketOrder(self.identifier('order',self.order_count),self.order_count,index,time,selected,self.position['fill_id'])
                    self.order_count+=1;pending[key]=order;self.event('order',asdict(order),index,time)
                elif intent.kind in ('cancel','cancel_all'):
                    for key,order in list(pending.items()):
                        if intent.kind=='cancel_all' or key[1]==intent.exit_id:
                            pending.pop(key);self.cancel_target(order.order_id,index,time,'target_removed')
                            self.event('cancelled',{'order_id':order.order_id,'reason':intent.kind},index,time)
                    for name in list(self.protections):
                        if intent.kind=='cancel_all' or name==intent.exit_id:self.cancel_exit(name,index,time,intent.kind)
                    self.event('cancel_command',{'id':intent.exit_id,'all':intent.kind=='cancel_all'},index,time)
                else:
                    self.reserve_exit(intent,pending,index,time)
            self.pending=list(pending.values())
            valid={o.order_id for o in self.pending if o.intent.kind=='entry'}
            for name,order in list(self.protections.items()):
                if order.target_kind=='pending' and order.target_id not in valid:self.cancel_exit(name,index,time,'target_removed')
            self.event('mark',self.statistics(),index,time)
        self.count+=1;self.last_time=time;self.phase='committed';events=self.events;self.events=[];return events

    def reserve_exit(self,intent,pending,index,time):
        entry=pending.get(('entry',intent.entry_id))
        if entry is not None and (self.position is None or entry.intent.direction!=self.position['direction']):
            kind,target,direction,available='pending',entry.order_id,entry.intent.direction,entry.intent.quantity
        elif self.position and self.position['entry_id']==intent.entry_id:
            kind,target,direction,available='fill',self.position['fill_id'],self.position['direction'],self.position['quantity']
        else:
            if entry is None:self.event('ignored',{'exit_id':intent.exit_id,'reason':'no_matching_entry'},index,time);return
            kind,target,direction,available='pending',entry.order_id,entry.intent.direction,entry.intent.quantity
        if intent.stop is not None and intent.limit is not None and not (Decimal(intent.stop)<Decimal(intent.limit) if direction==1 else Decimal(intent.stop)>Decimal(intent.limit)):
            raise ValueError('Invalid long/short partial bracket levels.')
        self.cancel_exit(intent.exit_id,index,time,'modified')
        reserved=sum(self.units(o.quantity) for o in self.protections.values() if o.target_id==target)
        units=min(self.selected(intent,available),max(0,self.units(available)-reserved))
        if not units:self.event('ignored',{'exit_id':intent.exit_id,'reason':'no_unreserved_quantity'},index,time);return
        if len(self.protections)>=16:raise ValueError('At most 16 active partial brackets.')
        order=ReservedExit(self.identifier('order',self.order_count),self.order_count,index,time,intent,kind,target,direction,self.quantity(units))
        self.order_count+=1;self.protections[intent.exit_id]=order;self.event('order',asdict(order),index,time)

    def statistics(self):
        with localcontext() as context:
            context.prec=80
            return self._statistics()

    def _statistics(self):
        value=super().statistics()
        principal=Decimal(self.position['quantity'])*Decimal(self.position['price'])*self.position['direction'] if self.position else Decimal(0)
        equity=self.cash+(Decimal(self.position['quantity'])*self.position['direction']*(self.mark or Decimal(0)) if self.position else Decimal(0))
        value.update(cash_exact=self.text(self.cash),equity_exact=self.text(equity),gross_realized_exact=self.text(self.realized),
            unrealized_exact=self.text(equity-self.cash-principal),allocated_entry_commission=self.text(self.allocated_entry_fees),
            entry_commission=self.text(self.entry_fees),exit_commission=self.text(self.exit_fees),closed_positions=self.closed_positions,
            protections=[{'order_id':o.order_id,'exit_id':o.intent.exit_id,'from_entry':o.intent.entry_id,'stop':o.intent.stop,'limit':o.intent.limit,
                'quantity':o.quantity,'created_bar':o.created_bar,'target_kind':o.target_kind,'target_id':o.target_id} for o in self.protections.values()])
        return value

    def checkpoint(self):
        value=super().checkpoint()
        return {**value,'schema_version':self.STATE_SCHEMA,'protections':[asdict(o) for o in self.protections.values()],
            'entry_count':self.entry_count,'closed_positions':self.closed_positions,'entry_fees':self.text(self.entry_fees),
            'exit_fees':self.text(self.exit_fees),'allocated_entry_fees':self.text(self.allocated_entry_fees)}

    def restore(self,state,*,count,last_time,sites):
        expected=set(self.checkpoint_fields())
        def require(value):
            if not value:raise ValueError('Invalid partial broker checkpoint.')
        require(self.phase=='ready' and type(state) is dict and state.keys()==expected)
        require(state['schema_version']==self.STATE_SCHEMA and state['policy']==asdict(self.policy) and state['identity']==self.identity)
        require(type(state['count']) is int and state['count']==count and type(state['last_time']) is int and state['last_time']==last_time)
        with localcontext() as context:
            context.prec=80
            numbers={}
            for name in ('cash','fees','realized','mark','entry_fees','exit_fees','allocated_entry_fees'):
                raw=state[name]
                try:value=Decimal(raw) if type(raw) is str else None
                except InvalidOperation:raise ValueError('Invalid partial decimal state.')
                require(value is not None and value.is_finite() and abs(value)<=Decimal('1e60') and value.as_tuple().exponent>=-48)
                require(self.text(value)==raw);numbers[name]=value
            for name in ('order_count','fill_count','trade_count','event_count','entry_count','closed_positions'):
                require(type(state[name]) is int and 0<=state[name]<=count*2048)
            require(numbers['mark']>0 and numbers['fees']==numbers['entry_fees']+numbers['exit_fees'])
            require(0<=numbers['allocated_entry_fees']<=numbers['entry_fees'] and numbers['exit_fees']>=0)
            position=state['position'];p=None
            if position is not None:
                fields={'fill_id','order_id','entry_id','direction','quantity','price','commission','bar_index','time',
                    'original_quantity','original_commission','closed_quantity','allocated_commission','closed_slices','entry_sequence','entry_order_sequence'}
                require(type(position) is dict and position.keys()==fields)
                require(type(position['direction']) is int and position['direction'] in (-1,1))
                require(type(position['entry_id']) is str and 0<len(position['entry_id'])<=128)
                for name in ('quantity','price','commission','original_quantity','original_commission','closed_quantity','allocated_commission'):
                    raw=position[name];require(type(raw) is str and decimal_text(raw,positive=name in ('quantity','price','original_quantity'))==raw and Decimal(raw)>=0)
                for name,limit in (('entry_sequence',state['fill_count']),('entry_order_sequence',state['order_count']),('bar_index',count)):
                    require(type(position[name]) is int and 0<=position[name]<limit)
                require(type(position['time']) is int and 0<=position['time']<=last_time)
                require(position['fill_id']==self.identifier('fill',position['entry_sequence']) and position['order_id']==self.identifier('order',position['entry_order_sequence']))
                original=Decimal(position['original_quantity']);remaining=Decimal(position['quantity']);closed=Decimal(position['closed_quantity'])
                require(original==remaining+closed and original%Decimal(self.policy.settings.quantity_step)==remaining%Decimal(self.policy.settings.quantity_step)==0)
                require(type(position['closed_slices']) is int and 0<=position['closed_slices']<=state['trade_count'])
                require((position['closed_slices']==0)==(closed==0))
                require(position['entry_sequence']==state['fill_count']-1-position['closed_slices'])
                fee=self.money(original*Decimal(position['price'])*Decimal(self.policy.commission_percent)/100)
                require(Decimal(position['original_commission'])==fee)
                require(Decimal(position['allocated_commission'])==self.money(fee*closed/original))
                require(Decimal(position['commission'])==fee-Decimal(position['allocated_commission']));p=dict(position)
            require(state['entry_count']==state['closed_positions']+int(p is not None))
            require(state['entry_count']>0 or state['trade_count']==state['fill_count']==0 and numbers['fees']==numbers['realized']==0)
            require(state['trade_count']>=state['closed_positions'] and state['fill_count']==state['entry_count']+state['trade_count'] and state['fill_count']<=2*state['order_count'])
            principal=Decimal(p['quantity'])*Decimal(p['price'])*p['direction'] if p else Decimal(0)
            require(numbers['cash']==Decimal(self.policy.initial_capital)+numbers['realized']-numbers['fees']-principal)
            require(numbers['entry_fees']-numbers['allocated_entry_fees']==(Decimal(p['commission']) if p else 0))
            extra_targets=self.restore_entry_targets(state,count,last_time,sites)
            pending=[];orders=set();sequences=set();keys=set()
            require(type(state['pending']) is list and len(state['pending'])<=16)
            def boundary(raw):
                require(type(raw['sequence']) is int and 0<=raw['sequence']<state['order_count'] and raw['sequence'] not in sequences)
                require(raw['order_id']==self.identifier('order',raw['sequence']) and raw['order_id'] not in orders)
                require(type(raw['created_bar']) is int and 0<=raw['created_bar']<count and type(raw['created_time']) is int and 0<=raw['created_time']<=last_time)
                sequences.add(raw['sequence']);orders.add(raw['order_id'])
            for order in extra_targets:boundary(asdict(order))
            for raw in state['pending']:
                require(type(raw) is dict and raw.keys()==set(PartialMarketOrder.__dataclass_fields__))
                payload=raw['intent'];require(type(payload) is dict)
                cls=OrderIntent if payload.get('kind')=='entry' else PartialCloseIntent
                require(payload.keys()==set(cls.__dataclass_fields__));intent=cls(**payload);self.validate_intent(intent)
                require(intent.call_site_id in sites and raw['created_bar']==count-1 and raw['created_time']==last_time)
                boundary(raw);key=(intent.kind,intent.entry_id);require(key not in keys);keys.add(key)
                if intent.kind=='entry':require(raw['target_id'] is None)
                else:
                    require(p is not None and raw['target_id']==p['fill_id'] and intent.entry_id==p['entry_id'] and intent.percent is None)
                    require(intent.quantity is not None and Decimal(intent.quantity)>0 and Decimal(intent.quantity)%Decimal(self.policy.settings.quantity_step)==0)
                pending.append(PartialMarketOrder(**{**raw,'intent':intent}))
            protections={};reservations={}
            require(type(state['protections']) is list and len(state['protections'])<=16)
            for raw in state['protections']:
                require(type(raw) is dict and raw.keys()==set(ReservedExit.__dataclass_fields__))
                payload=raw['intent'];require(type(payload) is dict and payload.keys()==set(PartialProtectionIntent.__dataclass_fields__))
                intent=PartialProtectionIntent(**payload);self.validate_intent(intent);require(intent.kind=='exit' and intent.call_site_id in sites and intent.exit_id not in protections)
                boundary(raw);require(type(raw['direction']) is int and raw['direction'] in (-1,1))
                if intent.stop is not None and intent.limit is not None:require(Decimal(intent.stop)<Decimal(intent.limit) if raw['direction']==1 else Decimal(intent.stop)>Decimal(intent.limit))
                if raw['target_kind']=='fill':
                    require(p is not None and raw['target_id']==p['fill_id'] and intent.entry_id==p['entry_id'] and raw['direction']==p['direction']);available=p['quantity']
                else:
                    require(raw['target_kind']=='pending')
                    target=next((o for o in (*pending,*extra_targets) if o.order_id==raw['target_id'] and o.intent.kind=='entry'),None)
                    require(target is not None and target.intent.entry_id==intent.entry_id and target.intent.direction==raw['direction']);available=target.intent.quantity
                qty=raw['quantity'];require(type(qty) is str and decimal_text(qty,positive=True)==qty and Decimal(qty)%Decimal(self.policy.settings.quantity_step)==0)
                reservations[raw['target_id']]=reservations.get(raw['target_id'],0)+self.units(qty)
                require(reservations[raw['target_id']]<=self.units(available))
                protections[intent.exit_id]=ReservedExit(**{**raw,'intent':intent})
            for name,value in numbers.items():setattr(self,name,value)
            for name in ('count','last_time','order_count','fill_count','trade_count','event_count','entry_count','closed_positions'):setattr(self,name,state[name])
            self.position=p;self.pending=pending;self.protections=protections;self.phase='committed'
            self.restore_entry_targets_commit(extra_targets)

    def restore_entry_targets(self,state,count,last_time,sites):return ()

    def restore_entry_targets_commit(self,targets):pass

    @staticmethod
    def checkpoint_fields():
        return ('schema_version','policy','identity','count','last_time','cash','fees','realized','mark','position','pending',
            'order_count','fill_count','trade_count','event_count','protections','entry_count','closed_positions','entry_fees','exit_fees','allocated_entry_fees')
