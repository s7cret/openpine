"""Derived storage bounds. Never trust capacity hints from a saved artifact.

Only integer proofs are used. Overflow, mutation, unsupported expressions and
analysis exhaustion widen to int64, then to the complete legal window horizon.
"""
from dataclasses import fields,is_dataclass
from .model import (LiteralIR,InputIR,LoadIR,BinaryIR,UnaryIR,ConditionalIR,
                    CallIR,StoreIR,UserCallIR,BlockIR,IfIR)
from .wire import digest

HORIZON=100000
INT=(-2**63,2**63-1)
PROFILE='openpine.history-storage.1'
CELL_BYTES=640  # conservative charge, including exact prefix integers and slots

def policy():
    return dict(profile_id=PROFILE,legal_length=[1,HORIZON],unknown_retention=HORIZON,
        allocation='geometric_to_proven_retention',proofs=['integer_intervals','positive_modulo','min_max','frozen_inputs','immutable_bindings','specialized_functions'],
        cells_per_evaluation=1000000,charged_bytes_per_cell=CELL_BYTES,
        job_history_fraction=[1,2],preview='changed_cells',restore='temporary_then_atomic_publish',
        estimate='conservative_history_charge_not_process_RSS',external_execution_verified=False)

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
    rows={}
    def walk(node,env,collect=True):
        if isinstance(node,CallIR) and node.operation in ('context.request','context.lower_tf'):return
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
            rows[node.call_site_id]=dict(call_site_id=node.call_site_id,operation=node.operation,
                legal_max=HORIZON,retention=cap,proof='integer_interval' if hi<HORIZON else 'full_horizon',
                length_interval=[lo,hi],max_cells=cells,charged_bytes=cells*CELL_BYTES)
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
    payload=dict(profile_id=PROFILE,settings_sha256=settings['sha256'] if settings else None,calls=sorted(rows.values(),key=lambda r:r['call_site_id']))
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
    cells=sum(r['max_cells'] for c in contexts for r in c['calls'])
    return dict(profile_id=PROFILE,contexts=contexts,max_cells=cells,
        retained_charge_bytes=cells*CELL_BYTES,restore_peak_charge_bytes=2*cells*CELL_BYTES,
        scope='changing_windows_only; other histories and process RSS remain separately bounded',
        allocation='incremental; upper estimate is not allocated at launch')
