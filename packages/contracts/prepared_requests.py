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
    computed: bool = False

    def resolve(self,program,settings):
        return target(program,self.expression,settings,resolve=True,computed=self.computed)[0]

def target(program,expression,settings=None,*,resolve=False,computed=False):
    if computed:
        from .computed_targets import evaluate
        value,dependencies=evaluate(program,expression,settings if resolve else None)
        return value,TargetTemplate(expression,dependencies,True)
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

def resolved_requests(program,settings=None,*,primary=None):
    from .context_ir import request_nodes
    from .primary_targets import PrimaryRequired
    result=[];missing=set()
    for node in request_nodes(program):
        if not node.overload_id.endswith('.prepared'):result.append(node);continue
        args=list(node.arguments);pending=False
        for i in (0,1):
            if node.overload_id.endswith('.primary.prepared'):
                from .primary_targets import resolve
                try:value=resolve(program,args[i].expression,('symbol','timeframe')[i],settings,primary=primary,allow_functions=node.overload_id.endswith('.function.primary.prepared'),allow_scalars=node.overload_id.endswith('.scalar.function.primary.prepared'))[0]
                except PrimaryRequired as error:missing.update(error.fields);pending=True;continue
            else:value=target(program,args[i].expression,settings,resolve=True,computed=node.overload_id.endswith('.computed.prepared'))[0]
            args[i]=replace(args[i],expression=LiteralIR(value,ValueType('string'),args[i].expression.span))
        if pending:continue
        selected=replace(node,arguments=tuple(args))
        if node.overload_id.endswith('.computed.prepared'):
            from .computed_targets import validate_text
            for kind,arg in zip(('symbol','timeframe'),args[:2],strict=True):
                try:validate_text(kind,arg.expression.value)
                except ValueError as error:raise ValueError(f'Resolved request {kind} {arg.expression.value!r}: {error}') from error
        validate_pair(selected)
        result.append(selected)
    if missing:raise PrimaryRequired(missing)
    return tuple(result)

def validate_pair(node):
    from .calendar import timeframe
    symbol,tf=(a.expression.value for a in node.arguments[:2])
    if type(symbol) is not str or not symbol or ':' not in symbol or len(symbol)>256:raise ValueError('Use an explicit qualified request symbol.')
    timeframe(tf)

def graph(program,settings=None,*,primary=None):
    from .inputs import admitted_settings
    from .context_ir import request_nodes
    selected=admitted_settings(program,settings)
    rows=[{'call_site_id':n.call_site_id,'operation':n.operation,'symbol':n.arguments[0].expression.value,'timeframe':n.arguments[1].expression.value} for n in resolved_requests(program,selected,primary=primary)]
    raw={n.call_site_id:n for n in request_nodes(program)}
    for row in rows:
        node=raw[row['call_site_id']]
        if node.overload_id.endswith('.primary.prepared'):
            from .primary_targets import resolve
            values={name:resolve(program,node.arguments[i].expression,name,selected,primary=primary,allow_functions=node.overload_id.endswith('.function.primary.prepared'),allow_scalars=node.overload_id.endswith('.scalar.function.primary.prepared')) for i,name in enumerate(('symbol','timeframe'))}
            row['dependencies']={name:list(value[1]) for name,value in values.items()}
            row['raw_targets']={name:value[2] for name,value in values.items()}
            if node.overload_id.endswith('.function.primary.prepared'):
                from .function_targets import trace as function_trace
                from .scalar_targets import trace as scalar_trace
                trace=scalar_trace if node.overload_id.endswith('.scalar.function.primary.prepared') else function_trace
                row['function_dependencies']={name:trace(program,node.arguments[i].expression)['functions'] for i,name in enumerate(('symbol','timeframe'))}
        elif node.overload_id.endswith('.prepared'):
            row['dependencies']={name:list(target(program,node.arguments[i].expression,computed=node.overload_id.endswith('.computed.prepared'))[1].dependencies) for i,name in enumerate(('symbol','timeframe'))}
    if len({(r['symbol'],r['timeframe']) for r in rows})>8:raise ValueError('Prepared requests exceed the eight-context budget.')
    body={'schema_version':PROFILE,'settings_sha256':selected['sha256'] if selected else None,'requests':rows}
    from .primary_targets import uses_primary,metadata,PROFILE as PRIMARY_PROFILE
    if uses_primary(program):
        main=metadata(primary)
        if main is not None:
            from .calendar import requestable
            from .lower_tf import requestable as lower_requestable,options
            for node in resolved_requests(program,selected,primary=primary):
                tf=node.arguments[1].expression.value
                if node.operation=='context.lower_tf':
                    if not lower_requestable(main['timeframe'],tf) and not options(node)[0]:raise ValueError('Lower requests require a smaller divisible primary interval.')
                elif not requestable(main['timeframe'],tf):raise ValueError('security requires a same/higher primary interval.')
        body.update(schema_version=PRIMARY_PROFILE,primary=main)
    from .function_targets import SUFFIX,PROFILE as FUNCTION_PROFILE
    if any(n.overload_id.endswith(SUFFIX) for n in raw.values()):
        from dataclasses import asdict
        body.update(schema_version=FUNCTION_PROFILE,program_sha256=digest(asdict(program)))
    from .scalar_targets import SUFFIX as SCALAR_SUFFIX,PROFILE as SCALAR_PROFILE,trace as scalar_trace
    if any(n.overload_id.endswith(SCALAR_SUFFIX) for n in raw.values()):
        body.update(schema_version=SCALAR_PROFILE)
        for row in rows:
            n=raw[row['call_site_id']]
            if n.overload_id.endswith(SCALAR_SUFFIX):row['scalar_dependencies']={name:scalar_trace(program,n.arguments[i].expression)['kernels'] for i,name in enumerate(('symbol','timeframe'))}
    return {**body,'sha256':digest(body)}
