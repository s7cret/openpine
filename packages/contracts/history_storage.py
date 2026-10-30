"""Derived storage bounds. Never trust capacity hints from a saved artifact.

Only integer proofs are used. Overflow, mutation, unsupported expressions and
analysis exhaustion widen to int64, then to the complete legal window horizon.
"""
from dataclasses import fields,is_dataclass
from .model import (LiteralIR,InputIR,LoadIR,BinaryIR,UnaryIR,ConditionalIR,
                    CallIR,StoreIR,UserCallIR,BlockIR,IfIR,HistoryIR)
from .wire import digest
from .memory import KINDS,charge,value_kind

HORIZON=100000
INT=(-2**63,2**63-1)
PROFILE='openpine.history-storage.3'
CELL_BYTES=640  # conservative charge, including exact prefix integers and slots

def policy():
    return dict(profile_id=PROFILE,legal_length=[1,HORIZON],unknown_retention=HORIZON,
        allocation='geometric_to_proven_retention',proofs=['integer_intervals','positive_modulo','min_max','frozen_inputs','immutable_bindings','specialized_functions'],
        cells_per_evaluation=1000000,charged_bytes_by_kind=KINDS,
        job_memory_profile='openpine.job-memory.1',preview='changed_cells',restore='temporary_then_atomic_publish',
        estimate='conservative_history_charge_not_process_RSS',string_history='slot_plus_four_bytes_per_character',
        ema_warmup='release_bytes_after_seed_keep_logical_cells',external_execution_verified=False)

def dynamic(node):
    return isinstance(node,CallIR) and node.operation.startswith(('window.','swindow.'))

