"""Typed request targets resolved from frozen input settings before any data I/O."""
from dataclasses import dataclass,replace
from .model import LiteralIR,LoadIR,InputIR,StoreIR,ValueType,Diagnostic,OpenPineError
from .wire import digest

PROFILE='openpine.prepared-requests.1'

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'security':True,'lower_tf':version>=5,
        'targets':'qualified_symbol_and_canonical_timeframe','dependencies':'immutable_global_string_literals_and_inputs',
        'resolution':'frozen_settings_before_data_acquisition','mutation':'new_run_required',
        'max_contexts':8,'max_requests':16,'external_equivalence':False}

def prepared_contract(contract,version):
    return replace(contract,overload_id=contract.overload_id+'.prepared',assertion_id=contract.assertion_id+'.prepared',
        signature_sha256=digest({'base':contract.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('input','input',*contract.qualifier_limits[2:]))

def uses_prepared(program):
    from .context_ir import ir_nodes
    return any(getattr(n,'operation',None) in ('context.request','context.lower_tf') and n.overload_id.endswith('.prepared') for n in ir_nodes(program.statements))

def base_schema(program):
    from .inputs import definitions
    from .composite import uses_composite
    from .loops import uses_loops
    from .arrays import uses_arrays
    from .environment import uses_environment
    return ('openpine.typed-ir.10' if uses_environment(program) else 'openpine.typed-ir.9' if uses_arrays(program)
        else 'openpine.typed-ir.8' if uses_loops(program) else 'openpine.typed-ir.7' if uses_composite(program)
        else 'openpine.typed-ir.6' if definitions(program) else 'openpine.typed-ir.5')

@dataclass(frozen=True)
class TargetTemplate:
    expression: object
    dependencies: tuple[str,...]

    def resolve(self,program,settings):
        return target(program,self.expression,settings,resolve=True)[0]

def target(program,expression,settings=None,*,resolve=False):
    from .context_ir import ir_nodes
    from .inputs import admitted_settings
    def fail(message):raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','REQUEST_TARGET',message,expression.span,language_version=program.identity.language_version))
    declarations={n.binding.binding_id:n for n in program.statements if isinstance(n,StoreIR) and n.declaration}
    modified={n.binding.binding_id for n in ir_nodes(program.statements) if isinstance(n,StoreIR) and not n.declaration}
    bindings={b.binding_id:b for b in program.bindings};active=set();dependencies=set();seen=0
    selected=admitted_settings(program,settings) if resolve else None
    def visit(node):
        nonlocal seen
        seen+=1
        if seen>64:fail('Request target dependency limit exceeded.')
        if node.value_type.base!='string' or node.value_type.qualifier not in ('const','input'):fail('Request targets require a frozen string input or literal.')
        if isinstance(node,LiteralIR):
            if type(node.value) is not str:fail('Request target literal must be a string.')
            return node.value
        if isinstance(node,InputIR):
            if node.definition.input_type!='string':fail('Request targets require string inputs.')
            dependencies.add(node.definition.input_id)
            return selected['values'][node.definition.input_id] if selected else node.definition.default
        if not isinstance(node,LoadIR):fail('Request targets exclude per-bar expressions, calls and mutable state.')
        binding=bindings.get(node.binding_id);decl=declarations.get(node.binding_id)
        if (binding is None or decl is None or binding.scope_id!='scope:root' or binding.mode!='bar' or binding.namespace
                or binding.binding_id in modified or binding.value_type!=node.value_type):fail('Request targets require immutable global string declarations.')
        if node.binding_id in active:fail('Request target dependencies contain a cycle.')
        active.add(node.binding_id);value=visit(decl.expression);active.remove(node.binding_id);return value
    value=visit(expression)
    return value,TargetTemplate(expression,tuple(sorted(dependencies)))

def resolved_requests(program,settings=None):
    from .context_ir import request_nodes
    result=[]
    for node in request_nodes(program):
        if not node.overload_id.endswith('.prepared'):result.append(node);continue
        args=list(node.arguments)
        for i in (0,1):
            value=target(program,args[i].expression,settings,resolve=True)[0]
            args[i]=replace(args[i],expression=LiteralIR(value,ValueType('string'),args[i].expression.span))
        selected=replace(node,arguments=tuple(args));validate_pair(selected);result.append(selected)
    return tuple(result)

def validate_pair(node):
    from .calendar import timeframe
    symbol,tf=(a.expression.value for a in node.arguments[:2])
    if type(symbol) is not str or not symbol or ':' not in symbol or len(symbol)>256:raise ValueError('Use an explicit qualified request symbol.')
    timeframe(tf)

def graph(program,settings=None):
    from .inputs import admitted_settings
    from .context_ir import request_nodes
    selected=admitted_settings(program,settings)
    rows=[{'call_site_id':n.call_site_id,'operation':n.operation,'symbol':n.arguments[0].expression.value,'timeframe':n.arguments[1].expression.value} for n in resolved_requests(program,selected)]
    raw={n.call_site_id:n for n in request_nodes(program)}
    for row in rows:
        node=raw[row['call_site_id']]
        if node.overload_id.endswith('.prepared'):
            row['dependencies']={name:list(target(program,node.arguments[i].expression)[1].dependencies) for i,name in enumerate(('symbol','timeframe'))}
    if len({(r['symbol'],r['timeframe']) for r in rows})>8:raise ValueError('Prepared requests exceed the eight-context budget.')
    body={'schema_version':PROFILE,'settings_sha256':selected['sha256'] if selected else None,'requests':rows}
    return {**body,'sha256':digest(body)}
