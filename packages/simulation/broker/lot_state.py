"""Validate an entire lot checkpoint before assigning any destination fields."""
from dataclasses import asdict
from decimal import Decimal,InvalidOperation,localcontext
from packages.contracts.lots import LotTarget,LotCloseOrder,LotProtection
from packages.contracts.pending_entries import EntryIntent,EntryOrder
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.contracts.trading import decimal_text
from packages.simulation.ledger.lots import LotLedger


def restore(broker,state,*,count,last_time,sites):
    def require(ok):
        if not ok:raise ValueError('Invalid lot broker checkpoint.')
    fields={'schema_version','policy','identity','count','last_time','cash','fees','realized','mark','position','ledger','entry_orders',
        'pending','protections','order_count','fill_count','trade_count','event_count','closed_positions'}
    if broker.groups:fields.add('groups')
    require(broker.phase=='ready' and type(state) is dict and state.keys()==fields)
    require(state['schema_version']==broker.STATE_SCHEMA and state['policy']==asdict(broker.policy) and state['identity']==broker.identity)
    require(type(state['count']) is int and type(count) is int and state['count']==count and count>0 and type(state['last_time']) is int and state['last_time']==last_time and last_time>=0)
    try:
        with localcontext() as context:
            context.prec=80
            numbers={}
            for name in ('cash','fees','realized','mark'):
                raw=state[name];require(type(raw) is str);n=Decimal(raw)
                require(n.is_finite() and abs(n)<=Decimal('1e60') and n.as_tuple().exponent>=-48 and broker.text(n)==raw);numbers[name]=n
            require(numbers['mark']>0 and numbers['fees']>=0)
            for name in ('order_count','fill_count','trade_count','event_count','closed_positions'):
                require(type(state[name]) is int and 0<=state[name]<=count*4096)
            require(state['order_count']<=16*count and state['event_count']>=count+state['order_count']+state['fill_count']+state['trade_count'])
            ledger=LotLedger.restore(state['ledger'],broker.policy,count=count,last_time=last_time,fill_count=state['fill_count'],order_count=state['order_count'],trade_count=state['trade_count'],identifier=broker.identifier)
            position=ledger.position();require(state['position']==position)
            principal=Decimal(position['cost_basis'])*position['direction'] if position else Decimal(0)
            require(numbers['cash']==Decimal(broker.policy.initial_capital)+numbers['realized']-numbers['fees']-principal)
            require(numbers['fees']==ledger.entry_fees+ledger.exit_fees)
            require(state['closed_positions']<=ledger.closed_lots and ledger.entry_count<=state['order_count'] and state['fill_count']<=2*state['order_count'])
            require(ledger.exit_count<=state['order_count'] and (position is not None or ledger.entry_count==0 or state['closed_positions']>0))
            require(ledger.exit_count<=state['trade_count']<=16*ledger.exit_count)
            require(ledger.entry_count>0 or ledger.exit_count==state['trade_count']==0 and numbers['fees']==numbers['realized']==0)
            used_sequences={p['entry_order_sequence'] for p in ledger.lots.values()};orders={p['order_id'] for p in ledger.lots.values()}
            def boundary(raw):
                require(type(raw['sequence']) is int and 0<=raw['sequence']<state['order_count'] and raw['sequence'] not in used_sequences)
                require(raw['order_id']==broker.identifier('order',raw['sequence']) and raw['order_id'] not in orders)
                require(type(raw['created_bar']) is int and 0<=raw['created_bar']<count and type(raw['created_time']) is int and 0<=raw['created_time']<=last_time)
                used_sequences.add(raw['sequence']);orders.add(raw['order_id'])
            require(type(state['entry_orders']) is list and len(state['entry_orders'])<=16)
            entries={};entry_targets={};previous=-1
            for raw in state['entry_orders']:
                require(type(raw) is dict and raw.keys()==set(EntryOrder.__dataclass_fields__))
                payload=raw['intent'];require(type(payload) is dict and payload.keys()==set(EntryIntent.__dataclass_fields__))
                intent=EntryIntent(**payload);broker.validate_intent(intent)
                require(intent.call_site_id in sites and intent.entry_id not in entries and raw['sequence']>previous);previous=raw['sequence'];boundary(raw)
                o=EntryOrder(**{**raw,'intent':intent});activation=(o.activated_bar,o.activated_time,o.activation_basis)
                if activation!=(None,None,None):
                    require(o.order_kind=='stop_limit' and type(o.activated_bar) is int and o.created_bar<o.activated_bar<count)
                    require(type(o.activated_time) is int and o.created_time<o.activated_time<=last_time and o.activation_basis in ('open_crossing','ohlc_touch'))
                if o.order_kind=='market':require(o.created_bar==count-1 and o.created_time==last_time)
                entries[intent.entry_id]=o;entry_targets[o.order_id]=o
            def targets(raw,intent,direction,*,allow_pending,global_close=False):
                require(type(raw) is list and 1<=len(raw)<=16);result=[];seen=set();previous=-1
                for item in raw:
                    require(type(item) is dict and item.keys()==set(LotTarget.__dataclass_fields__))
                    t=LotTarget(**item);require(t.kind in ('lot','pending') and t.target_id not in seen);seen.add(t.target_id)
                    require(type(t.quantity) is str and decimal_text(t.quantity,positive=True)==t.quantity and Decimal(t.quantity)%Decimal(broker.policy.settings.quantity_step)==0)
                    if t.kind=='pending':
                        require(allow_pending and len(raw)==1 and t.target_id in entry_targets);p=entry_targets[t.target_id]
                        require(p.intent.entry_id==intent.entry_id and p.intent.direction==direction and Decimal(t.quantity)<=Decimal(p.intent.quantity))
                    else:
                        require(t.target_id in ledger.lots);p=ledger.lots[t.target_id]
                        require(p['direction']==direction and Decimal(t.quantity)<=Decimal(p['quantity']) and previous<p['entry_sequence']);previous=p['entry_sequence']
                        if broker.policy.close_entries_rule=='ANY' and not global_close:require(p['entry_id']==intent.entry_id)
                    result.append(t)
                return tuple(result)
            pending=[];seen_close=set();previous=-1;close_reserved={}
            require(type(state['pending']) is list and len(state['pending'])<=16)
            for raw in state['pending']:
                require(type(raw) is dict and raw.keys()==set(LotCloseOrder.__dataclass_fields__))
                payload=raw['intent'];require(type(payload) is dict and payload.keys()==set(PartialCloseIntent.__dataclass_fields__))
                intent=PartialCloseIntent(**payload);broker.validate_intent(intent);boundary(raw)
                require(intent.call_site_id in sites and intent.entry_id not in seen_close and intent.percent is None and intent.quantity is not None)
                require(raw['created_bar']==count-1 and raw['created_time']==last_time and raw['sequence']>previous);previous=raw['sequence'];seen_close.add(intent.entry_id)
                global_close=broker.groups is not None and intent.kind=='close_all'
                available=ledger.quantity() if global_close else ledger.quantity(intent.entry_id)
                require(position is not None and available>0)
                ts=targets(raw['targets'],intent,position['direction'],allow_pending=False,global_close=global_close)
                require(Decimal(intent.quantity)==sum(Decimal(t.quantity) for t in ts))
                require(Decimal(intent.quantity)<=available)
                for t in ts:
                    close_reserved[t.target_id]=close_reserved.get(t.target_id,Decimal(0))+Decimal(t.quantity)
                    require(close_reserved[t.target_id]<=Decimal(ledger.lots[t.target_id]['quantity']))
                pending.append(LotCloseOrder(**{**raw,'intent':intent,'targets':ts}))
            protections={};reserved={};previous=-1
            require(type(state['protections']) is list and len(state['protections'])<=16)
            for raw in state['protections']:
                require(type(raw) is dict and raw.keys()==set(LotProtection.__dataclass_fields__))
                payload=raw['intent'];require(type(payload) is dict and payload.keys()==set(PartialProtectionIntent.__dataclass_fields__))
                intent=PartialProtectionIntent(**payload);broker.validate_intent(intent);boundary(raw)
                require(intent.kind=='exit' and intent.call_site_id in sites and intent.exit_id not in protections and raw['sequence']>previous);previous=raw['sequence']
                direction=raw['direction'];require(type(direction) is int and direction in (-1,1))
                require(type(raw['eligible_bar']) is int and raw['created_bar']<raw['eligible_bar']<=count)
                if intent.stop is not None and intent.limit is not None:require(Decimal(intent.stop)<Decimal(intent.limit) if direction==1 else Decimal(intent.stop)>Decimal(intent.limit))
                ts=targets(raw['targets'],intent,direction,allow_pending=True)
                for t in ts:
                    reserved[t.target_id]=reserved.get(t.target_id,Decimal(0))+Decimal(t.quantity)
                    available=ledger.lots[t.target_id]['quantity'] if t.kind=='lot' else entry_targets[t.target_id].intent.quantity
                    require(reserved[t.target_id]<=Decimal(available))
                protections[intent.exit_id]=LotProtection(**{**raw,'intent':intent,'targets':ts})
            group_records=broker.groups.validate_checkpoint(state['groups'],entries,protections,pending,sites) if broker.groups else None
            for name,value in numbers.items():setattr(broker,name,value)
            for name in ('order_count','fill_count','trade_count','event_count','closed_positions','count','last_time'):setattr(broker,name,state[name])
            broker.ledger=ledger;broker.position=position;broker.entries=entries;broker.pending=pending;broker.protections=protections;broker.phase='committed'
            if broker.groups:broker.groups.records=group_records
    except (InvalidOperation,TypeError,KeyError,OverflowError) as error:raise ValueError('Invalid lot checkpoint value.') from error
