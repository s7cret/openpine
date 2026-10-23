"""Closed runtime-state codec. No pickle, imports or constructors chosen by JSON."""

import math
from dataclasses import asdict,fields,is_dataclass

from packages.contracts import CallIR,StoreIR,UserCallIR,HISTORICAL_PROFILE_ID
from packages.runtime.history import Ring,StorageBudget,LIMIT,CELL_LIMIT
from packages.runtime.series import EMAState,SMASeedEMAState
from packages.runtime.series.basic import SMAState,ChangeState,CrossState
from packages.runtime.series.technical import TECHNICAL_KINDS,TECHNICAL_TYPES,export_technical,restore_technical
from packages.runtime.series.composite import COMPOSITE_KINDS,COMPOSITE_TYPES,export_composite,restore_composite,KIND_OPERATIONS
from packages.contracts.loops import repeated_call_sites, ITERATIONS_PER_BAR

SCHEMA = "openpine.runtime-state.1"


def require(condition,message="Invalid runtime checkpoint."):
    if not condition:
        raise ValueError(message)


def integer(value,lo=0,hi=10_000_000):
    require(type(value) is int and lo<=value<=hi)
    return value


def scalar(value,kind=None,*,wide=False):
    require(value is None or type(value) in (int,float,bool,str))
    if type(value) is float:require(math.isfinite(value))
    if type(value) is int:require(value.bit_length()<=4096 if wide else -(2**63)<=value<=2**63-1)
    if type(value) is str:require(len(value)<=65536)
    if kind is not None and value is not None:
        require(type(value) is {"float":float,"int":int,"bool":bool,"string":str}[kind])
    return value


def shape(value,names):
    require(type(value) is dict and value.keys()==set(names))


def layout(program):
    calls,bindings={},{b.binding_id:b for b in program.bindings}
    def walk(value):
        if isinstance(value,CallIR):calls[value.call_site_id]=value
        if isinstance(value,StoreIR):bindings[value.binding.binding_id]=value.binding
        if isinstance(value,UserCallIR):
            bindings.update((p.binding_id,p) for p in value.parameters)
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name))
        elif type(value) is tuple:
            for item in value:walk(item)
    walk(program.statements)
    return calls,bindings


def ring_state(ring):
    return {"items":ring.items,"count":ring.count,"capacity":ring.capacity}


def export_state(evaluation):
    from packages.runtime.engine import program_hash
    require(not evaluation.closed and evaluation.at_boundary and evaluation.count>0
        and not evaluation.pending_outputs and not evaluation.history.current,
        "Checkpoint requires a completed historical bar.")
    require(evaluation.program.identity.implementation_profile_id==HISTORICAL_PROFILE_ID,
        "Literal evaluations cannot be checkpointed.")
    states=[]
    for key,state in evaluation.states.items():
        if type(state) is SMASeedEMAState:
            kind,value="ema_seed",{"previous":state.previous,"pending":state.pending}
        elif type(state) is EMAState:kind,value="ema",{"previous":state.previous}
        elif type(state) is SMAState:
            kind,value="sma",{"prefixes":ring_state(state.prefixes),"total":state.total,"count":state.count}
        elif type(state) is ChangeState:kind,value="change",{"values":ring_state(state.values)}
        elif type(state) is CrossState:kind,value="cross",{"previous":list(state.previous) if state.previous is not None else None}
        elif type(state) in TECHNICAL_TYPES:kind,value=export_technical(state)
        elif type(state) in COMPOSITE_TYPES:kind,value=export_composite(state)
        else:raise ValueError("Unregistered runtime state.")
        states.append({"key":list(key),"kind":kind,"value":value})
    return {"schema_version":state_schema(evaluation),"program_sha256":program_hash(evaluation.program),
        **({'settings_sha256':evaluation.input_settings['sha256']} if evaluation.input_settings else {}),
        "mode":"memory" if evaluation.output_sink is None else "sink",
        "count":evaluation.count,"context_id":evaluation.context_id,"first_time":evaluation.first_time,
        "last_time":evaluation.last_time,"persistent":evaluation.persistent,
        "history":{key:ring_state(ring) for key,ring in evaluation.history.buffers.items()},
        "states":states,"budget_cells":evaluation.budget.cells,"times":evaluation.times,
        "outputs":[{"site":site,"title":title,"type":asdict(t),"values":values}
                   for site,(title,t,values) in evaluation.outputs.items()]}


def state_schema(evaluation):
    return ('openpine.runtime-state.4' if evaluation.program.schema_version == 'openpine.typed-ir.8' else
        'openpine.runtime-state.3' if evaluation.program.schema_version == 'openpine.typed-ir.7' else
        'openpine.runtime-state.2' if evaluation.input_settings else SCHEMA)


