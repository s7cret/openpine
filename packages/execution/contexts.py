"""Deterministic requested-bar scheduling inside one historical worker."""

from dataclasses import asdict,fields,replace
import heapq

from packages.contracts import BarFrame,CallIR,LiteralIR,ProgramIR,ValueType,Diagnostic,OpenPineError
from packages.contracts.contexts import (ContextBinding,ContextIdentity,ContextRunResult,MarketContext,
    RequestIdentity,timeframe_ms)
from packages.contracts.context_ir import request_nodes,request_flags
from packages.contracts.wire import digest

SCHEMA="openpine.context-state.1"


def invalid(message):raise OpenPineError(Diagnostic("INPUT_INVALID","CONTEXT_INPUT",message))


def validate_source(context,source):
    if (context.context_id,context.symbol,context.timeframe)!=(source.identity.context_id,source.identity.symbol,source.identity.timeframe):
        invalid("Context metadata differs from its pinned dataset.")


class ValueSink:
    def __init__(self):self.last=None
    def emit(self,site,title,value_type,value,index,timestamp):self.last=value


def expression_program(program,expression,runtime,request_span=None):
    declaration=program.statements[0]
    if declaration.operation=='simulation.strategy':
        from packages.contracts import ArgumentIR
        from packages.contracts.scopes import site_id
        def call(operation,arguments,span):
            contract=next(c for c in runtime.contracts.values() if c.operation==operation)
            return CallIR(contract.symbol_id,contract.overload_id,contract.assertion_id,contract.signature_sha256,operation,
                tuple(ArgumentIR(pid,arg,'source',i) for i,(pid,arg) in enumerate(zip(contract.parameter_ids,arguments,strict=True))),
                tuple(range(len(arguments))),contract.effects,contract.result_type,site_id('call',span),span)
        declaration=call('harness.indicator',(LiteralIR('',ValueType('string'),declaration.span),
            LiteralIR('',ValueType('string'),declaration.span),LiteralIR(False,ValueType('bool'),declaration.span)),declaration.span)
        plot=call('visual.plot',(expression,LiteralIR('',ValueType('string'),expression.span)),request_span or expression.span)
        return ProgramIR(program.identity,(declaration,plot),language_policy=program.language_policy)
    # A requested expression has no access to chart declaration dependencies.
    # Its private metadata uses literals; the original declaration runs only in
    # the primary evaluation, including any supported user-function arguments.
    defaults={"string":"","bool":False,"int":0,"float":0.0}
    declaration=replace(declaration,arguments=tuple(replace(a,expression=LiteralIR(
        defaults[a.expression.value_type.base],ValueType(a.expression.value_type.base),a.expression.span)) for a in declaration.arguments))
    plot=next(n for n in program.statements if isinstance(n,CallIR) and n.operation=="visual.plot")
    arguments=tuple(replace(a,expression=expression if i==0 else LiteralIR("",ValueType("string"),a.expression.span))
        for i,a in enumerate(plot.arguments))
    return ProgramIR(program.identity,(declaration,replace(plot,arguments=arguments)),language_policy=program.language_policy)


