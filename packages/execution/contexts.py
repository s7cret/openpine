"""Deterministic requested-bar scheduling inside one historical worker."""
from packages.contracts.trading import STRATEGY_DECLARATIONS

from dataclasses import asdict,fields,replace
import heapq

from packages.contracts import BarFrame,CallIR,LiteralIR,ProgramIR,ValueType,Diagnostic,OpenPineError,ArgumentIR
from packages.contracts.contexts import (ContextBinding,ContextIdentity,ContextRunResult,MarketContext,
    RequestIdentity)
from packages.contracts.calendar import aligned,next_open,requestable
from packages.contracts.context_ir import request_nodes,request_flags,request_expression,missing_request
from packages.contracts.wire import digest

SCHEMA="openpine.context-state.1"


def invalid(message):raise OpenPineError(Diagnostic("INPUT_INVALID","CONTEXT_INPUT",message))


def validate_source(context,source):
    if (context.context_id,context.symbol,context.timeframe)!=(source.identity.context_id,source.identity.symbol,source.identity.timeframe):
        invalid("Context metadata differs from its pinned dataset.")


class ValueSink:
    def __init__(self):self.last=None
    def emit(self,site,title,value_type,value,index,timestamp):self.last=value


def expression_program(program,expression,runtime,request_span=None,*,raw=False):
    from packages.contracts import TupleType,EvaluateIR
    from packages.contracts.scopes import site_id
    def call(operation,arguments,span):
        contract=next(c for c in runtime.contracts.values() if c.operation==operation
            and len(c.parameter_ids)==len(arguments)
            and all(arg.value_type.base in allowed for arg,allowed in zip(arguments,c.parameter_types,strict=True)))
        return CallIR(contract.symbol_id,contract.overload_id,contract.assertion_id,contract.signature_sha256,operation,
            tuple(ArgumentIR(pid,arg,'source',i) for i,(pid,arg) in enumerate(zip(contract.parameter_ids,arguments,strict=True))),
            tuple(range(len(arguments))),contract.effects,contract.result_type,site_id('call',span),span)
    def environment_program(result):
        from packages.contracts.environment import uses_environment
        from packages.contracts.overloads import seal_schema
        return seal_schema(replace(result,schema_version='openpine.typed-ir.10') if uses_environment(result) else result)
    declaration=program.statements[0]
    if declaration.operation in STRATEGY_DECLARATIONS:
        declaration=call('harness.indicator',(LiteralIR('',ValueType('string'),declaration.span),
            LiteralIR('',ValueType('string'),declaration.span),LiteralIR(False,ValueType('bool'),declaration.span)),declaration.span)
    else:
        # Private metadata has no chart declaration dependencies. The original
        # declaration (including helper calls) runs only in the primary context.
        defaults={"string":"","bool":False,"int":0,"float":0.0}
        declaration=replace(declaration,arguments=tuple(replace(a,expression=LiteralIR(
            defaults[a.expression.value_type.base],ValueType(a.expression.value_type.base),a.expression.span)) for a in declaration.arguments))
    inputs,bindings,functions=request_expression(program,expression)
    if raw or isinstance(expression.value_type,TupleType) or expression.value_type.base=='bool':
        result=ProgramIR(program.identity,(declaration,*inputs,EvaluateIR(expression,expression.span)),
            bindings=bindings,functions=functions,language_policy=program.language_policy,
            schema_version='openpine.typed-ir.7' if isinstance(expression.value_type,TupleType) else 'openpine.typed-ir.5')
        from packages.contracts.composite import uses_composite
        from packages.contracts.inputs import definitions
        if uses_composite(result):result=replace(result,schema_version='openpine.typed-ir.7')
        elif definitions(result):result=replace(result,schema_version='openpine.typed-ir.6')
        return environment_program(result)
    plot=call('visual.plot',(expression,LiteralIR('',ValueType('string'),expression.span)),request_span or expression.span)
    from packages.contracts.inputs import definitions
    result=ProgramIR(program.identity,(declaration,*inputs,plot),bindings=bindings,functions=functions,
        language_policy=program.language_policy)
    if definitions(result):result=replace(result,schema_version='openpine.typed-ir.6')
    from packages.contracts.composite import uses_composite
    return environment_program(replace(result,schema_version='openpine.typed-ir.7') if uses_composite(result) else result)


def prepare_expression(program,node,runtime,sink,settings=None,*,raw=False):
    from packages.contracts import TupleType
    private=expression_program(program,node.arguments[2].expression,runtime,node.span,raw=raw)
    evaluation=(runtime.prepare_expression(private,sink,settings=settings) if raw or isinstance(node.value_type,TupleType) or node.value_type.base=='bool'
        else runtime.prepare(private,output_sink=sink,settings=settings))
    evaluation.storage_owner='request '+node.call_site_id
    return evaluation