def plan(program,settings=None):
    from .inputs import admitted_settings
    settings=admitted_settings(program,settings)
    values=settings['values'] if settings else {}
    mutated=set()
    def writes(node):
        if isinstance(node,StoreIR) and (not node.declaration or node.binding.mode in ('var','varip')):mutated.add(node.binding.binding_id)
        if is_dataclass(node):
            for f in fields(node):writes(getattr(node,f.name))
        elif type(node) is tuple:
            for child in node:writes(child)
    writes(program.statements)
    remaining=131072
    def bounds(node,env,depth=0):
        nonlocal remaining
        remaining-=1
        if remaining<0 or depth>128:return INT
        recur=lambda n:bounds(n,env,depth+1)
        if isinstance(node,LiteralIR) and type(node.value) is int:return (node.value,)*2
        if isinstance(node,InputIR):
            x=values.get(node.definition.input_id,node.definition.default)
            return (x,x) if type(x) is int else INT
        if isinstance(node,LoadIR):return env.get(node.binding_id,INT) if node.binding_id not in mutated else INT
        if isinstance(node,UnaryIR) and node.operator in ('+','-'):
            a,b=recur(node.operand);v=(a,b) if node.operator=='+' else (-b,-a)
        elif isinstance(node,BinaryIR):
            a,b=recur(node.left);c,d=recur(node.right)
            if node.operator=='%' and c==d and c>0:return (0,c-1)
            if node.operator=='+':v=(a+c,b+d)
            elif node.operator=='-':v=(a-d,b-c)
            elif node.operator=='*':xs=(a*c,a*d,b*c,b*d);v=(min(xs),max(xs))
            else:return INT
        elif isinstance(node,ConditionalIR):
            a,b=recur(node.when_true);c,d=recur(node.when_false);v=(min(a,c),max(b,d))
        elif isinstance(node,CallIR) and node.operation in ('numeric.min','numeric.max') and node.value_type.base=='int':
            xs=[recur(a.expression) for a in node.arguments];fn=min if node.operation=='numeric.min' else max
            v=(fn(x[0] for x in xs),fn(x[1] for x in xs))
        elif isinstance(node,UserCallIR):
            local=dict(env)
            for p,a in zip(node.parameters,node.arguments,strict=True):local[p.binding_id]=recur(a.expression)
            statements(node.statements,local,False)
            return bounds(node.result,local,depth+1)
        elif isinstance(node,BlockIR):
            local=dict(env);statements(node.statements,local,False);return bounds(node.result,local,depth+1)
        else:return INT
        return v if INT[0]<=v[0]<=v[1]<=INT[1] else INT
    rows={};histories={}
    def walk(node,env,collect=True):
        if isinstance(node,CallIR) and node.operation in ('context.request','context.lower_tf'):return
        if isinstance(node,HistoryIR) and collect:
            lo,hi=bounds(node.offset,env);cap=max(0,min(HORIZON,hi)) if hi>=0 else HORIZON
            old=histories.get(node.series_id)
            kind=value_kind(node.operand.value_type.base)
            if old is None or cap>old['retention']:
                histories[node.series_id]=dict(series_id=node.series_id,operation='history',retention=cap,
                    proof='integer_interval' if hi<HORIZON else 'full_horizon',offset_interval=[lo,hi],
                    alignment='bars',max_cells=cap,cell_kind=kind,charged_bytes=charge(kind,cap))
        if isinstance(node,CallIR) and node.operation in ('series.sma','series.change') and collect:
            lo,hi=bounds(node.arguments[1].expression,env)
            zero=node.operation=='series.change';cap=max(0 if zero else 1,min(HORIZON,hi))
            if hi<(0 if zero else 1):cap=HORIZON
            cells=cap+1 if not zero else cap
            kind='exact' if not zero else 'value'
            rows[node.call_site_id]=dict(call_site_id=node.call_site_id,operation=node.operation,
                legal_max=HORIZON,retention=cap,proof='integer_interval' if hi<HORIZON else 'full_horizon',
                length_interval=[lo,hi],max_cells=cells,cell_kind=kind,
                alignment='defined_samples' if not zero else 'reached_calls',charged_bytes=charge(kind,cells)+charge('state'))
        if dynamic(node) and collect:
            arg=node.arguments[-1 if node.operation.startswith('swindow.') else 1].expression
            lo,hi=bounds(arg,env);cap=max(1,min(HORIZON,hi))
            # An invalid/NA length still fails at runtime, before any mutation.
            if hi<1:cap=HORIZON
            if node.operation=='swindow.vwma':cells=4*cap+2
            elif node.operation in ('window.sum',):cells=cap+1
            elif node.operation in ('swindow.mom','swindow.roc'):cells=cap+1
            elif node.operation in ('window.highest','window.lowest') or node.operation.startswith('swindow.'):cells=2*cap
            else:cells=2*(cap+1)
            if node.operation=='swindow.vwma':amount=charge('pair',2*cap)+charge('exact',2*(cap+1));kind='paired_prefix'
            elif node.operation.startswith('window.') and node.operation not in ('window.highest','window.lowest'):amount=charge('exact',cells);kind='exact'
            elif node.operation.startswith('swindow.') and node.operation not in ('swindow.mom','swindow.roc'):amount=cap*(charge('value')+charge('index'));kind='indexed'
            else:amount=charge('value',cells);kind='value'
            rows[node.call_site_id]=dict(call_site_id=node.call_site_id,operation=node.operation,
                legal_max=HORIZON,retention=cap,proof='integer_interval' if hi<HORIZON else 'full_horizon',
                length_interval=[lo,hi],max_cells=cells,cell_kind=kind,charged_bytes=amount+charge('state'))
        if isinstance(node,CallIR) and collect and node.call_site_id not in rows and node.operation.startswith(('series.','signals.')):
            op=node.operation;cells=0;kind='value';cap=0;proof='scalar_state';extra={}
            length_ops=('highest','lowest','sum','wma','vwma','mom','roc','rising','falling','highestbars','lowestbars','variance','stdev','variance.population','stdev.population','bb','valuewhen')
            name=op.split('.',1)[1]
            if name in length_ops:
                argument=node.arguments[1 if name in ('bb','variance','stdev','variance.population','stdev.population') else -1].expression
                lo,hi=bounds(argument,env);cap=max(1,min(HORIZON,hi)) if hi>=1 else HORIZON
                proof='integer_interval' if hi<HORIZON else 'full_horizon'
                cells=2*cap if name in ('highest','lowest') else 4*cap if name in ('rising','falling','highestbars','lowestbars') else 2*cap if name=='vwma' else cap+1 if name in ('mom','roc','valuewhen') else cap
                kind='pair' if name=='vwma' else 'index' if cells==4*cap else 'value'
            elif op=='series.ema.sma_seed':
                lo,hi=bounds(node.arguments[1].expression,env);cap=max(1,hi)
                # EMA's language length has no 100k limit. Its byte admission
                # bounds an unknown warmup; never silently clamp its semantics.
                if hi==INT[1]:extra['quota_bounded_warmup']=True
                else:extra['warmup_samples']=cap
                proof='job_byte_quota' if extra else 'integer_interval'
            elif name=='macd' and program.identity.language_version<5:
                caps=[max(1,min(HORIZON,bounds(a.expression,env)[1])) for a in node.arguments[1:]]
                cells=sum(caps);cap=max(caps);proof='fixed_seed_windows'
            rows[node.call_site_id]=dict(call_site_id=node.call_site_id,operation=op,retention=cap,
                proof=proof,max_cells=cells,cell_kind=kind,charged_bytes=charge(kind,cells+extra.get('warmup_samples',0))+charge('state'),**extra)
        if isinstance(node,UserCallIR):
            for a in node.arguments:walk(a.expression,env,collect)
            local=dict(env)
            for p,a in zip(node.parameters,node.arguments,strict=True):local[p.binding_id]=bounds(a.expression,env)
            statements(node.statements,local,collect);walk(node.result,local,collect);return
        if isinstance(node,BlockIR):
            local=dict(env);statements(node.statements,local,collect);walk(node.result,local,collect);return
        if isinstance(node,IfIR):
            walk(node.condition,env,collect);statements(node.body,dict(env),collect);statements(node.otherwise,dict(env),collect);return
        if is_dataclass(node):
            for f in fields(node):walk(getattr(node,f.name),env,collect)
        elif type(node) is tuple:
            for n in node:walk(n,env,collect)
    def statements(nodes,env,collect):
        for n in nodes:
            walk(n,env,collect)
            if isinstance(n,StoreIR) and n.declaration and n.binding.binding_id not in mutated:
                env[n.binding.binding_id]=bounds(n.expression,env)
    statements(program.statements,{},True)
    payload=dict(profile_id=PROFILE,settings_sha256=settings['sha256'] if settings else None,
        calls=sorted(rows.values(),key=lambda r:r['call_site_id']),histories=sorted(histories.values(),key=lambda r:r['series_id']))
    return {**payload,'sha256':digest(payload)}

