"""Pure active-lot ledger; one fill fee, independent per-lot entry-fee residues."""
from dataclasses import asdict
from decimal import Decimal,localcontext
from packages.contracts.trading import Fill,decimal_text
from functools import wraps

def exact(method):
    @wraps(method)
    def invoke(*args,**kwargs):
        with localcontext() as context:
            context.prec=80
            return method(*args,**kwargs)
    return invoke


class LotLedger:
    def __init__(self,settings):
        self.settings=settings;self.lots={};self.entry_count=0;self.exit_count=0;self.closed_lots=0
        self.entry_fees=Decimal(0);self.exit_fees=Decimal(0);self.allocated_entry_fees=Decimal(0)

    @staticmethod
    def text(value):
        if not value:return '0'
        raw=format(value,'f')
        return raw.rstrip('0').rstrip('.') if '.' in raw else raw

    def money(self,value):
        from decimal import ROUND_HALF_EVEN
        return value.quantize(Decimal(1).scaleb(-self.settings.money_decimals),rounding=ROUND_HALF_EVEN)

    def ordered(self,entry_id=None):
        return sorted((p for p in self.lots.values() if entry_id is None or p['entry_id']==entry_id),key=lambda p:p['entry_sequence'])

    @exact
    def quantity(self,entry_id=None):return sum((Decimal(p['quantity']) for p in self.ordered(entry_id)),Decimal(0))

    def position(self):
        if not self.lots:return None
        with localcontext() as c:
            c.prec=80;rows=self.ordered();qty=self.quantity();cost=sum(Decimal(p['quantity'])*Decimal(p['price']) for p in rows)
            return {'direction':rows[0]['direction'],'quantity':self.text(qty),'price':self.text(cost/qty),
                'cost_basis':self.text(cost),'lot_count':len(rows),'entry_ids':list(dict.fromkeys(p['entry_id'] for p in rows))}

    @exact
    def add(self,fill,entry_sequence,order_sequence):
        if type(fill) is not Fill or fill.fill_id in self.lots or self.lots and self.ordered()[0]['direction']!=fill.direction:
            raise ValueError('Lots must have unique fill identities and one direction.')
        if self.quantity()+Decimal(fill.quantity)>Decimal('1e24'):raise ValueError('Aggregate position quantity exceeds 1e24.')
        self.lots[fill.fill_id]={**asdict(fill),'original_quantity':fill.quantity,'original_commission':fill.commission,
            'closed_quantity':'0','allocated_commission':'0','closed_slices':0,'entry_sequence':entry_sequence,'entry_order_sequence':order_sequence}
        self.entry_count+=1;self.entry_fees+=Decimal(fill.commission)

    def clip(self,targets):
        result=[]
        for target in targets:
            lot=self.lots.get(target.target_id)
            if target.kind!='lot' or lot is None:continue
            qty=min(Decimal(target.quantity),Decimal(lot['quantity']))
            if qty>0:result.append((lot,qty))
        return result

    @exact
    def consume(self,fill,targets):
        """Return closed slices; the caller records the single aggregate exit fill."""
        rows=self.clip(targets);total=sum((q for _,q in rows),Decimal(0))
        if type(fill) is not Fill or total<=0 or Decimal(fill.quantity)!=total or len({p['fill_id'] for p,_ in rows})!=len(rows):
            raise ValueError('Exit fill differs from its exact lot allocations.')
        if any(fill.direction!=-p['direction'] for p,_ in rows):raise ValueError('Exit direction differs from its lots.')
        cumulative=Decimal(0);allocated_exit=Decimal(0);slices=[]
        for p,qty in rows:
            closed=Decimal(p['closed_quantity'])+qty;original=Decimal(p['original_quantity']);remaining=original-closed
            entry_allocated=self.money(Decimal(p['original_commission'])*closed/original)
            entry_fee=entry_allocated-Decimal(p['allocated_commission'])
            cumulative+=qty;exit_allocated=Decimal(fill.commission) if cumulative==total else self.money(Decimal(fill.commission)*cumulative/total)
            exit_fee=exit_allocated-allocated_exit;allocated_exit=exit_allocated
            gross=(Decimal(fill.price)-Decimal(p['price']))*qty*p['direction']
            slices.append({'lot_id':p['fill_id'],'entry_fill_id':p['fill_id'],'exit_fill_id':fill.fill_id,'entry_id':p['entry_id'],
                'direction':p['direction'],'quantity':self.text(qty),'entry_price':p['price'],'exit_price':fill.price,
                'gross_profit':self.text(gross),'entry_commission':self.text(entry_fee),'exit_commission':self.text(exit_fee),
                'commission':self.text(entry_fee+exit_fee),'net_profit':self.text(gross-entry_fee-exit_fee),
                'entry_bar':p['bar_index'],'exit_bar':fill.bar_index,'remaining_quantity':self.text(remaining),'lot_closed':remaining==0})
            self.allocated_entry_fees+=entry_fee
            if remaining:
                self.lots[p['fill_id']]={**p,'quantity':self.text(remaining),'commission':self.text(Decimal(p['original_commission'])-entry_allocated),
                    'closed_quantity':self.text(closed),'allocated_commission':self.text(entry_allocated),'closed_slices':p['closed_slices']+1}
            else:self.lots.pop(p['fill_id']);self.closed_lots+=1
        self.exit_count+=1;self.exit_fees+=Decimal(fill.commission)
        return slices

    def checkpoint(self):
        return {'lots':[dict(p) for p in self.ordered()],'entry_count':self.entry_count,'exit_count':self.exit_count,'closed_lots':self.closed_lots,
            'entry_fees':self.text(self.entry_fees),'exit_fees':self.text(self.exit_fees),'allocated_entry_fees':self.text(self.allocated_entry_fees)}

    @classmethod
    @exact
    def restore(cls,value,policy,*,count,last_time,fill_count,order_count,trade_count,identifier):
        new=cls(policy.settings)
        def require(ok):
            if not ok:raise ValueError('Invalid active-lot ledger checkpoint.')
        require(type(value) is dict and value.keys()==new.checkpoint().keys())
        for name in ('entry_count','exit_count','closed_lots'):
            require(type(value[name]) is int and 0<=value[name]<=count*1024);setattr(new,name,value[name])
        for name in ('entry_fees','exit_fees','allocated_entry_fees'):
            raw=value[name];require(type(raw) is str)
            n=Decimal(raw);require(n.is_finite() and n>=0 and n<=Decimal('1e60') and n.as_tuple().exponent>=-48 and cls.text(n)==raw);setattr(new,name,n)
        capacity=16 if policy.profile_id in ('openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1') else max(1,policy.pyramiding)
        require(type(value['lots']) is list and len(value['lots'])<=capacity)
        previous=-1;order_sequences=set();directions=set()
        fields=set(Fill.__dataclass_fields__)|{'original_quantity','original_commission','closed_quantity','allocated_commission','closed_slices','entry_sequence','entry_order_sequence'}
        for p in value['lots']:
            require(type(p) is dict and p.keys()==fields)
            require(type(p['entry_id']) is str and 0<len(p['entry_id'])<=128 and type(p['direction']) is int and p['direction'] in (-1,1));directions.add(p['direction'])
            for name in ('quantity','price','original_quantity','closed_quantity'):
                require(type(p[name]) is str and decimal_text(p[name],positive=name in ('quantity','price','original_quantity'))==p[name] and Decimal(p[name])>=0)
            for name in ('commission','original_commission','allocated_commission'):
                raw=p[name];require(type(raw) is str);n=Decimal(raw)
                require(n.is_finite() and 0<=n<=Decimal('1e60') and n.as_tuple().exponent>=-48 and cls.text(n)==raw)
            require(type(p['entry_sequence']) is int and previous<p['entry_sequence']<fill_count);previous=p['entry_sequence']
            require(type(p['entry_order_sequence']) is int and 0<=p['entry_order_sequence']<order_count and p['entry_order_sequence'] not in order_sequences);order_sequences.add(p['entry_order_sequence'])
            require(p['fill_id']==identifier('fill',p['entry_sequence']) and p['order_id']==identifier('order',p['entry_order_sequence']))
            require(type(p['bar_index']) is int and 0<=p['bar_index']<count and type(p['time']) is int and 0<=p['time']<=last_time)
            original=Decimal(p['original_quantity']);remaining=Decimal(p['quantity']);closed=Decimal(p['closed_quantity']);step=Decimal(policy.settings.quantity_step)
            require(original==remaining+closed and original%step==remaining%step==0)
            require(type(p['closed_slices']) is int and 0<=p['closed_slices']<=trade_count and (p['closed_slices']==0)==(closed==0))
            fee=new.money(original*Decimal(p['price'])*Decimal(policy.commission_percent)/100)
            if policy.profile_id=='openpine.economics-broker.1':
                commission=policy.commission;value=Decimal(commission.value)
                fee=new.money(original*Decimal(p['price'])*value/100 if commission.kind=='percent' else original*value if commission.kind=='cash_per_contract' else value)
                if commission.kind=='cash_per_order' and Decimal(p['original_commission'])==0 and p['entry_sequence']>0:fee=Decimal(0)
            allocated=new.money(fee*closed/original)
            require(Decimal(p['original_commission'])==fee and Decimal(p['allocated_commission'])==allocated and Decimal(p['commission'])==fee-allocated)
            new.lots[p['fill_id']]=dict(p)
        require(len(directions)<=1 and new.quantity()<=Decimal('1e24') and new.entry_count==new.closed_lots+len(new.lots))
        require(fill_count==new.entry_count+new.exit_count and new.exit_count<=trade_count and new.closed_lots<=trade_count)
        require(new.allocated_entry_fees<=new.entry_fees and new.entry_fees-new.allocated_entry_fees==sum((Decimal(p['commission']) for p in new.lots.values()),Decimal(0)))
        return new
