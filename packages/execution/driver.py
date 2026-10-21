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
    def __init__(self,evaluation,source,contexts=None):
        self.evaluation,self.source=evaluation,source
        self.contexts=contexts
        self.phase="ready"

    def __getattr__(self,name):return getattr(self.evaluation,name)

    def checkpoint(self):
        primary=self.evaluation.checkpoint()
        if self.contexts is None:return primary
        from packages.execution.contexts import SCHEMA
        return {"schema_version":SCHEMA,"count":self.count,"context_id":self.context_id,"last_time":self.last_time,
            "inputs_sha256":self.contexts.inputs_sha,"primary":primary,"groups":self.contexts.checkpoint()}

    def finish(self,data):
        result=self.evaluation.finish(data)
        return self.contexts.decorate(result) if self.contexts is not None else result

    def finish_stream(self,data):
        result=self.evaluation.finish_stream(data)
        return self.contexts.decorate_summary(result) if self.contexts is not None else result

    def restore(self,state):
        if type(state) is not dict or type(state.get("count")) is not int or type(state.get("last_time")) is not int:
            raise ValueError("Invalid checkpoint cursor field types.")
        if state["context_id"]!=self.source.identity.context_id or not 0<state["count"]<=self.source.identity.count:
            raise ValueError("Runtime checkpoint belongs to another input cursor.")
        if hasattr(self.source,"verify_cursor"):
            self.source.verify_cursor(state["count"],state["last_time"])
        try:
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
                self.phase="executing"
                if self.contexts is not None:self.evaluation.request_values=self.contexts.advance(bar,cancelled)
                evaluation.step(BarFrame(self.source.identity.context_id,evaluation.count,bar.time,
                    bar.close,bar.open,bar.high,bar.low,bar.volume))
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
