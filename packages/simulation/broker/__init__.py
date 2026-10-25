"""Single-position, next-open market broker with bounded checkpoint state."""

from dataclasses import asdict
from decimal import Decimal,localcontext,ROUND_HALF_EVEN,InvalidOperation

from packages.contracts.trading import BrokerPolicy,OrderIntent,MarketOrder,Fill,Trade,decimal_text
from packages.contracts.wire import digest


class Broker:
    def __init__(self,policy,identity):
        from packages.contracts.lots import LotBrokerPolicy
        from packages.contracts.order_groups import OrderGroupPolicy
        from packages.contracts.trailing import TrailingPolicy
        from packages.contracts.economics import EconomicsPolicy
        from packages.contracts.risk import RiskPolicy
        if type(policy) not in (BrokerPolicy,LotBrokerPolicy,OrderGroupPolicy,TrailingPolicy,EconomicsPolicy,RiskPolicy):raise ValueError('Invalid broker policy.')
        self.policy,self.identity=policy,identity
        self.cash=Decimal(policy.initial_capital);self.fees=Decimal(0);self.realized=Decimal(0)
        self.position=None;self.pending=[];self.order_count=0;self.fill_count=0;self.trade_count=0;self.event_count=0
        self.count=0;self.last_time=None;self.mark=None;self.phase='ready';self.events=[]

    def money(self,value):
        return value.quantize(Decimal(1).scaleb(-self.policy.settings.money_decimals),rounding=ROUND_HALF_EVEN)

    def text(self,value):return format(value.normalize(),'f') if value else '0'

    def identifier(self,kind,sequence):return digest({'program':self.identity,'kind':kind,'sequence':sequence})

    def event(self,kind,value,index,time):
        if self.policy.profile_id in ('openpine.trailing-broker.1','openpine.economics-broker.1') and len(self.events)>=4096:raise ValueError('Broker bar exceeds its event budget.')
        self.events.append({'sequence':self.event_count,'bar_index':index,'time':time,'kind':kind,'value':value})
        self.event_count+=1

    def price(self,open_price,direction):
        value=Decimal(decimal_text(open_price,positive=True))
        if self.policy.slippage_ticks:value+=direction*Decimal(self.policy.settings.tick_size)*self.policy.slippage_ticks
        if value<=0:raise ValueError('Slippage produces a nonpositive execution price.')
        return value

    def fill(self,order,direction,quantity,open_price,index,time):
        price=self.price(open_price,direction)
        economic=getattr(self,'economics',None)
        already_paid=economic is not None and order.order_id in economic.commissions.paid
        fee=economic.commissions.fee(order,quantity,price) if economic else self.money(abs(quantity*price)*Decimal(self.policy.commission_percent)/100)
        fill=Fill(self.identifier('fill',self.fill_count),order.order_id,order.intent.entry_id,direction,
            self.text(quantity),self.text(price),self.text(fee),index,time)
        self.fill_count+=1;self.fees+=fee
        self.cash-=direction*quantity*price+fee
        if economic:self.event('commission_calculated',{'fill_id':fill.fill_id,'order_id':order.order_id,'kind':self.policy.commission.kind,'policy_value':self.policy.commission.value,'quantity':self.text(quantity),'price':self.text(price),'amount':self.text(fee),'basis':'already_charged_order' if already_paid else 'first_order_fill' if self.policy.commission.kind=='cash_per_order' else 'actual_fill'},index,time)
        self.event('fill',asdict(fill),index,time)
        if getattr(self,'risk',None):self.risk.record_fill(order)
        return fill

    def close_position(self,order,open_price,index,time):
        entry=self.position;quantity=Decimal(entry['quantity']);direction=entry['direction']
        exit=self.fill(order,-direction,quantity,open_price,index,time)
        gross=(Decimal(exit.price)-Decimal(entry['price']))*quantity*direction
        commission=Decimal(entry['commission'])+Decimal(exit.commission)
        trade=Trade(self.identifier('trade',self.trade_count),entry['fill_id'],exit.fill_id,entry['entry_id'],direction,
            entry['quantity'],entry['price'],exit.price,self.text(self.money(gross)),self.text(commission),
            self.text(self.money(gross-commission)),entry['bar_index'],index)
        self.trade_count+=1;self.realized+=gross;self.position=None
        self.event('trade',asdict(trade),index,time)

    def begin(self,index,time,open_price,close_price):
        if self.phase not in ('ready','committed') or type(index) is not int or index!=self.count or type(time) is not int or (self.last_time is not None and time<=self.last_time):
            raise ValueError('Broker bars must be supplied once in chronological order.')
        self.events=[];self.phase='executing'
        with localcontext() as context:
            context.prec=80
            self.mark=Decimal(decimal_text(close_price,positive=True))
            # Confirm both prices even when there are no orders. Close-only data
            # cannot silently stand in for an executable open.
            Decimal(decimal_text(open_price,positive=True))
            for order in self.pending:
                intent=order.intent
                if order.created_bar>=index:raise ValueError('A market order cannot fill on its creation bar.')
                if intent.kind=='close':
                    if self.position is None or self.position['entry_id']!=intent.entry_id:
                        self.event('ignored',{'order_id':order.order_id,'reason':'no_matching_position'},index,time)
                    else:self.close_position(order,open_price,index,time)
                elif self.position is not None and self.position['direction']==intent.direction:
                    self.event('ignored',{'order_id':order.order_id,'reason':'pyramiding_disabled'},index,time)
                else:
                    if self.position is not None:self.close_position(order,open_price,index,time)
                    fill=self.fill(order,intent.direction,Decimal(intent.quantity),open_price,index,time)
                    self.position=asdict(fill)
            self.pending=[]
            return self.views()

    def views(self):
        with localcontext() as context:
            context.prec=80
            quantity=Decimal(self.position['quantity'])*self.position['direction'] if self.position else Decimal(0)
            equity=self.cash+quantity*(self.mark or Decimal(0))
            return {'position_size':float(quantity),'position_avg_price':float(self.position['price']) if self.position else None,
                'equity':float(self.money(equity))}

    def commit(self,intents,index,time):
        if self.phase!='executing' or index!=self.count or len(intents)>16:raise ValueError('Invalid broker commit boundary/command limit.')
        with localcontext() as context:
            context.prec=80
            pending={}
            for intent in intents:
                self.validate_intent(intent)
                if intent.entry_id in pending:
                    self.event('replaced',{'order_id':pending[intent.entry_id].order_id},index,time)
                order=MarketOrder(self.identifier('order',self.order_count),self.order_count,index,time,intent)
                self.order_count+=1;pending[intent.entry_id]=order
                self.event('order',asdict(order),index,time)
            self.pending=list(pending.values())
            self.event('mark',self.statistics(),index,time)
        self.count+=1;self.last_time=time;self.phase='committed'
        events=self.events;self.events=[]
        return events

    def validate_intent(self,intent):
        if (type(intent) is not OrderIntent or type(intent.call_site_id) is not str or not intent.call_site_id
                or type(intent.entry_id) is not str or not 0<len(intent.entry_id)<=128
                or intent.kind not in ('entry','close') or type(intent.direction) is not int
                or (intent.kind=='close' and (intent.direction!=0 or intent.quantity is not None))
                or (intent.kind=='entry' and intent.direction not in (-1,1))):raise ValueError('Invalid order intent.')
        if intent.kind=='entry':
            if type(intent.quantity) is not str or decimal_text(intent.quantity,positive=True)!=intent.quantity:
                raise ValueError('Invalid order quantity.')
            if Decimal(intent.quantity)%Decimal(self.policy.settings.quantity_step):raise ValueError('Order quantity does not match the pinned quantity step.')

    def statistics(self):
        with localcontext() as context:
            context.prec=80
            quantity=Decimal(self.position['quantity'])*self.position['direction'] if self.position else Decimal(0)
            unrealized=quantity*(self.mark-Decimal(self.position['price'])) if self.position else Decimal(0)
            return {'cash':self.text(self.money(self.cash)),'equity':self.text(self.money(self.cash+quantity*(self.mark or Decimal(0)))),
                'position_size':self.text(quantity),'position_avg_price':self.position['price'] if self.position else None,
                'gross_realized':self.text(self.money(self.realized)),'unrealized':self.text(self.money(unrealized)),
                'commission':self.text(self.fees),'orders':self.order_count,'fills':self.fill_count,'trades':self.trade_count}

    def checkpoint(self):
        if self.phase!='committed' or self.events:raise ValueError('Broker checkpoint requires a committed bar.')
        return {'schema_version':'openpine.broker-state.1','policy':asdict(self.policy),'identity':self.identity,
            'count':self.count,'last_time':self.last_time,'cash':self.text(self.cash),'fees':self.text(self.fees),
            'realized':self.text(self.realized),'mark':self.text(self.mark),'position':self.position,
            'pending':[asdict(order) for order in self.pending],
            'order_count':self.order_count,'fill_count':self.fill_count,'trade_count':self.trade_count,'event_count':self.event_count}

    def restore(self,state,*,count,last_time,sites):
        expected={'schema_version','policy','identity','count','last_time','cash','fees','realized','mark','position','pending','order_count','fill_count','trade_count','event_count'}
        if (self.phase!='ready' or type(state) is not dict or state.keys()!=expected or state['schema_version']!='openpine.broker-state.1'
                or state['policy']!=asdict(self.policy) or state['identity']!=self.identity or type(state['count']) is not int
                or state['count']!=count or type(state['last_time']) is not int or state['last_time']!=last_time):
            raise ValueError('Broker checkpoint belongs to another policy/program/cursor.')
        for name in ('cash','fees','realized','mark'):
            value=state[name]
            try:number=Decimal(value) if type(value) is str else None
            except InvalidOperation as error:raise ValueError('Invalid broker decimal state.') from error
            if number is None or not number.is_finite() or abs(number)>Decimal('1e60') or number.as_tuple().exponent < -48:
                raise ValueError('Invalid broker decimal state.')
        for name in ('order_count','fill_count','trade_count','event_count'):
            if type(state[name]) is not int or not 0<=state[name]<=count*100:raise ValueError('Invalid broker counters.')
        if not 0<=state['trade_count']*2<=state['fill_count']<=state['order_count']*2 or Decimal(state['fees'])<0 or Decimal(state['mark'])<=0:
            raise ValueError('Inconsistent broker counters/mark.')
        pending=state['pending']
        if type(pending) is not list or len(pending)>16:raise ValueError('Invalid pending order set.')
        restored=[];ids=set();sequences=set()
        for item in pending:
            if type(item) is not dict or item.keys()!=set(MarketOrder.__dataclass_fields__):raise ValueError('Invalid pending order fields.')
            payload=item['intent']
            if type(payload) is not dict or payload.keys()!=set(OrderIntent.__dataclass_fields__):raise ValueError('Invalid saved intent fields.')
            intent=OrderIntent(**payload);self.validate_intent(intent)
            if (intent.call_site_id not in sites or intent.entry_id in ids or type(item['sequence']) is not int
                    or not 0<=item['sequence']<state['order_count'] or item['sequence'] in sequences
                    or item['order_id']!=self.identifier('order',item['sequence']) or type(item['created_bar']) is not int
                    or item['created_bar']!=count-1 or type(item['created_time']) is not int or item['created_time']!=last_time):
                raise ValueError('Invalid saved order identity/boundary.')
            ids.add(intent.entry_id);sequences.add(item['sequence']);restored.append(MarketOrder(**{**item,'intent':intent}))
        position=state['position']
        if position is not None:
            if (type(position) is not dict or position.keys()!=set(Fill.__dataclass_fields__) or type(position['direction']) is not int
                    or position['direction'] not in (-1,1) or type(position['entry_id']) is not str or not 0<len(position['entry_id'])<=128
                    or type(position['bar_index']) is not int or not 0<=position['bar_index']<count
                    or type(position['time']) is not int or not 0<=position['time']<=last_time):raise ValueError('Invalid saved position.')
            for name in ('quantity','price','commission'):
                if type(position[name]) is not str or decimal_text(position[name],positive=name!='commission')!=position[name]:raise ValueError('Invalid saved position decimal.')
            if Decimal(position['commission'])<0:raise ValueError('Invalid saved entry commission.')
        with localcontext() as context:
            context.prec=80
            if state['fill_count']!=2*state['trade_count']+(1 if position else 0):raise ValueError('Position differs from the fill/trade counters.')
            principal=Decimal(position['quantity'])*Decimal(position['price'])*position['direction'] if position else Decimal(0)
            if Decimal(state['cash'])!=Decimal(self.policy.initial_capital)+Decimal(state['realized'])-Decimal(state['fees'])-principal:
                raise ValueError('Broker cash violates the accounting identity.')
            if position and (position['fill_id']!=self.identifier('fill',state['fill_count']-1)
                    or Decimal(position['commission'])!=self.money(Decimal(position['quantity'])*Decimal(position['price'])*Decimal(self.policy.commission_percent)/100)):
                raise ValueError('Saved position fill identity/fee differs.')
            for name in ('cash','fees','realized','mark'):setattr(self,name,Decimal(state[name]))
        for name in ('order_count','fill_count','trade_count','event_count','count','last_time'):setattr(self,name,state[name])
        self.position=position;self.pending=restored;self.phase='committed'
