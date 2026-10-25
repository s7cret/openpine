"""Active OCA membership and net-order transitions composed into the lot broker."""
from dataclasses import asdict,replace
from decimal import Decimal
from packages.contracts.order_groups import GroupEntryIntent,GroupExitIntent,CloseAllIntent
from packages.contracts.pending_entries import EntryIntent,EntryOrder
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.contracts.lots import LotTarget,LotCloseOrder
from packages.contracts.trading import BrokerPolicy,OrderIntent,decimal_text
from . import Broker


class OrderGroups:
    SCHEMA='openpine.order-groups-state.1'

    def __init__(self,broker):self.broker=broker;self.records={}

    @staticmethod
    def group(name,kind):
        if type(name) is not str or len(name)>128 or type(kind) is not str or kind not in ('none','cancel','reduce') or kind!='none' and not name:
            raise ValueError('OCA requires a bounded literal name and none/cancel/reduce type.')

    def validate_intent(self,intent):
        b=self.broker
        if type(intent) is GroupEntryIntent:
            if intent.kind not in ('entry','order'):raise ValueError('Invalid grouped entry command.')
            b.validate_intent(EntryIntent(intent.call_site_id,'entry',intent.entry_id,intent.direction,intent.quantity,intent.limit,intent.stop))
            self.group(intent.oca_name,intent.oca_type);return True
        if type(intent) is GroupExitIntent:
            if intent.kind!='exit':raise ValueError('Invalid grouped exit command.')
            b.validate_intent(PartialProtectionIntent(intent.call_site_id,'exit',intent.entry_id,intent.exit_id,intent.stop,intent.limit,intent.quantity,intent.percent))
            self.group(intent.oca_name,'reduce' if intent.oca_name else 'none');return True
        if type(intent) is CloseAllIntent or type(intent) is PartialCloseIntent and intent.kind=='close_all':
            if type(intent.call_site_id) is not str or not intent.call_site_id or intent.kind!='close_all':raise ValueError('Invalid close_all command.')
            if type(intent) is PartialCloseIntent:
                if intent.entry_id!='' or type(intent.direction) is not int or intent.direction!=0 or intent.percent is not None or type(intent.quantity) is not str or decimal_text(intent.quantity,positive=True)!=intent.quantity:
                    raise ValueError('Invalid frozen close_all allocation.')
            return True
        return False

    @staticmethod
    def available(order):
        return Decimal(order.intent.quantity) if type(order) is EntryOrder else sum((Decimal(t.quantity) for t in order.targets),Decimal(0))

    def attach(self,order,command,name='',kind='none'):
        self.group(name,kind)
        if kind!='none':
            names={r['name']:r['type'] for r in self.records.values() if r['type']!='none'}
            if name in names and names[name]!=kind:raise ValueError('Active OCA group name has a different type.')
            if name not in names and len(names)>=16:raise ValueError('At most 16 active OCA groups.')
        self.records[order.order_id]={'command':command,'name':name,'type':kind,'original_quantity':self.broker.text(self.available(order))}

    def removed(self,order):self.records.pop(order.order_id,None)

    def describe(self,order):
        r=self.records.get(order.order_id)
        if r is None:return {}
        return {'command':r['command'],'oca_name':r['name'],'oca_type':r['type'],
            'original_quantity':r['original_quantity'],'remaining_quantity':self.broker.text(self.available(order))}

    def is_net(self,order):return self.records.get(order.order_id,{}).get('command')=='order'

    def command(self,intent,index,time,pending):
        b=self.broker
        if type(intent) is GroupEntryIntent:
            normalized=EntryIntent(intent.call_site_id,'entry',intent.entry_id,intent.direction,intent.quantity,intent.limit,intent.stop)
            b.submit_entry(normalized,index,time,group=(intent.kind,intent.oca_name,intent.oca_type));return True
        if type(intent) is GroupExitIntent:
            normalized=PartialProtectionIntent(intent.call_site_id,'exit',intent.entry_id,intent.exit_id,intent.stop,intent.limit,intent.quantity,intent.percent)
            b.reserve_exit(normalized,index,time,group=('exit',intent.oca_name,'reduce' if intent.oca_name else 'none'));return True
        if type(intent) is CloseAllIntent:
            used={}
            for key,order in pending.items():
                if key is None:continue
                for t in order.targets:used[t.target_id]=used.get(t.target_id,0)+b.units(t.quantity)
            targets=[]
            for lot in b.ledger.ordered():
                n=max(0,b.units(lot['quantity'])-used.get(lot['fill_id'],0))
                if n:targets.append(LotTarget('lot',lot['fill_id'],b.quantity(n)))
            if not targets:b.event('ignored',{'command':'close_all','reason':'no_unreserved_lots'},index,time);return True
            if None in pending:b.event('replaced',{'order_id':pending[None].order_id},index,time)
            frozen=PartialCloseIntent(intent.call_site_id,'close_all','',0,b.text(sum(Decimal(t.quantity) for t in targets)),None)
            order=LotCloseOrder(b.identifier('order',b.order_count),b.order_count,index,time,frozen,tuple(targets));b.order_count+=1
            pending[None]=order;b.event('order',{**b.order_payload(order),'command':'close_all'},index,time);return True
        return False

    def execute_net(self,order,index,time,price,limit,basis):
        b=self.broker;qty=Decimal(order.intent.quantity);opposite=b.position and b.position['direction']!=order.intent.direction
        if not opposite and len(b.ledger.lots)>=16:
            b.cancel_entry(order.intent.entry_id,index,time,'active_lot_limit');b.event('ignored',{'order_id':order.order_id,'reason':'active_lot_limit'},index,time);return False
        b.event('entry_trigger',{**b.describe_entry(order),'basis':basis,'reference_price':b.text(price),
            'protection_eligible_bar':index if basis=='open_crossing' else index+1},index,time)
        left=qty;b._limit_fill=limit;fill=None
        try:
            if opposite:
                closing=min(qty,b.ledger.quantity());targets=[];wanted=b.units(b.text(closing))
                for lot in b.ledger.ordered():
                    n=min(wanted,b.units(lot['quantity']))
                    if n:targets.append(LotTarget('lot',lot['fill_id'],b.quantity(n)));wanted-=n
                    if not wanted:break
                b.close_targets(order,tuple(targets),b.text(price),index,time);left-=closing
            if left:
                fill=Broker.fill(b,order,order.intent.direction,left,b.text(price),index,time)
                b.ledger.add(fill,b.fill_count-1,order.sequence);b.position=b.ledger.position()
        finally:b._limit_fill=False
        b.entries.pop(order.intent.entry_id)
        if fill is not None:b.activate_entry_protections(order,fill,index,time,basis)
        b.shrink(index,time);self.filled(order,qty,index,time)
        return True

    def filled(self,order,quantity,index,time,*,retain=False):
        b=self.broker;r=self.records.get(order.order_id) if retain else self.records.pop(order.order_id,None)
        if r is None or r['type']=='none':return
        peers=[]
        for p in (*b.entries.values(),*b.protections.values()):
            if p.order_id==order.order_id:continue
            meta=self.records.get(p.order_id)
            if meta and (meta['name'],meta['type'])==(r['name'],r['type']):peers.append(p)
        b.event('oca_filled',{'order_id':order.order_id,'oca_name':r['name'],'oca_type':r['type'],
            'quantity':b.text(quantity),'command':r['command']},index,time)
        for p in sorted(peers,key=lambda p:p.sequence):
            current=b.entries.get(p.intent.entry_id) if type(p) is EntryOrder else b.protections.get(p.intent.exit_id)
            if current is None or current.order_id!=p.order_id:continue
            p=current
            before=self.available(p);units=max(0,b.units(b.text(before))-b.units(b.text(quantity)))
            if r['type']=='cancel' or not units:
                if type(p) is EntryOrder:b.cancel_entry(p.intent.entry_id,index,time,'oca_'+r['type'])
                else:b.cancel_exit(p.intent.exit_id,index,time,'oca_'+r['type'])
            else:
                if type(p) is EntryOrder:
                    b.entries[p.intent.entry_id]=replace(p,intent=replace(p.intent,quantity=b.quantity(units)))
                else:
                    targets=[];left=units
                    for t in p.targets:
                        take=min(left,b.units(t.quantity))
                        if take:targets.append(replace(t,quantity=b.quantity(take)));left-=take
                        if not left:break
                    b.protections[p.intent.exit_id]=replace(p,targets=tuple(targets))
                b.event('oca_reduced',{'order_id':p.order_id,'source_order_id':order.order_id,'oca_name':r['name'],'oca_type':'reduce',
                    'previous_quantity':b.text(before),'quantity':b.quantity(units)},index,time)
        b.shrink(index,time)

    def checkpoint(self):
        b=self.broker;members=[];live=sorted((*b.entries.values(),*b.protections.values()),key=lambda p:p.sequence)
        if set(self.records)!={p.order_id for p in live}:raise ValueError('OCA membership differs from active orders.')
        for p in live:members.append({'order_id':p.order_id,**self.records[p.order_id],'remaining_quantity':b.text(self.available(p))})
        return {'schema_version':self.SCHEMA,'members':members}

    def statistics(self):
        members=self.checkpoint()['members'];groups={}
        for r in members:
            if r['type']=='none':continue
            key=(r['name'],r['type']);groups.setdefault(key,[]).append(dict(r))
        return [{'name':name,'type':kind,'members':rows} for (name,kind),rows in sorted(groups.items())]

    def validate_checkpoint(self,value,entries,protections,pending,sites):
        def require(ok):
            if not ok:raise ValueError('Invalid OCA membership checkpoint.')
        require(type(value) is dict and value.keys()=={'schema_version','members'} and value['schema_version']==self.SCHEMA)
        require(type(sites) is dict and type(value['members']) is list and len(value['members'])==len(entries)+len(protections)<=32)
        live=sorted((*entries.values(),*protections.values()),key=lambda p:p.sequence);records={};names={}
        for raw,p in zip(value['members'],live,strict=True):
            require(type(raw) is dict and raw.keys()=={'order_id','command','name','type','original_quantity','remaining_quantity'})
            require(raw['order_id']==p.order_id and p.intent.call_site_id in sites);rule=sites[p.intent.call_site_id]
            require(raw['command']==rule['command'] and raw['name']==rule['oca_name'] and raw['type']==rule['oca_type'])
            require(p.intent.entry_id==rule['entry_id'])
            if type(p) is not EntryOrder:require(p.intent.exit_id==rule['exit_id'])
            self.group(raw['name'],raw['type'])
            original=raw['original_quantity'];require(type(original) is str and decimal_text(original,positive=True)==original)
            require(Decimal(original)%Decimal(self.broker.policy.settings.quantity_step)==0 and Decimal(original)>=self.available(p))
            require(raw['remaining_quantity']==self.broker.text(self.available(p)))
            if raw['type']!='none':
                require(raw['name'] not in names or names[raw['name']]==raw['type']);names[raw['name']]=raw['type']
            records[p.order_id]={key:raw[key] for key in ('command','name','type','original_quantity')}
        require(len(names)<=16)
        for p in pending:
            require(p.intent.call_site_id in sites)
            rule=sites[p.intent.call_site_id]
            require(rule['command']==('close_all' if p.intent.kind=='close_all' else 'close'))
            if p.intent.kind!='close_all':require(rule['entry_id']==p.intent.entry_id)
        return records
