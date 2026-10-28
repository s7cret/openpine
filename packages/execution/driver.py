"""One historical event loop for memory and streaming execution."""

import itertools
from types import SimpleNamespace

from packages.contracts import BarFrame,Diagnostic,OpenPineError


class MemorySource:
    def __init__(self,data):
        self.data=data
        self.identity=SimpleNamespace(context_id=data.context_id,count=len(data.bars),symbol=data.symbol,timeframe=data.timeframe)

    def bind_identity(self):
        from packages.marketdata import data_identity
        from packages.contracts.storage import DatasetIdentity
        identity=data_identity(self.data)
        self.identity=DatasetIdentity(identity.sha256,identity.context_id,identity.symbol,identity.timeframe,
            len(identity.times),identity.times[0],identity.times[-1],"openpine.historical-json.1")

    def read_from(self,index):
        return itertools.islice(self.data.bars,index,None)

    def verify_cursor(self,index,last_time):
        if type(index) is not int or not 0<index<=len(self.data.bars) or self.data.bars[index-1].time!=last_time:
            raise ValueError("Checkpoint timestamp differs from its memory input cursor.")


class HistoricalDriver:
    def __init__(self,evaluation,source,contexts=None,simulation_policy=None,evaluation_window=None,*,trade_history_factory=None,magnifier=None,lower_source=None):
        self.evaluation,self.source=evaluation,source
        self.evaluation_window=evaluation_window
        if evaluation_window is not None:
            from packages.contracts.optimization import evaluation_window as validate_window
            validate_window(evaluation_window)
            identity=source.identity
            if not evaluation_window['warmup_start_ms']<=identity.first_ms<=evaluation_window['score_start_ms']<=identity.last_ms<evaluation_window['end_ms']:
                raise ValueError('Research input lies outside its frozen evaluation window.')
        self.contexts=contexts
        from packages.contracts.environment import uses_environment,source_environment
        if uses_environment(evaluation.program):
            evaluation.environment=source_environment(source)
            from packages.execution.environment import broker_environment
            simulation_policy=broker_environment(evaluation.environment,simulation_policy)
        self.phase="ready"
        from packages.contracts.magnifier import admit
        admit(evaluation.program,magnifier)
        if (magnifier is None)!=(lower_source is None):raise ValueError('Magnifier needs exactly one pinned lower source.')
        if magnifier:
            from .magnifier_feed import identities
            identities(source,lower_source)
            if contexts and any(g.mode=='on' for g in contexts.groups.values()):raise ValueError('Magnifier cannot expose requested candles with lookahead_on before close.')
        self.simulation=None;self.cycle=None;self.magnifier=None
        if simulation_policy is not None:
            from packages.execution.trading import TradingExecution
            if magnifier:
                from .magnifier import MagnifierTrading
                from packages.contracts.observed_broker import promote
                simulation_policy=promote(simulation_policy,evaluation.program.identity.language_version,source.identity.timeframe)
                self.simulation=MagnifierTrading(evaluation,simulation_policy,trade_history_factory=trade_history_factory)
                self.simulation.orders_start_ms=evaluation_window['score_start_ms'] if evaluation_window else None
            else:self.simulation=TradingExecution(evaluation,simulation_policy,orders_start_ms=evaluation_window['score_start_ms'] if evaluation_window else None,trade_history_factory=trade_history_factory)
            from packages.contracts.strategy_cycle import selected
            if magnifier or selected(evaluation.program):
                from .strategy_cycle import StrategyCycle
                self.cycle=StrategyCycle(evaluation,self.simulation,magnifier['cycle_settings'] if magnifier else None,historical=bool(magnifier))
        if magnifier:
            from .magnifier import Magnifier
            self.magnifier=Magnifier(self,lower_source,magnifier)

    def __getattr__(self,name):return getattr(self.evaluation,name)

    def checkpoint(self,*,streaming=False):
        if self.magnifier:return self.magnifier.checkpoint(streaming=streaming)
        primary=self.evaluation.checkpoint(streaming=streaming)
        if self.contexts is not None:
            from packages.execution.contexts import SCHEMA
            primary={"schema_version":SCHEMA,"count":self.count,"context_id":self.context_id,"last_time":self.last_time,
                "inputs_sha256":self.contexts.inputs_sha,"primary":primary,"groups":self.contexts.checkpoint(streaming=streaming)}
        if self.simulation is not None:
            return {'schema_version':'openpine.trading-state.2' if self.cycle else 'openpine.trading-state.1','count':self.count,'context_id':self.context_id,
                'last_time':self.last_time,'runtime':primary,'simulation':self.simulation.checkpoint(),**({'cycle':self.cycle.checkpoint()} if self.cycle else {})}
        return primary

    def output_checkpoint(self,sink):
        if self.magnifier:
            return sink.checkpoint(self.count,simulation_completed=self.simulation.ledger_bars,observations=self.simulation.observations)
        return sink.checkpoint(self.count)

    def finish(self,data):
        result=self.evaluation.finish(data)
        result=self.contexts.decorate(result) if self.contexts is not None else result
        result=self.simulation.decorate(result) if self.simulation is not None else result
        from packages.contracts.inputs import configured_result
        return configured_result(result,self.evaluation.input_settings)

    def finish_stream(self,data):
        result=self.evaluation.finish_stream(data)
        result=self.contexts.decorate_summary(result) if self.contexts is not None else result
        if self.simulation is not None:result.update(schema_version='openpine.run-summary.5',simulation=self.simulation.summary())
        if self.evaluation.input_settings:result.update(schema_version='openpine.run-summary.6',settings=self.evaluation.input_settings)
        if self.evaluation_window is not None:result.update(schema_version='openpine.run-summary.7',evaluation_window=self.evaluation_window)
        return result

    def restore(self,state):
        if self.magnifier:
            try:return self.magnifier.restore(state)
            except BaseException:
                self.evaluation.closed=True
                if self.contexts:self.contexts.close()
                raise
        if type(state) is not dict or type(state.get("count")) is not int or type(state.get("last_time")) is not int:
            raise ValueError("Invalid checkpoint cursor field types.")
        if state["context_id"]!=self.source.identity.context_id or not 0<state["count"]<=self.source.identity.count:
            raise ValueError("Runtime checkpoint belongs to another input cursor.")
        if hasattr(self.source,"verify_cursor"):
            self.source.verify_cursor(state["count"],state["last_time"])
        try:
            simulation=None
            if self.simulation is not None:
                if (state.keys()!=({'schema_version','count','context_id','last_time','runtime','simulation'}|({'cycle'} if self.cycle else set()))
                        or state['schema_version']!=('openpine.trading-state.2' if self.cycle else 'openpine.trading-state.1')
                        or any(state[k]!=state['runtime'][k] for k in ('count','context_id','last_time'))):raise ValueError('Invalid trading checkpoint boundary.')
                simulation=state['simulation'];cycle_state=state.get('cycle');state=state['runtime']
            if self.contexts is not None:
                from packages.execution.contexts import SCHEMA
                if (type(state) is not dict or state.keys()!={"schema_version","count","context_id","last_time","inputs_sha256","primary","groups"}
                        or state["schema_version"]!=SCHEMA or state["inputs_sha256"]!=self.contexts.inputs_sha
                        or any(state[k]!=state["primary"][k] for k in ("count","context_id","last_time"))):
                    raise ValueError("Checkpoint belongs to another context graph/input set.")
                self.contexts.restore(state["groups"])
                self.contexts.validate_boundary(state["last_time"])
                self.evaluation.restore(state["primary"])
            else:self.evaluation.restore(state)
            if self.simulation is not None:self.simulation.restore(simulation,self.count,self.last_time)
            if self.cycle:self.cycle.restore(cycle_state,count=self.count,sequence=self.count-1,active=None,observations=self.count,fill_count=self.simulation.broker.fill_count)
        except BaseException:
            self.evaluation.closed=True
            if self.contexts:self.contexts.close()
            raise
        self.phase="committed"

    def run(self,*,progress=None,cancelled=None,checkpoint=None,interval=0,pause=None,pause_after=None):
        if self.magnifier:return self.magnifier.run(progress=progress,cancelled=cancelled,checkpoint=checkpoint,interval=interval,pause=pause,pause_after=pause_after)
        evaluation=self.evaluation
        evaluation.cancelled=cancelled
        iterator=iter(self.source.read_from(evaluation.count))
        try:
            for bar in iterator:
                if cancelled and cancelled():
                    raise OpenPineError(Diagnostic("EXECUTION_CANCELLED","CANCELLED","Execution cancelled."))
                fills_before=self.simulation.broker.fill_count if self.simulation else 0
                if self.simulation is not None:
                    self.phase='opening';self.simulation.begin(bar)
                self.phase='contexts'
                if self.contexts is not None:self.evaluation.request_values=self.contexts.advance(bar,cancelled)
                self.phase="executing"
                frame=BarFrame(self.source.identity.context_id,evaluation.count,bar.time,bar.close,bar.open,bar.high,bar.low,bar.volume)
                if self.cycle:
                    from packages.contracts.realtime import MarketEvent
                    from packages.contracts.calendar import next_open
                    event=MarketEvent(evaluation.count,frame.context_id,'close',bar.time,next_open(bar.time,self.source.identity.timeframe),0,bar.open,bar.high,bar.low,bar.close,bar.volume)
                    self.cycle.run(frame,event.payload(),confirmed=True,filled=self.simulation.broker.fill_count>fills_before,environment_phase='historical',requests=evaluation.request_values)
                else:
                    evaluation.step(frame)
                    if self.simulation is not None:
                        self.phase='settling';self.simulation.commit(bar)
                self.phase="committed"
                stopping=(pause and pause()) or (pause_after is not None and evaluation.count>=pause_after)
                if checkpoint and (stopping or interval and evaluation.count%interval==0):
                    self.phase="checkpointing"
                    checkpoint(self)
                    self.phase="committed"
                if progress and (evaluation.count%1000==1 or evaluation.count==self.source.identity.count):
                    progress(evaluation.count,self.source.identity.count)
                if stopping:
                    if checkpoint is None:raise ValueError("Pause requires durable checkpoint support.")
                    self.phase="paused"
                    return False
            if evaluation.count!=self.source.identity.count:
                raise ValueError("Historical input ended at another cursor.")
            self.phase="completed"
            return True
        except BaseException:
            self.phase="failed";evaluation.closed=True
            raise
        finally:
            evaluation.cancelled=None
            if hasattr(iterator,"close"):iterator.close()
            if self.contexts is not None:self.contexts.close()