class Group:
    def __init__(self,binding,source,mode,requests,runtime,program):
        self.binding,self.source,self.mode=binding,source,mode
        self.cursor=0;self.iterator=None;self.next=None;self.exhausted=False;self.last_time=None
        self.records={}
        for node in requests:
            sink=ValueSink();evaluation=runtime.prepare(expression_program(program,node.arguments[2].expression,runtime,node.span),output_sink=sink)
            self.records[node.call_site_id]=(node,evaluation,sink)

    @property
    def key(self):return self.binding.context.context_id+":"+self.mode

    def peek(self):
        if self.iterator is None and not self.exhausted:self.iterator=iter(self.source.read_from(self.cursor))
        if self.next is None and not self.exhausted:
            try:self.next=next(self.iterator)
            except StopIteration:self.exhausted=True;self.close()
        return self.next

    def close(self):
        if self.iterator is not None and hasattr(self.iterator,"close"):self.iterator.close()
        self.iterator=None;self.next=None

    def step(self,cancelled):
        bar=self.peek();duration=timeframe_ms(self.binding.context.timeframe)
        if bar.time%duration or self.last_time is not None and bar.time<self.last_time+duration:
            invalid("Requested bars violate the aligned fixed-duration time rule.")
        if cancelled and cancelled():raise OpenPineError(Diagnostic("EXECUTION_CANCELLED","CANCELLED","Execution cancelled."))
        frame=BarFrame(self.source.identity.context_id,self.cursor,bar.time,bar.close,bar.open,bar.high,bar.low,bar.volume)
        for _,evaluation,_ in self.records.values():evaluation.step(frame)
        self.cursor+=1;self.last_time=bar.time;self.next=None

    def values(self,before):
        return {site:sink.last if request_flags(node,evaluation.program.identity.language_version)[0]=="off" or self.cursor!=before else None
            for site,(node,evaluation,sink) in self.records.items()}

    def checkpoint(self):
        return {"cursor":self.cursor,"last_time":self.last_time,"records":{
            site:{"runtime":evaluation.checkpoint() if self.cursor else None,"last":sink.last}
            for site,(_,evaluation,sink) in self.records.items()}}

    def restore(self,state):
        if (type(state) is not dict or state.keys()!={"cursor","last_time","records"}
                or type(state["cursor"]) is not int or not 0<=state["cursor"]<=self.source.identity.count
                or type(state["records"]) is not dict or state["records"].keys()!=self.records.keys()):
            raise ValueError("Invalid requested context cursor/state.")
        cursor=state["cursor"]
        if cursor:
            if type(state["last_time"]) is not int:raise ValueError("Invalid requested timestamp.")
            if hasattr(self.source,"verify_cursor"):self.source.verify_cursor(cursor,state["last_time"])
        elif state["last_time"] is not None:raise ValueError("Empty context has a timestamp.")
        for site,(_,evaluation,sink) in self.records.items():
            row=state["records"][site]
            if type(row) is not dict or row.keys()!={"runtime","last"}:raise ValueError("Invalid requested expression state.")
            if cursor:
                evaluation.restore(row["runtime"])
                if evaluation.count!=cursor or evaluation.context_id!=self.source.identity.context_id or evaluation.last_time!=state["last_time"]:
                    raise ValueError("Requested expression cursor differs from its context.")
                if not evaluation.matches(row["last"],ValueType("float","series")):raise ValueError("Invalid requested last value.")
                sink.last=row["last"]
            elif row["runtime"] is not None or row["last"] is not None:raise ValueError("Empty context has runtime state.")
        self.cursor,self.last_time=cursor,state["last_time"]