def restore_state(evaluation,state):
    from packages.runtime.engine import program_hash
    require(evaluation.count==0 and not evaluation.closed,"Restore needs a fresh admitted evaluation.")
    shape(state,("schema_version","program_sha256","mode","count","context_id","first_time","last_time","persistent",
        "history","states","budget_cells","times","outputs",*(['settings_sha256'] if evaluation.input_settings else [])))
    require(state['schema_version']==state_schema(evaluation) and state["mode"]==("memory" if evaluation.output_sink is None else "sink"))
    if evaluation.input_settings:require(state['settings_sha256']==evaluation.input_settings['sha256'],'Checkpoint belongs to different execution settings.')
    require(state["program_sha256"]==program_hash(evaluation.program),"Checkpoint belongs to a different program.")
    count=integer(state["count"],1,2**63-1)
    require(type(state["context_id"]) is str and 0<len(state["context_id"])<=256)
    first=integer(state["first_time"],0,2**63-1);last=integer(state["last_time"],first,2**63-1)
    calls,bindings=layout(evaluation.program)
    require(type(state["persistent"]) is dict)
    persistent={}
    for key,value in state["persistent"].items():
        require(key in bindings and getattr(bindings[key],"mode",None)=="var")
        scalar(value,bindings[key].value_type.base)
        require(evaluation.matches(value,bindings[key].value_type))
        persistent[key]=value
    budget=StorageBudget()
    def ring(value,kind=None,wide=False):
        shape(value,("items","count","capacity"))
        require(type(value["items"]) is list)
        require(value["capacity"]==LIMIT+1 and type(value["capacity"]) is int)
        n=integer(value["count"],0,2**63-1)
        require(len(value["items"])==min(n,LIMIT+1))
        for item in value["items"]:
            if wide:require(type(item) is int)
            scalar(item,kind,wide=wide)
        result=Ring(budget);result.items=value["items"];result.count=n
        budget.cells+=len(result.items)
        require(budget.cells<=CELL_LIMIT)
        return result
    require(type(state["history"]) is dict and state["history"].keys()==evaluation.history.buffers.keys())
    histories={}
    for key,value in state["history"].items():
        source=evaluation.history.sources[key]
        histories[key]=ring(value,source.value_type.base)
        require(histories[key].count==count)
    require(type(state["states"]) is list and len(state["states"])<=len(calls))
    kernels={}
    repeated = repeated_call_sites(evaluation.program)
    operations={"ema":"series.ema","ema_seed":"series.ema.sma_seed","sma":"series.sma","change":"series.change",
        **{kind:"series."+kind for kind in TECHNICAL_KINDS}}
    for row in state["states"]:
        shape(row,("key","kind","value"));key=row["key"];kind=row["kind"];value=row["value"]
        require(type(key) is list and len(key) in (2,3) and all(type(x) is str for x in key))
        key=tuple(key)
        require(key not in kernels and key[0] in calls and key[1]==state["context_id"])
        op=calls[key[0]].operation
        updates = count * (ITERATIONS_PER_BAR + 1) if key[0] in repeated else count
        require(kind in (*operations,"cross",*COMPOSITE_KINDS))
        require(op in KIND_OPERATIONS[kind] if kind in COMPOSITE_KINDS else op in ("series.cross","series.crossover","series.crossunder") if kind=="cross" else op==operations[kind])
        require(key[2:]==(() if kind in ("ema","ema_seed") else (kind,)))
        if kind in COMPOSITE_KINDS:
            result=restore_composite(kind,value,budget,updates,evaluation.program.identity.language_version)
        elif kind in TECHNICAL_KINDS:
            result=restore_technical(kind,value,budget,updates)
        elif kind in ("ema","ema_seed"):
            shape(value,("previous","pending") if kind=="ema_seed" else ("previous",))
            previous=scalar(value["previous"],"float")
            result=SMASeedEMAState() if kind=="ema_seed" else EMAState()
            result.previous=previous
            if kind=="ema_seed":
                require(type(value["pending"]) is list and len(value["pending"])<=min(updates,LIMIT))
                for item in value["pending"]:require(type(item) is float);scalar(item)
                require(previous is None or not value["pending"])
                result.pending=value["pending"]
        elif kind=="sma":
            shape(value,("prefixes","total","count"))
            result=object.__new__(SMAState);result.prefixes=ring(value["prefixes"],"int",wide=True)
            require(type(value["total"]) is int)
            result.count=integer(value["count"],0,updates);result.total=scalar(value["total"],"int",wide=True)
            require(result.prefixes.count==result.count+1 and result.prefixes.ago(0)==result.total)
        elif kind=="change":
            shape(value,("values",));result=object.__new__(ChangeState)
            result.values=ring(value["values"],calls[key[0]].arguments[0].expression.value_type.base)
            require(result.values.count<=updates)
        else:
            shape(value,("previous",));previous=value["previous"]
            require(previous is None or type(previous) is list and len(previous)==2)
            if previous is not None:
                for index,v in enumerate(previous):scalar(v,calls[key[0]].arguments[index].expression.value_type.base)
            result=CrossState();result.previous=tuple(previous) if previous is not None else None
        kernels[key]=result
    require(type(state["budget_cells"]) is int and budget.cells==state["budget_cells"])
    require(type(state["times"]) is list and type(state["outputs"]) is list)
    if state["mode"]=="memory":
        require(len(state["times"])==count and state["times"][0]==first and state["times"][-1]==last)
        require(all(type(t) is int and a<t for a,t in zip([-1,*state["times"][:-1]],state["times"])))
    else:require(not state["times"])
    outputs={}
    for row in state["outputs"]:
        shape(row,("site","title","type","values"));site=row["site"]
        require(site in calls and calls[site].operation=="visual.plot" and site not in outputs)
        t=calls[site].arguments[0].expression.value_type
        require(row["type"]==asdict(t) and type(row["title"]) is str and len(row["title"])<=65536)
        require(type(row["values"]) is list and len(row["values"])==(count if state["mode"]=="memory" else 0))
        for v in row["values"]:scalar(v,t.base)
        outputs[site]=(row["title"],t,row["values"])
    require(set(outputs)=={site for site,call in calls.items() if call.operation=="visual.plot"})
    # Assign only after the entire closed state record has been checked.
    evaluation.count=count;evaluation.context_id=state["context_id"]
    evaluation.first_time=first;evaluation.last_time=last;evaluation.persistent=persistent
    evaluation.states=kernels;evaluation.history.buffers=histories;evaluation.budget=budget
    evaluation.times=state["times"];evaluation.outputs=outputs;evaluation.at_boundary=True
