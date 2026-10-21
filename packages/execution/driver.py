"""One historical event loop for memory and streaming execution."""

import itertools
from types import SimpleNamespace

from packages.contracts import BarFrame,Diagnostic,OpenPineError


class MemorySource:
    def __init__(self,data):
        self.data=data
        self.identity=SimpleNamespace(context_id=data.context_id,count=len(data.bars))

    def read_from(self,index):
        return itertools.islice(self.data.bars,index,None)


class HistoricalDriver:
    def __init__(self,evaluation,source):
        self.evaluation,self.source=evaluation,source
        self.phase="ready"

    def restore(self,state):
        if state["context_id"]!=self.source.identity.context_id or not 0<state["count"]<=self.source.identity.count:
            raise ValueError("Runtime checkpoint belongs to another input cursor.")
        if hasattr(self.source,"verify_cursor"):
            self.source.verify_cursor(state["count"],state["last_time"])
        self.evaluation.restore(state)
        self.phase="committed"

    def run(self,*,progress=None,cancelled=None,checkpoint=None,interval=0,pause=None,pause_after=None):
        evaluation=self.evaluation
        iterator=iter(self.source.read_from(evaluation.count))
        try:
            for bar in iterator:
                if cancelled and cancelled():
                    raise OpenPineError(Diagnostic("EXECUTION_CANCELLED","CANCELLED","Execution cancelled."))
                self.phase="executing"
                evaluation.step(BarFrame(self.source.identity.context_id,evaluation.count,bar.time,
                    bar.close,bar.open,bar.high,bar.low,bar.volume))
                self.phase="committed"
                stopping=(pause and pause()) or (pause_after is not None and evaluation.count>=pause_after)
                if checkpoint and (stopping or interval and evaluation.count%interval==0):
                    self.phase="checkpointing"
                    checkpoint(evaluation)
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
