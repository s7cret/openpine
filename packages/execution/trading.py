"""Connect the pure broker to runtime commands and a bounded persistence port."""

from dataclasses import asdict,fields,replace
from packages.contracts.trading import StrategyRunResult
from packages.contracts import Diagnostic,OpenPineError
from packages.contracts.strategy_ir import strategy_nodes
from packages.simulation.broker import Broker
from packages.runtime.engine import program_hash


class TradingExecution:
    def __init__(self,evaluation,policy):
        self.evaluation=evaluation;self.broker=Broker(policy,program_hash(evaluation.program))
        self.sites={node.call_site_id for node in strategy_nodes(evaluation.program)}
        self.events=[];self.sink=evaluation.output_sink
        if self.sink is not None:self.sink.enable_simulation()

    def begin(self,bar):
        self.evaluation.order_intents=[]
        try:self.evaluation.strategy_values=self.broker.begin(self.evaluation.count,bar.time,bar.open,bar.close)
        except ValueError as error:raise OpenPineError(Diagnostic('INPUT_INVALID','STRATEGY_BARS',str(error),
            language_version=self.evaluation.program.identity.language_version)) from error

    def commit(self,bar):
        intents=[replace(intent,quantity=intent.quantity or self.broker.policy.default_quantity) if intent.kind=='entry' else intent
            for intent in self.evaluation.order_intents]
        try:events=self.broker.commit(intents,self.evaluation.count-1,bar.time)
        except ValueError as error:raise OpenPineError(Diagnostic('RUNTIME_ERROR','ORDER_QUANTITY',str(error),
            language_version=self.evaluation.program.identity.language_version)) from error
        if self.sink is None:self.events.extend(events)
        else:self.sink.simulation_emit(events)
        self.evaluation.order_intents=[]

    def summary(self):
        return {'schema_version':'openpine.simulation.1','policy':asdict(self.broker.policy),
            'statistics':self.broker.statistics(),'position':self.broker.position,
            'pending_orders':[asdict(order) for order in self.broker.pending]}

    def decorate(self,result):
        values={f.name:getattr(result,f.name) for f in fields(result) if f.init}
        return StrategyRunResult(**values,simulation={**self.summary(),'events':tuple(self.events)})

    def checkpoint(self):return {'broker':self.broker.checkpoint(),'events':self.events if self.sink is None else None}

    def restore(self,value,count,last_time):
        if type(value) is not dict or value.keys()!={'broker','events'}:raise ValueError('Invalid simulation checkpoint fields.')
        self.broker.restore(value['broker'],count=count,last_time=last_time,sites=self.sites)
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