def report(program,settings=None):
    """Primary plus one private owner per written request, even on one market."""
    from dataclasses import replace
    from .context_ir import request_nodes,request_expression
    from .model import EvaluateIR
    contexts=[dict(context='primary',**plan(program,settings))]
    for node in request_nodes(program):
        expression=node.arguments[2].expression
        dependencies,bindings,functions=request_expression(program,expression)
        private=replace(program,statements=(*dependencies,EvaluateIR(expression,expression.span)),bindings=bindings,functions=functions)
        contexts.append(dict(context=node.call_site_id,**plan(private,settings)))
    cells=sum(r['max_cells'] for c in contexts for r in (*c['calls'],*c['histories']))
    charged=sum(r['charged_bytes'] for c in contexts for r in (*c['calls'],*c['histories']))
    return dict(profile_id=PROFILE,contexts=contexts,max_cells=cells,
        retained_charge_bytes=charged,restore_peak_charge_bytes=2*charged,
        estimate_complete=not any(r.get('quota_bounded_warmup') for c in contexts for r in c['calls']),
        scope='histories_and_stateful_kernels; arrays and transient owners listed separately',
        additional_owners=inventory(program),
        allocation='incremental; upper estimate is not allocated at launch')

def inventory(program):
    from .arrays import uses_arrays,MAX_CELLS,MAX_OBJECTS,MAX_STRING_BYTES
    from .objects import uses_objects
    objects=uses_objects(program)
    return [dict(owner='reference_heap' if objects else 'arrays',active=uses_arrays(program) or objects,max_objects=MAX_OBJECTS,max_elements=MAX_CELLS,
                 max_string_bytes=MAX_STRING_BYTES,charged_bytes=charge('array_object',MAX_OBJECTS)+charge('array_cell',MAX_CELLS)*(2 if objects else 1)+(8 if objects else 4)*MAX_STRING_BYTES,
                 admission='shared_job_storage_ledger_and_reference_quotas' if objects else 'shared_job_storage_ledger_and_array_quotas'),
        dict(owner='preview',retention='changed_cells_and_copy_on_write_arrays',admission='shared_job_storage_ledger'),
        dict(owner='restore',retention='temporary_then_atomic_publish',admission='shared_job_storage_ledger'),
        dict(owner='candle_readers_and_output_writer',retention='bounded_batches_and_duckdb_caches',admission='job_io_slots'),
        dict(owner='checkpoint_codec_and_python_overhead',retention='transient',admission='job_base_reserve_and_process_RSS'),
        dict(owner='in_memory_API_outputs',retention='all_outputs_in_direct_run_only',admission='process_RSS; production_workers_stream_to_sink')]