class ContextScheduler:
    def __init__(self,program,runtime,primary,source,context_sources):
        if type(primary) is not MarketContext:invalid("Requests need an explicit primary market context.")
        if hasattr(source,"bind_identity"):source.bind_identity()
        validate_source(primary,source)
        self.primary,self.source=primary,source;self.primary_ms=timeframe_ms(primary.timeframe)
        primary_snapshot=getattr(source,"manifest",{}).get("snapshot_id",source.identity.sha256)
        primary_binding=ContextBinding(primary,primary_snapshot)
        self.inputs=[(primary_binding,source),*context_sources]
        if len(self.inputs)>8 or any(type(b) is not ContextBinding for b,_ in self.inputs):invalid("Invalid context input count/type.")
        if len({b.context.context_id for b,_ in self.inputs})!=len(self.inputs) or len({(b.context.symbol,b.context.timeframe) for b,_ in self.inputs})!=len(self.inputs):
            invalid("Ambiguous context bindings.")
        for binding,selected in self.inputs:
            if hasattr(selected,"bind_identity"):selected.bind_identity()
            validate_source(binding.context,selected)
            if hasattr(selected,"manifest") and selected.manifest["snapshot_id"]!=binding.snapshot_id:invalid("Context snapshot reference differs from its source.")
            if hasattr(selected,"manifest") and selected.manifest["provenance"]["provider"]!=binding.context.source:invalid("Context source differs from snapshot provenance.")
            if hasattr(selected,"verify_blocks"):selected.verify_blocks()
        self.identities=tuple(ContextIdentity(b.context,b.snapshot_id,s.identity) for b,s in context_sources)
        self.inputs_sha=digest([{"binding":asdict(b),"dataset":asdict(s.identity)} for b,s in self.inputs])
        available={(b.context.symbol,b.context.timeframe):(b,s) for b,s in self.inputs}
        grouped={};descriptors=[]
        for node in request_nodes(program):
            symbol,timeframe=(node.arguments[i].expression.value for i in (0,1))
            if (symbol,timeframe) not in available:invalid(f"No pinned context for {symbol}, {timeframe}.")
            binding,selected=available[symbol,timeframe];duration=timeframe_ms(timeframe)
            if duration<self.primary_ms or duration%self.primary_ms:invalid("Requested timeframe must be an exact same/higher multiple of the primary timeframe.")
            gaps,mode=request_flags(node,program.identity.language_version)
            grouped.setdefault((binding.context.context_id,mode),(binding,selected,[]))[2].append(node)
            descriptors.append(RequestIdentity(node.call_site_id,binding.context.context_id,gaps,mode,mode=="on"))
        self.requests=tuple(descriptors);self.event_hook=None
        self.groups={}
        for (_,mode),(binding,selected,nodes) in sorted(grouped.items()):
            group=Group(binding,selected,mode,nodes,runtime,program);self.groups[group.key]=group

    def advance(self,bar,cancelled=None):
        if bar.time%self.primary_ms:invalid("Primary bars violate the aligned fixed-duration time rule.")
        before={key:g.cursor for key,g in self.groups.items()};queue=[]
        def enqueue(group):
            candidate=group.peek()
            if candidate is None:return
            event_time=candidate.time+(0 if group.mode=="on" else timeframe_ms(group.binding.context.timeframe))
            if event_time<=(bar.time if group.mode=="on" else bar.time+self.primary_ms):
                heapq.heappush(queue,(event_time,group.key))
        for group in self.groups.values():enqueue(group)
        while queue:
            event_time,key=heapq.heappop(queue);group=self.groups[key]
            group.step(cancelled)
            if self.event_hook:self.event_hook((event_time,key,group.cursor))
            enqueue(group)
        values={}
        for key,group in self.groups.items():values.update(group.values(before[key]))
        return values

    def checkpoint(self):return {key:group.checkpoint() for key,group in self.groups.items()}

    def restore(self,state):
        if type(state) is not dict or state.keys()!=self.groups.keys():raise ValueError("Checkpoint context graph differs.")
        for key,group in self.groups.items():group.restore(state[key])

    def validate_boundary(self,primary_time):
        for group in self.groups.values():
            duration=timeframe_ms(group.binding.context.timeframe)
            offset=0 if group.mode=="on" else duration
            threshold=primary_time if group.mode=="on" else primary_time+self.primary_ms
            if group.last_time is not None and group.last_time+offset>threshold:
                raise ValueError("Checkpoint requested cursor contains an unavailable future bar.")
            next_bar=group.peek()
            if next_bar is not None and next_bar.time+offset<=threshold:
                raise ValueError("Checkpoint requested cursor skipped an available bar.")

    def close(self):
        for group in self.groups.values():group.close()

    def decorate(self,result):
        return ContextRunResult(**{f.name:getattr(result,f.name) for f in fields(result) if f.init},
            primary_context=self.primary,contexts=self.identities,requests=self.requests)

    def decorate_summary(self,summary):
        return {**summary,"schema_version":"openpine.run-summary.4","primary_context":asdict(self.primary),
            "contexts":[asdict(c) for c in self.identities],"requests":[asdict(r) for r in self.requests]}
