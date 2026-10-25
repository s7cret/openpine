"""Connect the pure broker to runtime commands and a bounded persistence port."""

from dataclasses import asdict,fields,replace
from packages.contracts.trading import StrategyRunResult
from packages.contracts import Diagnostic,OpenPineError
from packages.contracts.strategy_ir import strategy_nodes
from packages.simulation.broker import Broker
from packages.runtime.engine import program_hash


class TradingExecution:
    def __init__(self,evaluation,policy,*,orders_start_ms=None):
        from packages.contracts.protection import PROFILE
        from packages.simulation.broker.protective import ProtectiveBroker
        from packages.contracts.partial_exits import PROFILE as PARTIAL_PROFILE
        from packages.simulation.broker.partial import PartialBroker
        from packages.contracts.pending_entries import PROFILE as PENDING_PROFILE
        from packages.simulation.broker.pending import PendingEntryBroker
        from packages.contracts.lots import PROFILE as LOT_PROFILE
        from packages.simulation.broker.lots import LotBroker
        if policy.profile_id=='openpine.trailing-broker.1' and policy.language_version!=evaluation.program.identity.language_version:
            raise ValueError('Trailing policy belongs to another language version.')
        self.evaluation=evaluation;self.broker=(LotBroker if policy.profile_id in (LOT_PROFILE,'openpine.order-group-broker.1','openpine.trailing-broker.1') else PendingEntryBroker if policy.profile_id==PENDING_PROFILE else PartialBroker if policy.profile_id==PARTIAL_PROFILE else ProtectiveBroker if policy.profile_id==PROFILE else Broker)(policy,program_hash(evaluation.program))
        self.orders_start_ms=orders_start_ms
        self.sites={node.call_site_id for node in strategy_nodes(evaluation.program)}
        if policy.profile_id in ('openpine.order-group-broker.1','openpine.trailing-broker.1'):
            from packages.contracts.strategy_ir import group_site_rules
            self.sites=group_site_rules(evaluation.program)
        self.events=[];self.sink=evaluation.output_sink
        if self.sink is not None:self.sink.enable_simulation()

    def begin(self,bar):
        self.evaluation.order_intents=[]
        try:
            from packages.contracts.protection import PROFILE
            extra={'high':bar.high,'low':bar.low} if self.broker.policy.profile_id in (PROFILE,'openpine.partial-broker.1','openpine.pending-entry-broker.1','openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1') else {}
            self.evaluation.strategy_values=self.broker.begin(self.evaluation.count,bar.time,bar.open,bar.close,**extra)
        except ValueError as error:raise OpenPineError(Diagnostic('INPUT_INVALID','STRATEGY_BARS',str(error),
            language_version=self.evaluation.program.identity.language_version)) from error

    def commit(self,bar):
        intents=[replace(intent,quantity=intent.quantity or self.broker.policy.default_quantity) if intent.kind in ('entry','order') else intent
            for intent in self.evaluation.order_intents]
        if self.orders_start_ms is not None and bar.time<self.orders_start_ms:intents=[]
        try:events=self.broker.commit(intents,self.evaluation.count-1,bar.time)
        except ValueError as error:raise OpenPineError(Diagnostic('RUNTIME_ERROR','ORDER_QUANTITY',str(error),
            language_version=self.evaluation.program.identity.language_version)) from error
        if self.sink is None:self.events.extend(events)
        else:self.sink.simulation_emit(events)
        self.evaluation.order_intents=[]

    def summary(self):
        from packages.contracts.protection import PROFILE
        value={'schema_version':'openpine.simulation.7' if self.broker.policy.profile_id=='openpine.trailing-broker.1' else 'openpine.simulation.6' if self.broker.policy.profile_id in ('openpine.order-group-broker.1','openpine.trailing-broker.1') else 'openpine.simulation.5' if self.broker.policy.profile_id=='openpine.lot-broker.1' else 'openpine.simulation.4' if self.broker.policy.profile_id=='openpine.pending-entry-broker.1' else 'openpine.simulation.3' if self.broker.policy.profile_id=='openpine.partial-broker.1' else 'openpine.simulation.2' if self.broker.policy.profile_id==PROFILE else 'openpine.simulation.1','policy':asdict(self.broker.policy),
            'statistics':self.broker.statistics(),'position':self.broker.position,
            'pending_orders':[asdict(order) for order in self.broker.pending]}
        if self.broker.policy.profile_id==PROFILE:
            from packages.contracts.protection import exit_policy
            value['execution_model']=exit_policy(self.evaluation.program.identity.language_version)
            value['pending_protection']=asdict(self.broker.protection) if self.broker.protection else None
        if self.broker.policy.profile_id=='openpine.partial-broker.1':
            from packages.contracts.partial_exits import partial_policy
            value['execution_model']=partial_policy(self.evaluation.program.identity.language_version)
            value['pending_protections']=[asdict(o) for o in self.broker.protections.values()]
        if self.broker.policy.profile_id=='openpine.pending-entry-broker.1':
            from packages.contracts.pending_entries import entry_policy
            value['execution_model']=entry_policy(self.evaluation.program.identity.language_version)
            value['pending_protections']=[asdict(o) for o in self.broker.protections.values()]
            value['pending_entries']=[asdict(o) for o in sorted(self.broker.entries.values(),key=lambda o:o.sequence)]
        if self.broker.policy.profile_id in ('openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1'):
            from packages.contracts.lots import lot_policy
            value['execution_model']=lot_policy(self.evaluation.program.identity.language_version)
            value['pending_protections']=[self.broker.order_payload(o) for o in sorted(self.broker.protections.values(),key=lambda o:o.sequence)]
            value['pending_entries']=[asdict(o) for o in sorted(self.broker.entries.values(),key=lambda o:o.sequence)]
            value['lots']=[dict(p) for p in self.broker.ledger.ordered()]
            if self.broker.groups:
                from packages.contracts.order_groups import group_policy
                value['execution_model']=group_policy(self.evaluation.program.identity.language_version)
                value['order_groups']=self.broker.groups.statistics()
                value['pending_entries']=[self.broker.describe_entry(o) for o in sorted(self.broker.entries.values(),key=lambda o:o.sequence)]
                value['pending_protections']=[self.broker.describe_protection(o) for o in sorted(self.broker.protections.values(),key=lambda o:o.sequence)]
        if self.broker.policy.profile_id=='openpine.trailing-broker.1':
            from packages.contracts.trailing import trailing_policy
            value['execution_model']=trailing_policy(self.evaluation.program.identity.language_version)
            value['exit_states']=self.broker.exits.statistics()
        return value

    def decorate(self,result):
        values={f.name:getattr(result,f.name) for f in fields(result) if f.init}
        return StrategyRunResult(**values,simulation={**self.summary(),'events':tuple(self.events)})

    def checkpoint(self):return {'broker':self.broker.checkpoint(),'events':self.events if self.sink is None else None}

    def restore(self,value,count,last_time):
        if type(value) is not dict or value.keys()!={'broker','events'}:raise ValueError('Invalid simulation checkpoint fields.')
        self.broker.restore(value['broker'],count=count,last_time=last_time,sites=self.sites)
        if self.orders_start_ms is not None and last_time<self.orders_start_ms and (self.broker.position or self.broker.pending or self.broker.order_count):
            raise ValueError('Warmup broker must not contain orders or positions.')
        if self.sink is None:
            events=value['events']
            if type(events) is not list or len(events)!=self.broker.event_count:raise ValueError('Invalid in-memory ledger prefix.')
            for index,event in enumerate(events):
                if (type(event) is not dict or event.keys()!={'sequence','bar_index','time','kind','value'} or event['sequence']!=index
                        or type(event['bar_index']) is not int or not 0<=event['bar_index']<count):raise ValueError('Invalid in-memory ledger event.')
            if not events or events[-1]['kind']!='mark' or events[-1]['bar_index']!=count-1 or events[-1]['time']!=last_time or events[-1]['value']!=self.broker.statistics():
                raise ValueError('Broker state differs from the last in-memory journal mark.')
            self.events=list(events)
        elif value['events'] is not None:raise ValueError('Streaming broker state must reference the output ledger prefix.')
