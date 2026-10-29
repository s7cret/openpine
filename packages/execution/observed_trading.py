"""Observation commits reuse the lot broker; Pine still commits only on close."""
from packages.contracts.realtime import MarketEvent
from packages.contracts.observed_broker import PROFILE,policy
from .trading import TradingExecution

class ObservedTrading(TradingExecution):
    def __init__(self,evaluation,selected,*,trade_history_factory=None):
        super().__init__(evaluation,selected,observed=True,trade_history_factory=trade_history_factory)
        self.observations=0;self.last_observation=None

    @property
    def ledger_bars(self):return self.broker.count+(self.broker.phase=='executing')

    def observe(self,event,*,first):
        self.evaluation.order_intents=[]
        if self.broker.performance:self.broker.performance.reads=0
        # A cumulative candle's old open/high/low are not a new tradable path.
        p=event.close
        self.evaluation.strategy_values=self.broker.begin(self.evaluation.count,event.time,p,p,high=p,low=p,continuing=not first)
        self.observations+=1;self.last_observation=event.payload()
        if event.kind!='close':
            self.broker.economics.commissions.prune()
            if self.broker.risk:self.broker.risk.prune()
            self.broker.event('mark',self.broker.statistics(),self.evaluation.count,event.time)
            self.flush(self.broker.events);self.broker.events=[]

    def submit(self,evaluation,event):
        events=self.broker.commit(evaluation.order_intents,self.evaluation.count,event.time,confirmed=False)
        self.flush(events);evaluation.order_intents=[]

    def provenance(self):
        event=self.last_observation
        return {k:event[k] for k in ('sequence','context_id','kind','time','event_time','revision')}|{'price_source':'observed_close','simulation_only':True}

    def flush(self,events):
        tagged=[{**e,'value':{**e['value'],'observation':self.provenance()}} if e['kind']!='mark' else e for e in events]
        if self.evaluation.observer:
            for event in tagged:self.evaluation.observer.record(self.evaluation,'broker',broker_event=event)
        if self.sink is None:self.events.extend(tagged)
        else:self.sink.simulation_emit(tagged)

    def publish_events(self,events):self.flush(events)

    def checkpoint(self):
        return {'schema_version':'openpine.observed-trading-state.1','observations':self.observations,
            'last_observation':self.last_observation,'simulation':{'broker':self.broker.snapshot(),'events':self.events if self.sink is None else None}}

    def restore_observation(self,value,count,last_time,active,*,sequence,context_id,available_at):
        if (type(value) is not dict or value.keys()!={'schema_version','observations','last_observation','simulation'}
                or value['schema_version']!='openpine.observed-trading-state.1' or type(value['observations']) is not int
                or not count+bool(active)<=value['observations']<=sequence+1):raise ValueError('Invalid observed broker checkpoint cursor.')
        event=MarketEvent.from_payload(value['last_observation'])
        if (event.sequence>sequence or event.context_id!=context_id or event.event_time>available_at
                or (active is not None and event.payload()!=active) or (active is None and (event.kind!='close' or event.time!=last_time))):
            raise ValueError('Broker observation differs from primary candle boundary.')
        boundary={'active':active is not None,'bar_time':event.time,'observations':value['observations']}
        super().restore(value['simulation'],count,last_time,observation=boundary)
        self.observations=value['observations'];self.last_observation=event.payload()

    def summary(self):
        return {**super().summary(),'execution_model':{**policy(self.evaluation.program.identity.language_version),**({'calculation':'Pine_on_received_primary_observations','calculation_profile':'openpine.observed-calculation.1'} if getattr(self.broker,'sampled_commands',False) else {})},
            'observed_execution':{'profile_id':PROFILE,'observations':self.observations,
            'last_observation':self.provenance() if self.last_observation else None,'bar_confirmed':self.broker.phase=='committed',
            'broker_committed':self.last_observation is not None,'calculation':'observed_updates' if getattr(self.broker,'sampled_commands',False) else 'confirmed_close_only'}}
