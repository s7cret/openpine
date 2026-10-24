"""Versioned lot broker: shared crossing rules, composed active-lot ledger."""
from dataclasses import asdict,replace
from decimal import Decimal,localcontext,InvalidOperation
from packages.contracts.lots import PROFILE,LotBrokerPolicy,LotTarget,LotCloseOrder,LotProtection
from packages.contracts.pending_entries import EntryIntent,EntryOrder
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.contracts.protection import ProtectionIntent
from packages.contracts.trading import OrderIntent,decimal_text
from packages.contracts.wire import digest
from packages.simulation.ledger.lots import LotLedger
from . import Broker
from .order_helpers import units,quantity,selected,validate_partial_intent,entry_crossing


class LotBroker(Broker):
    PROFILE_ID=PROFILE
    STATE_SCHEMA='openpine.broker-state.5'
    units=units;quantity=quantity;selected=selected;crossing=entry_crossing
    text=staticmethod(LotLedger.text)

    def __init__(self,policy,identity):
        if type(policy) is not LotBrokerPolicy:raise ValueError('Lot broker requires a complete lot policy.')
        super().__init__(policy,identity)
        self.ledger=LotLedger(policy.settings);self.entries={};self.protections={};self._limit_fill=False;self.closed_positions=0

    def price(self,value,direction):return Decimal(decimal_text(value,positive=True)) if self._limit_fill else super().price(value,direction)

    def validate_intent(self,intent):
        if type(intent) is EntryIntent:
            Broker.validate_intent(self,OrderIntent(intent.call_site_id,intent.kind,intent.entry_id,intent.direction,intent.quantity))
            if intent.kind!='entry':raise ValueError('Conditional entry requires entry kind.')
            for level in (intent.limit,intent.stop):
                if level is not None and (type(level) is not str or decimal_text(level,positive=True)!=level or self.policy.settings.tick_size and Decimal(level)%Decimal(self.policy.settings.tick_size)):
                    raise ValueError('Entry level is not a positive tick-aligned price.')
        else:validate_partial_intent(self,intent)

    def describe_entry(self,order):
        return {**asdict(order),'order_kind':order.order_kind,'status':'limit_active' if order.order_kind=='stop_limit' and order.activated_bar is not None else 'pending'}

    def activate(self,order,index,time,basis):
        order=replace(order,activated_bar=index,activated_time=time,activation_basis=basis);self.entries[order.intent.entry_id]=order
        self.event('entry_activated',{**self.describe_entry(order),'limit_eligible_bar':index if basis=='open_crossing' else index+1},index,time)
        return order

    def cancel_exit(self,name,index,time,reason):
        order=self.protections.pop(name,None)
        if order:self.event('cancelled',{'order_id':order.order_id,'exit_id':name,'reason':reason},index,time)

    def cancel_entry(self,name,index,time,reason):
        order=self.entries.pop(name,None)
        if not order:return
        for key,exit in list(self.protections.items()):
            if any(t.kind=='pending' and t.target_id==order.order_id for t in exit.targets):self.cancel_exit(key,index,time,'target_removed')
        self.event('cancelled',{**self.describe_entry(order),'reason':reason},index,time)

    def reserved(self,exclude=None):
        values={}
        for name,order in self.protections.items():
            if name==exclude:continue
            for t in order.targets:values[t.target_id]=values.get(t.target_id,0)+self.units(t.quantity)
        return values

    def bind_lots(self,entry_id,wanted,*,reserve=False,used=None):
        rows=self.ledger.ordered(None if self.policy.close_entries_rule=='FIFO' else entry_id)
        used=self.reserved() if reserve else used or {};targets=[]
        for lot in rows:
            n=min(wanted,max(0,self.units(lot['quantity'])-used.get(lot['fill_id'],0)))
            if n:targets.append(LotTarget('lot',lot['fill_id'],self.quantity(n)));wanted-=n
            if not wanted:break
        return tuple(targets)

    def shrink(self,index,time):
        used={};pending={o.order_id:o for o in self.entries.values()}
        for name,order in sorted(list(self.protections.items()),key=lambda x:x[1].sequence):
            targets=[]
            for t in order.targets:
                target=self.ledger.lots.get(t.target_id) if t.kind=='lot' else pending.get(t.target_id)
                available=target['quantity'] if t.kind=='lot' and target else target.intent.quantity if target else '0'
                n=min(self.units(t.quantity),max(0,self.units(available)-used.get(t.target_id,0)))
                if n:targets.append(replace(t,quantity=self.quantity(n)));used[t.target_id]=used.get(t.target_id,0)+n
            if not targets:self.cancel_exit(name,index,time,'reservation_exhausted_or_target_closed')
            elif tuple(targets)!=order.targets:
                self.protections[name]=replace(order,targets=tuple(targets))
                self.event('reservation_reduced',{'order_id':order.order_id,'exit_id':name,'targets':[asdict(t) for t in targets]},index,time)

    def close_targets(self,order,targets,price,index,time):
        rows=self.ledger.clip(targets)
        if not rows:return False
        original_total=self.ledger.quantity();qty=sum((q for _,q in rows),Decimal(0));fill=Broker.fill(self,order,-rows[0][0]['direction'],qty,price,index,time)
        slices=self.ledger.consume(fill,targets)
        for value in slices:
            self.realized+=Decimal(value['gross_profit'])
            original_total-=Decimal(value['quantity'])
            self.event('trade',{**value,'trade_id':self.identifier('trade',self.trade_count),'requested_entry_id':order.intent.entry_id,
                'position_remaining_quantity':self.text(original_total),'position_closed':original_total==0},index,time);self.trade_count+=1
        self.position=self.ledger.position()
        if not self.position:self.closed_positions+=1
        self.shrink(index,time)
        return True

    def open_entry(self,order,index,time,price,limit,basis):
        same=self.position and self.position['direction']==order.intent.direction
        if same and len(self.ledger.lots)>=max(1,self.policy.pyramiding):
            self.entries.pop(order.intent.entry_id)
            for name,p in list(self.protections.items()):
                if any(t.kind=='pending' and t.target_id==order.order_id for t in p.targets):self.cancel_exit(name,index,time,'entry_not_filled')
            self.event('ignored',{'order_id':order.order_id,'reason':'pyramiding_limit'},index,time);return False
        self.event('entry_trigger',{**self.describe_entry(order),'basis':basis,'reference_price':self.text(price),
            'protection_eligible_bar':index if basis=='open_crossing' else index+1},index,time)
        self._limit_fill=limit
        try:
            if self.position and not same:
                targets=tuple(LotTarget('lot',p['fill_id'],p['quantity']) for p in self.ledger.ordered())
                self.close_targets(order,targets,self.text(price),index,time)
            fill=Broker.fill(self,order,order.intent.direction,Decimal(order.intent.quantity),self.text(price),index,time)
            self.ledger.add(fill,self.fill_count-1,order.sequence);self.position=self.ledger.position()
        finally:self._limit_fill=False
        self.entries.pop(order.intent.entry_id)
        for name,p in list(self.protections.items()):
            if not any(t.kind=='pending' and t.target_id==order.order_id for t in p.targets):continue
            wanted=sum(self.units(t.quantity) for t in p.targets);self.protections.pop(name)
            targets=self.bind_lots(p.intent.entry_id,wanted,reserve=True)
            if not targets:self.event('cancelled',{'order_id':p.order_id,'exit_id':name,'reason':'no_unreserved_quantity'},index,time);continue
            self.protections[name]=replace(p,targets=targets,eligible_bar=index if basis=='open_crossing' else index+1)
            self.event('protection_activated',{'order_id':p.order_id,'exit_id':name,'entry_fill_id':fill.fill_id,
                'targets':[asdict(t) for t in targets],'eligible_bar':self.protections[name].eligible_bar},index,time)
        return True

    def execute_protections(self,index,time,opening,highest,lowest):
        candidates=[]
        for p in self.protections.values():
            if p.eligible_bar>index or any(t.kind!='lot' for t in p.targets):continue
            stop=Decimal(p.intent.stop) if p.intent.stop is not None else None;limit=Decimal(p.intent.limit) if p.intent.limit is not None else None;long=p.direction==1
            sg=stop is not None and (opening<=stop if long else opening>=stop);lg=limit is not None and (opening>=limit if long else opening<=limit)
            st=stop is not None and (lowest<=stop if long else highest>=stop);lt=limit is not None and (highest>=limit if long else lowest<=limit)
            role='stop' if sg else 'limit' if lg else 'stop' if st else 'limit' if lt else None
            if role:candidates.append((0 if sg or lg else 1 if role=='stop' else 2,p.sequence,p.intent.exit_id,role,sg or lg,st and lt))
        for _,_,name,role,gap,ambiguous in sorted(candidates):
            p=self.protections.pop(name,None)
            if p is None:continue
            level=getattr(p.intent,role)
            self.event('protection_trigger',{'order_id':p.order_id,'exit_id':name,'entry_id':p.intent.entry_id,'role':role,'level':level,
                'basis':'open_crossing' if gap else 'ohlc_touch','ambiguous':bool(not gap and ambiguous),'stop':p.intent.stop,'limit':p.intent.limit,
                'policy':PROFILE,'targets':[asdict(t) for t in p.targets]},index,time)
            self._limit_fill=role=='limit'
            try:self.close_targets(p,p.targets,self.text(opening) if gap else level,index,time)
            finally:self._limit_fill=False
            self.event('cancelled',{'order_id':p.order_id,'exit_id':name,'reason':'bracket_peer_filled'},index,time)

    def begin(self,index,time,open_price,close_price,high=None,low=None):
        try:
            with localcontext() as c:
                c.prec=80
                if self.phase not in ('ready','committed') or type(index) is not int or index!=self.count or type(time) is not int or self.last_time is not None and time<=self.last_time:raise ValueError('Invalid lot broker boundary.')
                opening,highest,lowest,closing=[Decimal(decimal_text(x,positive=True)) for x in (open_price,high,low,close_price)]
                if highest<max(opening,closing) or lowest>min(opening,closing) or lowest>highest:raise ValueError('Lot broker requires consistent positive OHLC.')
                self.events=[];self.phase='executing';self.mark=closing;actions=[]
                for order in sorted(self.entries.values(),key=lambda o:o.sequence):
                    match=self.crossing(order,index,time,opening,highest,lowest,at_open=True)
                    if match:actions.append((order.sequence,'entry',self.entries[order.intent.entry_id],match))
                actions.extend((o.sequence,'close',o,None) for o in self.pending)
                for _,kind,order,match in sorted(actions,key=lambda row:row[0]):
                    if kind=='entry':self.open_entry(order,index,time,*match,'open_crossing')
                    elif not self.close_targets(order,order.targets,open_price,index,time):self.event('ignored',{'order_id':order.order_id,'reason':'no_matching_lots'},index,time)
                self.pending=[];self.shrink(index,time);self.execute_protections(index,time,opening,highest,lowest);filled=False
                for order in sorted(list(self.entries.values()),key=lambda o:o.sequence):
                    match=self.crossing(order,index,time,opening,highest,lowest,at_open=False)
                    if match:
                        if filled:self.event('entry_deferred',{'order_id':order.order_id,'entry_id':order.intent.entry_id,'reason':'unknown_intrabar_entry_order'},index,time)
                        else:filled=self.open_entry(self.entries[order.intent.entry_id],index,time,*match,'ohlc_touch')
                return self.views()
        except BaseException:self.phase='failed';raise

    def reserve_exit(self,intent,index,time):
        self.cancel_exit(intent.exit_id,index,time,'modified')
        matching=self.ledger.quantity(intent.entry_id);entry=self.entries.get(intent.entry_id)
        if matching:
            wanted=self.selected(intent,self.text(matching));targets=self.bind_lots(intent.entry_id,wanted,reserve=True);direction=self.position['direction']
        elif entry:
            available=entry.intent.quantity;reserved=self.reserved().get(entry.order_id,0)
            wanted=min(self.selected(intent,available),max(0,self.units(available)-reserved))
            targets=(LotTarget('pending',entry.order_id,self.quantity(wanted)),) if wanted else ();direction=entry.intent.direction
        else:self.event('ignored',{'exit_id':intent.exit_id,'reason':'no_matching_entry'},index,time);return
        if intent.stop is not None and intent.limit is not None and not (Decimal(intent.stop)<Decimal(intent.limit) if direction==1 else Decimal(intent.stop)>Decimal(intent.limit)):
            raise ValueError('Invalid long/short protective lot bracket.')
        if not targets:self.event('ignored',{'exit_id':intent.exit_id,'reason':'no_unreserved_quantity'},index,time);return
        if len(self.protections)>=16:raise ValueError('At most 16 protective brackets.')
        order=LotProtection(self.identifier('order',self.order_count),self.order_count,index,time,intent,direction,targets,index+1)
        self.order_count+=1;self.protections[intent.exit_id]=order;self.event('order',self.describe_protection(order),index,time)

    def commit(self,intents,index,time):
        try:
            if self.phase!='executing' or index!=self.count or len(intents)>16:raise ValueError('Invalid lot command boundary.')
            with localcontext() as c:
                c.prec=80;pending={}
                for original in intents:
                    self.validate_intent(original);intent=PartialProtectionIntent(**asdict(original)) if type(original) is ProtectionIntent else original
                    if intent.kind=='entry':
                        if type(intent) is OrderIntent:intent=EntryIntent(**asdict(intent))
                        self.cancel_entry(intent.entry_id,index,time,'modified_entry')
                        if len(self.entries)>=16:raise ValueError('At most 16 pending entry IDs.')
                        order=EntryOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent);self.order_count+=1
                        self.entries[intent.entry_id]=order;self.event('order',self.describe_entry(order),index,time)
                    elif intent.kind=='close':
                        total=self.ledger.quantity(intent.entry_id)
                        if not total:self.event('ignored',{'entry_id':intent.entry_id,'reason':'no_matching_lots'},index,time);continue
                        used={}
                        for key,previous in pending.items():
                            if key==intent.entry_id:continue
                            for t in previous.targets:used[t.target_id]=used.get(t.target_id,0)+self.units(t.quantity)
                        targets=self.bind_lots(intent.entry_id,self.selected(intent,self.text(total)),used=used)
                        if not targets:self.event('ignored',{'entry_id':intent.entry_id,'reason':'zero_after_rounding'},index,time);continue
                        key=intent.entry_id
                        if key in pending:self.event('replaced',{'order_id':pending[key].order_id},index,time)
                        frozen=PartialCloseIntent(intent.call_site_id,'close',intent.entry_id,0,self.text(sum(Decimal(t.quantity) for t in targets)),None)
                        order=LotCloseOrder(self.identifier('order',self.order_count),self.order_count,index,time,frozen,targets);self.order_count+=1
                        pending[key]=order;self.event('order',self.order_payload(order),index,time)
                    elif intent.kind in ('cancel','cancel_all'):
                        for name in list(self.entries):
                            if intent.kind=='cancel_all' or name==intent.exit_id:self.cancel_entry(name,index,time,intent.kind)
                        for name in list(pending):
                            if intent.kind=='cancel_all' or name==intent.exit_id:self.event('cancelled',{'order_id':pending.pop(name).order_id,'reason':intent.kind},index,time)
                        for name in list(self.protections):
                            if intent.kind=='cancel_all' or name==intent.exit_id:self.cancel_exit(name,index,time,intent.kind)
                        self.event('cancel_command',{'id':intent.exit_id,'all':intent.kind=='cancel_all'},index,time)
                    else:self.reserve_exit(intent,index,time)
                self.pending=sorted(pending.values(),key=lambda o:o.sequence);self.event('mark',self.statistics(),index,time)
                self.count+=1;self.last_time=time;self.phase='committed';events=self.events;self.events=[];return events
        except BaseException:self.phase='failed';raise

    def describe_protection(self,o):
        return {**self.order_payload(o),'exit_id':o.intent.exit_id,'from_entry':o.intent.entry_id,'stop':o.intent.stop,'limit':o.intent.limit,
            'quantity':self.text(sum(Decimal(t.quantity) for t in o.targets))}

    @staticmethod
    def order_payload(order):return {**asdict(order),'targets':[asdict(t) for t in order.targets]}

    def views(self):
        with localcontext() as c:
            c.prec=80;p=self.ledger.position();q=Decimal(p['quantity'])*p['direction'] if p else Decimal(0)
            return {'position_size':float(q),'position_avg_price':float(p['price']) if p else None,'equity':float(self.money(self.cash+q*(self.mark or Decimal(0))))}

    def statistics(self):
        with localcontext() as c:
            c.prec=80;p=self.ledger.position();q=Decimal(p['quantity'])*p['direction'] if p else Decimal(0);equity=self.cash+q*(self.mark or Decimal(0))
            principal=Decimal(p['cost_basis'])*p['direction'] if p else Decimal(0);unrealized=equity-self.cash-principal
            return {'cash':self.text(self.money(self.cash)),'equity':self.text(self.money(equity)),'position_size':self.text(q),'position_avg_price':p['price'] if p else None,
                'gross_realized':self.text(self.money(self.realized)),'unrealized':self.text(self.money(unrealized)),'commission':self.text(self.fees),
                'orders':self.order_count,'fills':self.fill_count,'trades':self.trade_count,'cash_exact':self.text(self.cash),'equity_exact':self.text(equity),
                'gross_realized_exact':self.text(self.realized),'unrealized_exact':self.text(unrealized),
                'entry_commission':self.text(self.ledger.entry_fees),'exit_commission':self.text(self.ledger.exit_fees),'allocated_entry_commission':self.text(self.ledger.allocated_entry_fees),
                'entries': [self.describe_entry(o) for o in sorted(self.entries.values(),key=lambda o:o.sequence)],
                'protections':[self.describe_protection(o) for o in sorted(self.protections.values(),key=lambda o:o.sequence)],
                'lots':[dict(p) for p in self.ledger.ordered()],'open_lots':len(self.ledger.lots),'closed_lots':self.ledger.closed_lots,'closed_positions':self.closed_positions}

    def checkpoint(self):
        if self.phase!='committed' or self.events:raise ValueError('Lot checkpoint requires committed state.')
        return {'schema_version':self.STATE_SCHEMA,'policy':asdict(self.policy),'identity':self.identity,'count':self.count,'last_time':self.last_time,
            'cash':self.text(self.cash),'fees':self.text(self.fees),'realized':self.text(self.realized),'mark':self.text(self.mark),'position':self.ledger.position(),
            'ledger':self.ledger.checkpoint(),'entry_orders':[asdict(o) for o in sorted(self.entries.values(),key=lambda o:o.sequence)],
            'pending':[self.order_payload(o) for o in self.pending],'protections':[self.order_payload(o) for o in sorted(self.protections.values(),key=lambda o:o.sequence)],
            'order_count':self.order_count,'fill_count':self.fill_count,'trade_count':self.trade_count,'event_count':self.event_count,'closed_positions':self.closed_positions}

    def restore(self,state,*,count,last_time,sites):
        from .lot_state import restore
        return restore(self,state,count=count,last_time=last_time,sites=sites)
