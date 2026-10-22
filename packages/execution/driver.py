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
    def __init__(self,evaluation,source,contexts=None,simulation_policy=None):
        self.evaluation,self.source=evaluation,source
        self.contexts=contexts
        self.phase="ready"
        self.simulation=None
        if simulation_policy is not None:
            from packages.execution.trading import TradingExecution
            self.simulation=TradingExecution(evaluation,simulation_policy)

    def __getattr__(self,name):return getattr(self.evaluation,name)

    def checkpoint(self):
        primary=self.evaluation.checkpoint()
        if self.contexts is not None:
            from packages.execution.contexts import SCHEMA
            primary={"schema_version":SCHEMA,"count":self.count,"context_id":self.context_id,"last_time":self.last_time,
                "inputs_sha256":self.contexts.inputs_sha,"primary":primary,"groups":self.contexts.checkpoint()}
        if self.simulation is not None:
            return {'schema_version':'openpine.trading-state.1','count':self.count,'context_id':self.context_id,
                'last_time':self.last_time,'runtime':primary,'simulation':self.simulation.checkpoint()}
        return primary

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
        return result

    def restore(self,state):
        if type(state) is not dict or type(state.get("count")) is not int or type(state.get("last_time")) is not int:
            raise ValueError("Invalid checkpoint cursor field types.")
        if state["context_id"]!=self.source.identity.context_id or not 0<state["count"]<=self.source.identity.count:
            raise ValueError("Runtime checkpoint belongs to another input cursor.")
        if hasattr(self.source,"verify_cursor"):
            self.source.verify_cursor(state["count"],state["last_time"])
        try:
            simulation=None
            if self.simulation is not None:
                if (state.keys()!={'schema_version','count','context_id','last_time','runtime','simulation'}
                        or state['schema_version']!='openpine.trading-state.1'
                        or any(state[k]!=state['runtime'][k] for k in ('count','context_id','last_time'))):raise ValueError('Invalid trading checkpoint boundary.')
                simulation=state['simulation'];state=state['runtime']
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
        except BaseException:
            self.evaluation.closed=True
            if self.contexts:self.contexts.close()
            raise
        self.phase="committed"

    def run(self,*,progress=None,cancelled=None,checkpoint=None,interval=0,pause=None,pause_after=None):
        evaluation=self.evaluation
        iterator=iter(self.source.read_from(evaluation.count))
        try:
            for bar in iterator:
                if cancelled and cancelled():
                    raise OpenPineError(Diagnostic("EXECUTION_CANCELLED","CANCELLED","Execution cancelled."))
                if self.simulation is not None:
                    self.phase='opening';self.simulation.begin(bar)
                self.phase='contexts'
                if self.contexts is not None:self.evaluation.request_values=self.contexts.advance(bar,cancelled)
                self.phase="executing"
                evaluation.step(BarFrame(self.source.identity.context_id,evaluation.count,bar.time,
                    bar.close,bar.open,bar.high,bar.low,bar.volume))
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
            if hasattr(iterator,"close"):iterator.close()
            if self.contexts is not None:self.contexts.close()