class Group:
    def __init__(self,binding,source,mode,requests,runtime,program,settings=None,*,raw=False):
        self.binding,self.source,self.mode=binding,source,mode
        self.cursor=0;self.iterator=None;self.next=None;self.exhausted=False;self.last_time=None
        self.records={}
        for node in requests:
            from packages.contracts import TupleType
            sink=ValueSink();evaluation=prepare_expression(program,node,runtime,sink,settings,raw=raw)
            from packages.contracts.environment import uses_environment,source_environment
            if uses_environment(evaluation.program):evaluation.environment=source_environment(source)
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
        bar=self.peek();tf=self.binding.context.timeframe
        if not aligned(bar.time,tf) or self.last_time is not None and bar.time<next_open(self.last_time,tf):
            invalid("Requested bars violate the aligned UTC candle time rule.")
        if cancelled and cancelled():raise OpenPineError(Diagnostic("EXECUTION_CANCELLED","CANCELLED","Execution cancelled."))
        frame=BarFrame(self.source.identity.context_id,self.cursor-getattr(self,'start',0),bar.time,bar.close,bar.open,bar.high,bar.low,bar.volume)
        for _,evaluation,_ in self.records.values():evaluation.step(frame)
        self.cursor+=1;self.last_time=bar.time;self.next=None

    def values(self,before):
        from packages.contracts import TupleType
        result={}
        for site,(node,evaluation,sink) in self.records.items():
            value=sink.last if request_flags(node,evaluation.program.identity.language_version)[0]=='off' or self.cursor!=before else None
            if value is None:value=missing_request(node.value_type,evaluation.program.identity.language_version)
            result[site]=value
        return result

    def checkpoint(self,*,streaming=False):
        return {"cursor":self.cursor,"last_time":self.last_time,"records":{
            site:{"runtime":evaluation.checkpoint(streaming=streaming) if self.cursor else None,"last":sink.last}
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
        for site,(node,evaluation,sink) in self.records.items():
            row=state["records"][site]
            if type(row) is not dict or row.keys()!={"runtime","last"}:raise ValueError("Invalid requested expression state.")
            if cursor:
                evaluation.restore(row["runtime"])
                if evaluation.count!=cursor or evaluation.context_id!=self.source.identity.context_id or evaluation.last_time!=state["last_time"]:
                    raise ValueError("Requested expression cursor differs from its context.")
                from packages.contracts import TupleType
                value=row['last']
                if isinstance(node.value_type,TupleType):
                    if type(value) not in (tuple,list):raise ValueError('Invalid requested tuple encoding.')
                    value=tuple(value)
                if not evaluation.matches(value,node.value_type):raise ValueError("Invalid requested last value.")
                sink.last=value
            elif row["runtime"] is not None or row["last"] is not None:raise ValueError("Empty context has runtime state.")
        self.cursor,self.last_time=cursor,state["last_time"]


class ContextScheduler:
    def __init__(self,program,runtime,primary,source,context_sources,*,settings=None):
        if type(primary) is not MarketContext:invalid("Requests need an explicit primary market context.")
        if hasattr(source,"bind_identity"):source.bind_identity()
        validate_source(primary,source)
        self.primary,self.source=primary,source
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
        from packages.contracts.lower_tf import OPERATION,options,requestable as lower_requestable
        from .lower_contexts import LowerGroup
        grouped={};lower_grouped={};descriptors=[]
        from packages.contracts.prepared_requests import resolved_requests
        for node in resolved_requests(program,settings,primary=primary):
            symbol,timeframe=(node.arguments[i].expression.value for i in (0,1))
            if (symbol,timeframe) not in available:invalid(f"No pinned context for {symbol}, {timeframe}.")
            binding,selected=available[symbol,timeframe]
            if node.operation==OPERATION:
                ignore,count=options(node);valid=lower_requestable(primary.timeframe,timeframe)
                if not valid and not ignore:invalid('Lower requests require a strictly smaller divisible UTC minute interval.')
                lower_grouped.setdefault((binding.context.context_id,count,not valid),(binding,selected,[]))[2].append(node)
                descriptors.append(RequestIdentity(node.call_site_id,binding.context.context_id,'off','off',False))
                continue
            if not requestable(primary.timeframe,timeframe):invalid("Requested timeframe must be a supported same/higher multiple or calendar interval of the primary timeframe.")
            gaps,mode=request_flags(node,program.identity.language_version)
            grouped.setdefault((binding.context.context_id,mode),(binding,selected,[]))[2].append(node)
            descriptors.append(RequestIdentity(node.call_site_id,binding.context.context_id,gaps,mode,mode=="on"))
        self.requests=tuple(descriptors);self.event_hook=None
        self.groups={}
        for (_,mode),(binding,selected,nodes) in sorted(grouped.items()):
            group=Group(binding,selected,mode,nodes,runtime,program,settings);self.groups[group.key]=group
        from .lower_contexts import closed_count
        ends={}
        for (_,count,empty),(binding,selected,nodes) in sorted(lower_grouped.items()):
            if binding.context.context_id not in ends:
                ends[binding.context.context_id]=0 if empty else closed_count(selected,next_open(source.identity.last_ms,primary.timeframe))
            group=LowerGroup(binding,selected,count,empty,nodes,runtime,program,settings,ends[binding.context.context_id]);self.groups[group.key]=group

    def advance(self,bar,cancelled=None):
        if not aligned(bar.time,self.primary.timeframe):invalid("Primary bars violate the aligned UTC candle time rule.")
        return self.advance_available(bar.time,next_open(bar.time,self.primary.timeframe),cancelled)

    def advance_available(self,primary_time,available_at,cancelled=None,*,before=None):
        from .lower_contexts import LowerGroup
        if any(isinstance(g,LowerGroup) for g in self.groups.values()) and not primary_time<=available_at<=next_open(primary_time,self.primary.timeframe):invalid('Invalid primary availability boundary.')
        for group in self.groups.values():
            if isinstance(group,LowerGroup):group.begin(primary_time)
        before={key:g.cursor for key,g in self.groups.items()} if before is None else before
        queue=[]
        def enqueue(group):
            candidate=group.peek()
            if candidate is None:return
            event_time=candidate.time if group.mode=="on" else next_open(candidate.time,group.binding.context.timeframe)
            if event_time<=(primary_time if group.mode=="on" else available_at):
                heapq.heappush(queue,(event_time,group.key))
        for group in self.groups.values():enqueue(group)
        while queue:
            event_time,key=heapq.heappop(queue);group=self.groups[key]
            group.step(cancelled)
            if isinstance(group,LowerGroup):
                group.collect()
                if sum(g.pending_cells for g in self.groups.values() if isinstance(g,LowerGroup))>1000000:invalid('Lower request pending arrays exceed the shared cell budget.')
            if self.event_hook:self.event_hook((event_time,key,group.cursor))
            enqueue(group)
        values={}
        if sum(g.pending_cells for g in self.groups.values() if isinstance(g,LowerGroup))>1000000:invalid('Lower request pending arrays exceed the shared cell budget.')
        for key,group in self.groups.items():
            selected=group.values(before[key]);values.update(selected)
            for site,(node,evaluation,_) in group.records.items():
                if evaluation.observer:evaluation.observer.record(evaluation,'request_visibility',node,value=selected[site],
                    requested_context=group.binding.context.context_id,requested_cursor=group.cursor,requested_time=group.last_time,
                    available_at=primary_time if group.mode=='on' else available_at,lookahead=group.mode)
        return values

    def checkpoint(self,*,streaming=False):return {key:group.checkpoint(streaming=streaming) for key,group in self.groups.items()}

    def restore(self,state):
        if type(state) is not dict or state.keys()!=self.groups.keys():raise ValueError("Checkpoint context graph differs.")
        for key,group in self.groups.items():group.restore(state[key])

    def validate_boundary(self,primary_time):
        self.validate_available(primary_time,next_open(primary_time,self.primary.timeframe))

    def validate_available(self,primary_time,available_at):
        for group in self.groups.values():
            if hasattr(group,'validate_window'):group.validate_window(primary_time,available_at)
            boundary=lambda stamp:stamp if group.mode=='on' else next_open(stamp,group.binding.context.timeframe)
            threshold=primary_time if group.mode=="on" else available_at
            if group.last_time is not None and boundary(group.last_time)>threshold:
                raise ValueError("Checkpoint requested cursor contains an unavailable future bar.")
            next_bar=group.peek()
            if next_bar is not None and boundary(next_bar.time)<=threshold:
                raise ValueError("Checkpoint requested cursor skipped an available bar.")

    def close(self):
        for group in self.groups.values():group.close()

    def decorate(self,result):
        return ContextRunResult(**{f.name:getattr(result,f.name) for f in fields(result) if f.init},
            primary_context=self.primary,contexts=self.identities,requests=self.requests)

    def decorate_summary(self,summary):
        return {**summary,"schema_version":"openpine.run-summary.4","primary_context":asdict(self.primary),
            "contexts":[asdict(c) for c in self.identities],"requests":[asdict(r) for r in self.requests]}
