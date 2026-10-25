"""Bounded per-reservation price state composed into the existing lot broker."""
from dataclasses import asdict,replace
from decimal import Decimal
from packages.contracts.trailing import TrailingExitIntent,PARAMETERS,validate_distances
from packages.contracts.partial_exits import PartialProtectionIntent
from packages.contracts.trading import decimal_text
from .order_helpers import validate_partial_intent

PRICE_FIELDS=('stop','limit',*PARAMETERS)
STATE_FIELDS=('entry_price','stop','limit','activation','activated_bar','activated_time','extreme','trail_stop','stop_eligible_bar','previous_trail_stop','previous_stop_eligible_bar','extreme_bar','extreme_time','extreme_basis')

class TrailingExits:
    SCHEMA='openpine.trailing-state.1'

    def __init__(self,broker):self.broker=broker;self.records={}

    def validate(self,intent):
        b=self.broker
        if type(intent) is not TrailingExitIntent or intent.kind!='exit':raise ValueError('Invalid extended exit intent.')
        # Reuse the old quantity, ID and absolute-price checks without requiring
        # an absolute leg when the command supplies relative/trailing levels.
        p=PartialProtectionIntent(intent.call_site_id,'exit',intent.entry_id,intent.exit_id,intent.stop or b.policy.settings.tick_size,intent.limit,intent.quantity,intent.percent)
        validate_partial_intent(b,p);b.groups.group(intent.oca_name,'reduce' if intent.oca_name else 'none')
        for name in ('stop','limit','trail_price'):
            raw=getattr(intent,name)
            if raw is not None and (type(raw) is not str or decimal_text(raw,positive=True)!=raw or Decimal(raw)%Decimal(b.policy.settings.tick_size)):
                raise ValueError('Exit prices require positive tick-aligned canonical values.')
        validate_distances(intent)

    def validate_normalized(self,intent):
        validate_partial_intent(self.broker,replace(intent,stop=intent.stop or self.broker.policy.settings.tick_size))

    def command(self,intent,index,time):
        if type(intent) is not TrailingExitIntent:return False
        self.validate(intent);b=self.broker
        normalized=PartialProtectionIntent(intent.call_site_id,'exit',intent.entry_id,intent.exit_id,intent.stop,intent.limit,intent.quantity,intent.percent)
        b.reserve_exit(normalized,index,time,group=('exit',intent.oca_name,'reduce' if intent.oca_name else 'none'),configuration=asdict(intent))
        return True

    def choose(self,absolute,relative,direction,role):
        if absolute is None:return relative
        if relative is None or self.broker.policy.language_version<6:return absolute
        # Profit and activation cross in the entry direction; loss crosses in
        # the opposite direction. Absolute wins a numerical tie.
        earlier=min if (direction==1)==(role!='stop') else max
        return earlier(absolute,relative)

    def state(self,target,configuration,ledger=None):
        ledger=ledger or self.broker.ledger
        result={'target_id':target.target_id,'kind':target.kind,**{n:None for n in STATE_FIELDS}}
        if target.kind=='pending':return result
        lot=ledger.lots[target.target_id];entry=Decimal(lot['price']);direction=lot['direction'];tick=Decimal(self.broker.policy.settings.tick_size)
        def absolute(n):return Decimal(configuration[n]) if configuration[n] is not None else None
        def relative(n,sign):return entry+direction*sign*Decimal(configuration[n])*tick if configuration[n] is not None else None
        values={'stop':self.choose(absolute('stop'),relative('loss',-1),direction,'stop'),
            'limit':self.choose(absolute('limit'),relative('profit',1),direction,'limit'),
            'activation':self.choose(absolute('trail_price'),relative('trail_points',1),direction,'activation')}
        for name,value in values.items():
            if value is not None:
                raw=self.broker.text(value);decimal_text(raw,positive=True)
                if value%tick:raise ValueError('Resolved protective level is not tick aligned.')
                result[name]=raw
        result['entry_price']=lot['price'];return result

    def attach(self,order,configuration=None,previous=None,*,oca_name=''):
        if configuration is None:
            i=order.intent;configuration=asdict(TrailingExitIntent(i.call_site_id,'exit',i.entry_id,i.exit_id,i.stop,i.limit,i.quantity,i.percent,oca_name))
        self.validate(TrailingExitIntent(**configuration));states={}
        keep=previous is not None and all(configuration[n]==previous['configuration'][n] for n in PRICE_FIELDS)
        for target in order.targets:
            state=self.state(target,configuration);old=previous['states'].get(target.target_id) if keep else None
            states[target.target_id]=dict(old) if old and old['kind']==state['kind'] and old['entry_price']==state['entry_price'] else state
        self.records[order.order_id]={'configuration':dict(configuration),'states':states}

    def removed(self,order):self.records.pop(order.order_id,None)

    def retarget(self,old,new):
        record=self.records[old.order_id];record['states']={t.target_id:record['states'][t.target_id] for t in new.targets}

    def bound(self,old,new):
        record=self.records[old.order_id];record['states']={t.target_id:self.state(t,record['configuration']) for t in new.targets}

    def level(self,state,direction,index):
        fixed=Decimal(state['stop']) if state['stop'] is not None else None
        trail=Decimal(state['trail_stop']) if state['trail_stop'] is not None and state['stop_eligible_bar']<=index else None
        if trail is None and state['previous_trail_stop'] is not None and state['previous_stop_eligible_bar']<=index:trail=Decimal(state['previous_trail_stop'])
        if trail is None:return fixed,'stop'
        if fixed is None or (trail>fixed if direction==1 else trail<fixed):return trail,'trailing_stop'
        return fixed,'stop'

    def update(self,order,state,extreme,index,time,basis):
        b=self.broker;record=self.records[order.order_id];first=state['activated_bar'] is None
        if not first and not (extreme>Decimal(state['extreme']) if order.direction==1 else extreme<Decimal(state['extreme'])):return
        previous=state['trail_stop'];previous_eligible=state['stop_eligible_bar'];offset=Decimal(record['configuration']['trail_offset'])*Decimal(b.policy.settings.tick_size)
        stop=extreme-order.direction*offset;decimal_text(b.text(stop),positive=True)
        if stop%Decimal(b.policy.settings.tick_size):raise ValueError('Observed trailing extreme produces an unaligned stop.')
        if first:state['activated_bar']=index;state['activated_time']=time
        state.update(extreme=b.text(extreme),trail_stop=b.text(stop),stop_eligible_bar=index+(basis=='intrabar'),
            previous_trail_stop=previous if basis=='intrabar' else None,previous_stop_eligible_bar=previous_eligible if basis=='intrabar' else None,
            extreme_bar=index,extreme_time=time,extreme_basis=basis)
        b.event('trailing_activated' if first else 'trailing_updated',{'order_id':order.order_id,'exit_id':order.intent.exit_id,'target_id':state['target_id'],
            'activation':state['activation'],'extreme':state['extreme'],'stop':state['trail_stop'],'previous_stop':previous,'effective_from_bar':state['stop_eligible_bar'],'basis':basis},index,time)

    def execute(self,index,time,opening,highest,lowest):
        b=self.broker;batches={}
        for order in sorted(b.protections.values(),key=lambda p:p.sequence):
            if order.eligible_bar>index:continue
            record=self.records[order.order_id];long=order.direction==1
            for target in order.targets:
                if target.kind!='lot':continue
                state=record['states'][target.target_id]
                if state['activation'] is not None and (state['activated_bar'] is not None or (opening>=Decimal(state['activation']) if long else opening<=Decimal(state['activation']))):
                    self.update(order,state,opening,index,time,'open')
                stop,stop_role=self.level(state,order.direction,index);limit=Decimal(state['limit']) if state['limit'] is not None else None
                sg=stop is not None and (opening<=stop if long else opening>=stop);lg=limit is not None and (opening>=limit if long else opening<=limit)
                st=stop is not None and (lowest<=stop if long else highest>=stop);lt=limit is not None and (highest>=limit if long else lowest<=limit)
                role=stop_role if sg else 'limit' if lg else stop_role if st else 'limit' if lt else None
                if role is None:continue
                gap=sg or lg;level=limit if role=='limit' else stop;priority=0 if gap else 2 if role=='limit' else 1
                key=(priority,order.sequence,order.intent.exit_id,order.order_id,role,gap,b.text(level))
                if key not in batches:batches[key]={'ids':[],'ambiguous':False,'target_sequence':b.ledger.lots[target.target_id]['entry_sequence']}
                batches[key]['ids'].append(target.target_id);batches[key]['ambiguous']|=bool(st and lt)
        for key,batch in sorted(batches.items(),key=lambda item:(item[0][0],item[0][1],item[1]['target_sequence'])):
            _,_,name,order_id,role,gap,level=key;order=b.protections.get(name)
            if order is None or order.order_id!=order_id:continue
            targets=tuple(t for t in order.targets if t.target_id in batch['ids']);rows=b.ledger.clip(targets)
            if not rows:continue
            quantity=sum((q for _,q in rows),Decimal(0));b.protections.pop(name)
            b.event('protection_trigger',{'order_id':order_id,'exit_id':name,'entry_id':order.intent.entry_id,'role':role,'level':level,
                'basis':'open_crossing' if gap else 'ohlc_touch','ambiguous':bool(not gap and batch['ambiguous']),'policy':b.policy.profile_id,'targets':[asdict(t) for t in targets]},index,time)
            b._limit_fill=role=='limit'
            try:b.close_targets(order,targets,b.text(opening) if gap else level,index,time)
            finally:b._limit_fill=False
            left=tuple(t for t in order.targets if t.target_id not in batch['ids'] and t.target_id in b.ledger.lots)
            if left:
                new=replace(order,targets=left);b.protections[name]=new;self.retarget(order,new)
            else:self.removed(order)
            b.groups.filled(order,quantity,index,time,retain=bool(left))
            b.event('cancelled',{'order_id':order_id,'exit_id':name,'reason':'bracket_peer_filled','target_ids':list(batch['ids']),'remaining_targets':len(left)},index,time)
        # No high/low from a bar may tighten a stop and then retroactively fill
        # that newly tightened stop on an unknown earlier low/high of that bar.
        for order in sorted(b.protections.values(),key=lambda p:p.sequence):
            if order.eligible_bar>index:continue
            for state in self.records[order.order_id]['states'].values():
                if state['kind']!='lot' or state['activation'] is None:continue
                extreme=highest if order.direction==1 else lowest
                if state['activated_bar'] is not None or (extreme>=Decimal(state['activation']) if order.direction==1 else extreme<=Decimal(state['activation'])):
                    self.update(order,state,extreme,index,time,'intrabar')

    def checkpoint(self):
        orders=sorted(self.broker.protections.values(),key=lambda p:p.sequence)
        if set(self.records)!={p.order_id for p in orders}:raise ValueError('Trailing membership differs from protections.')
        return {'schema_version':self.SCHEMA,'orders':[{'order_id':p.order_id,'configuration':dict(self.records[p.order_id]['configuration']),
            'targets':[dict(self.records[p.order_id]['states'][t.target_id]) for t in p.targets]} for p in orders]}

    def statistics(self):
        b=self.broker;result=[]
        for order in sorted(b.protections.values(),key=lambda p:p.sequence):
            record=self.records[order.order_id];targets=[]
            for target in order.targets:
                state=record['states'][target.target_id];level,role=self.level(state,order.direction,b.count if b.phase=='executing' else b.count-1)
                targets.append({**state,'quantity':target.quantity,'effective_stop':b.text(level) if level is not None else None,'effective_role':role,
                    'status':'pending_entry' if target.kind=='pending' else 'trailing' if state['activated_bar'] is not None else 'waiting_activation' if state['activation'] is not None else 'fixed'})
            result.append({'order_id':order.order_id,'exit_id':order.intent.exit_id,'from_entry':order.intent.entry_id,'configuration':dict(record['configuration']),'targets':targets})
        return result

    def validate_checkpoint(self,value,protections,ledger,sites,count,last_time,group_records):
        def require(ok):
            if not ok:raise ValueError('Invalid per-target trailing checkpoint.')
        require(type(value) is dict and value.keys()=={'schema_version','orders'} and value['schema_version']==self.SCHEMA)
        require(type(value['orders']) is list and len(value['orders'])==len(protections)<=16);records={}
        for raw,order in zip(value['orders'],sorted(protections.values(),key=lambda p:p.sequence),strict=True):
            require(type(raw) is dict and raw.keys()=={'order_id','configuration','targets'} and raw['order_id']==order.order_id)
            cfg=raw['configuration'];require(type(cfg) is dict and cfg.keys()==set(TrailingExitIntent.__dataclass_fields__))
            self.validate(TrailingExitIntent(**cfg));i=order.intent
            normalized=PartialProtectionIntent(cfg['call_site_id'],'exit',cfg['entry_id'],cfg['exit_id'],cfg['stop'],cfg['limit'],cfg['quantity'],cfg['percent'])
            require(normalized==i and cfg['oca_name']==group_records[order.order_id]['name'])
            rule=sites[i.call_site_id]
            if not rule['extended'] and i.stop is not None and i.limit is not None:
                require(Decimal(i.stop)<Decimal(i.limit) if order.direction==1 else Decimal(i.stop)>Decimal(i.limit))
            for name,argument in rule['exit_price_arguments'].items():
                if not argument['dynamic']:require(cfg[name]==(decimal_text(argument['value']) if argument['value'] is not None else None))
            require(type(raw['targets']) is list and len(raw['targets'])==len(order.targets)<=16);states={}
            for state,target in zip(raw['targets'],order.targets,strict=True):
                require(type(state) is dict and state.keys()=={'target_id','kind',*STATE_FIELDS})
                original=self.state(target,cfg,ledger)
                for name in ('target_id','kind','entry_price','stop','limit','activation'):require(state[name]==original[name])
                if state['activated_bar'] is None:
                    require(all(state[n] is None for n in ('activated_time','extreme','trail_stop','stop_eligible_bar','previous_trail_stop','previous_stop_eligible_bar','extreme_bar','extreme_time','extreme_basis')))
                else:
                    require(target.kind=='lot' and state['activation'] is not None)
                    lot=ledger.lots[target.target_id];long=order.direction==1
                    require(type(state['activated_bar']) is int and lot['bar_index']<=state['activated_bar']<count)
                    require(type(state['activated_time']) is int and lot['time']<=state['activated_time']<=last_time)
                    require(type(state['extreme_bar']) is int and state['activated_bar']<=state['extreme_bar']<count)
                    require(type(state['extreme_time']) is int and state['activated_time']<=state['extreme_time']<=last_time)
                    require(state['extreme_basis'] in ('open','intrabar') and type(state['stop_eligible_bar']) is int and state['stop_eligible_bar']==state['extreme_bar']+(state['extreme_basis']=='intrabar'))
                    for name in ('extreme','trail_stop'):
                        v=state[name];require(type(v) is str and decimal_text(v,positive=True)==v and Decimal(v)%Decimal(self.broker.policy.settings.tick_size)==0)
                    extreme=Decimal(state['extreme']);activation=Decimal(state['activation']);require(extreme>=activation if long else extreme<=activation)
                    require(Decimal(state['trail_stop'])==extreme-order.direction*Decimal(cfg['trail_offset'])*Decimal(self.broker.policy.settings.tick_size))
                    if state['previous_trail_stop'] is None:require(state['previous_stop_eligible_bar'] is None)
                    else:
                        raw=state['previous_trail_stop'];require(type(raw) is str and decimal_text(raw,positive=True)==raw and Decimal(raw)%Decimal(self.broker.policy.settings.tick_size)==0)
                        require(state['extreme_basis']=='intrabar' and type(state['previous_stop_eligible_bar']) is int and state['activated_bar']<=state['previous_stop_eligible_bar']<=state['extreme_bar'])
                        require(Decimal(raw)<=Decimal(state['trail_stop']) if long else Decimal(raw)>=Decimal(state['trail_stop']))
                states[target.target_id]=dict(state)
            records[order.order_id]={'configuration':dict(cfg),'states':states}
        return records
