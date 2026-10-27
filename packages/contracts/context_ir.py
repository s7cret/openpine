"""Closed request shapes shared by compiler admission and historical scheduling."""

from dataclasses import fields,is_dataclass,replace
from .model import (CallIR,ReferenceIR,LiteralIR,ConditionalIR,BinaryIR,IfIR,UserCallIR,StoreIR,
    Diagnostic,OpenPineError,LoadIR,InputIR,ConvertIR,UnaryIR,NAIR,HistoryIR,BlockIR,UnpackIR,EvaluateIR)
from .model import TupleIR,TupleType,ForIR,WhileIR,ForInIR,ArrayIR
from .composite import COMPOSITE_OPERATIONS
from .contexts import request_policy
from .calendar import timeframe as validate_timeframe


REQUEST_OPERATIONS=frozenset(('numeric.abs','numeric.nz','numeric.int','numeric.float','numeric.round',
    'numeric.min','numeric.max','series.ema','series.ema.sma_seed','series.sma','series.change',
    'series.rma','series.tr','series.atr','series.rsi','series.highest','series.lowest')) | COMPOSITE_OPERATIONS
from .signals import OPERATIONS as SIGNAL_OPERATIONS
from .numeric_functions import NEW_OPERATIONS as NEW_NUMERIC_OPERATIONS
REQUEST_OPERATIONS |= NEW_NUMERIC_OPERATIONS
REQUEST_OPERATIONS |= SIGNAL_OPERATIONS | frozenset(('series.cross','series.crossover','series.crossunder'))
from .environment import CALENDAR
REQUEST_OPERATIONS |= frozenset('environment.'+n for n in (*CALENDAR,'timestamp5','timestamp6'))
REQUEST_SLOTS=frozenset(('open','high','low','close','volume',*(('environment.'+n) for n in ('time','time_close',*CALENDAR,'syminfo.mintick','syminfo.pointvalue','timeframe.multiplier'))))


def ir_nodes(value):
    """Visit IR only; contracts and source metadata are not executable nodes."""
    if hasattr(value,'kind'):
        yield value
        for field in fields(value):yield from ir_nodes(getattr(value,field.name))
    elif type(value) is tuple:
        for child in value:yield from ir_nodes(child)
    elif hasattr(value,'expression'):
        yield from ir_nodes(value.expression)


def request_expression(program,root):
    """Resolve a bounded dependency closure, preserving the already-bound IR.

    Each selected declaration runs once per requested bar, in source order.
    Its written call sites/locals retain their identities in a private runtime.
    This is dependency admission, never another source-name binding pass.
    """
    policy=request_policy(program.identity.language_version)
    def fail(message,node):
        raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','REQUEST_SCOPE',message,node.span,
            'rule.context.request',program.identity.language_version))
    scalar=('int','float','bool')
    def result_type(typ):
        return (all(e.base in scalar for e in typ.elements) and 2<=len(typ.elements)<=16
            if isinstance(typ,TupleType) else typ.base in scalar)
    if not result_type(root.value_type):fail('Requested results require numeric/bool scalar components.',root)
    declarations={}
    for node in program.statements:
        if isinstance(node,StoreIR) and node.declaration:declarations[node.binding.binding_id]=node
        elif isinstance(node,UnpackIR):
            declarations.update((b.binding_id,node) for b in node.bindings if b is not None)
    modified={n.binding.binding_id for n in ir_nodes(program.statements) if isinstance(n,StoreIR) and not n.declaration}
    functions={f.function_id:f for f in program.functions}
    chosen=set();active=set();heights={};local=set();used_functions=set();count=0
    def dependency(key,node):
        declaration=declarations.get(key)
        binding=next((b for b in program.bindings if b.binding_id==key),None)
        if (declaration is None or binding is None or binding.scope_id!='scope:root'
                or binding.namespace or binding.mode!='bar' or key in modified or binding.value_type.base not in scalar):
            fail('Requested captures require immutable unconditional global numeric/bool declarations.',node)
        identity=id(declaration)
        if identity in active:fail('Requested dependencies contain a cycle.',node)
        if identity in heights:return heights[identity]
        active.add(identity)
        height=admit(declaration.expression,allow_input=isinstance(declaration,StoreIR) and declaration in request_input_declarations(program))
        active.remove(identity);chosen.add(identity);heights[identity]=height+1
        return height+1
    def admit(node,allow_input=False):
        nonlocal count
        count+=1
        if count>policy['max_expression_nodes']:fail('Requested expression exceeds its bounded size/depth.',node)
        children=[];height=1
        if isinstance(node,TupleIR):
            if not result_type(node.value_type):fail('Requested tuple elements must be numeric/bool.',node)
            children=list(node.elements)
        elif isinstance(node,(LiteralIR,NAIR)):
            if node.value_type.base not in scalar:fail('Requested literals must be numeric/bool.',node)
        elif isinstance(node,InputIR):
            if not allow_input or node.value_type.base not in scalar:fail('Requested inputs must come from frozen declarations.',node)
        elif isinstance(node,ReferenceIR):
            if node.contract.slot not in REQUEST_SLOTS:fail('Requested references must belong to the requested market context.',node)
        elif isinstance(node,LoadIR):
            if node.binding_id not in local:height+=dependency(node.binding_id,node)
        elif isinstance(node,ConvertIR):
            if not result_type(node.value_type):fail('Requested conversion result must be numeric/bool.',node)
            children=[node.operand]
        elif isinstance(node,UnaryIR):
            if node.operator not in ('+','-','not'):fail('Unsupported requested unary operator.',node)
            children=[node.operand]
        elif isinstance(node,BinaryIR):
            if node.operator not in ('+','-','*','/','%','>','>=','<','<=','==','!=','and','or'):fail('Unsupported requested binary operator.',node)
            children=[node.left,node.right]
        elif isinstance(node,ConditionalIR):children=[node.condition,node.when_true,node.when_false]
        elif isinstance(node,HistoryIR):
            if (not isinstance(node.offset,LiteralIR) or type(node.offset.value) is not int
                    or not 0<=node.offset.value<=policy['max_history_offset'] or node.operand.value_type.base not in scalar):
                fail('Requested history needs a scalar expression and a bounded literal offset.',node)
            children=[node.operand,node.offset]
        elif isinstance(node,CallIR):
            if node.operation not in REQUEST_OPERATIONS or not result_type(node.value_type):
                fail('Requested calls require registered pure numeric/bool/TA kernels; nested requests and effects are excluded.',node)
            children=[a.expression for a in node.arguments]
        elif isinstance(node,UserCallIR):
            function=functions.get(node.function_id)
            if function is None:fail('Requested function is missing its definition.',node)
            used_functions.add(node.function_id)
            # Function contracts list every global visible at definition, not
            # just globals read by this specialization. Actual LoadIR nodes
            # determine dependencies; unused globals must not create effects.
            local.update(p.binding_id for p in node.parameters)
            children=[*(a.expression for a in node.arguments),*node.statements,node.result]
        elif isinstance(node,BlockIR):children=[*node.statements,node.result]
        elif isinstance(node,StoreIR):
            if node.binding.scope_id=='scope:root' or node.binding.value_type.base not in scalar:
                fail('Requested functions can write only their private scalar locals.',node)
            local.add(node.binding.binding_id);children=[node.expression]
        elif isinstance(node,UnpackIR):
            local.update(b.binding_id for b in node.bindings if b is not None)
            children=[node.expression]
        elif isinstance(node,IfIR):children=[node.condition,*node.body,*node.otherwise]
        elif isinstance(node,EvaluateIR):children=[node.expression]
        else:fail('Requested expression excludes loops, collections, strings and external effects.',node)
        if children:height=max(height,1+max(admit(child,allow_input=allow_input and isinstance(node,ConvertIR)) for child in children))
        if height>policy['max_expression_depth']:fail('Requested expression exceeds its bounded size/depth.',node)
        return height
    admit(root)
    inputs={id(n) for n in request_input_declarations(program)}
    dependencies=tuple(n for n in program.statements if id(n) in chosen|inputs)
    selected=(*dependencies,root)
    binding_ids=set()
    for node in ir_nodes(selected):
        if isinstance(node,StoreIR):binding_ids.add(node.binding.binding_id)
        elif isinstance(node,UnpackIR):binding_ids.update(b.binding_id for b in node.bindings if b is not None)
        elif isinstance(node,UserCallIR):binding_ids.update(p.binding_id for p in node.parameters)
    return dependencies,tuple(b for b in program.bindings if b.binding_id in binding_ids),tuple(
        replace(f,captures=tuple(key for key in f.captures if key in binding_ids))
        for f in program.functions if f.function_id in used_functions)


def request_input_declarations(program):
    """Keep the complete frozen settings schema in each private evaluation."""
    result=[]
    for node in program.statements:
        if not isinstance(node,StoreIR) or not node.declaration:continue
        expression=node.expression
        while isinstance(expression,ConvertIR):expression=expression.operand
        if isinstance(expression,InputIR):result.append(node)
    return tuple(result)


def request_nodes(program):
    result=[];policy=request_policy(program.identity.language_version)
    def fail(message,node):
        raise OpenPineError(Diagnostic("PROFILE_UNCOVERED","REQUEST_SCOPE",message,node.span,
            "rule.context.request",program.identity.language_version))
    def walk(value,forbidden=False):
        if isinstance(value,CallIR) and value.operation in ("context.request","context.lower_tf"):
            if forbidden:fail("This request profile requires unconditional global requests.",value)
            args=value.arguments
            lower=value.operation=='context.lower_tf'
            if lower:
                from .lower_tf import PARAMETERS,options,result_type
                if program.identity.language_version<5:fail('Lower-timeframe requests require v5/v6.',value)
                try:
                    options(value)
                    if result_type(args[2].expression.value_type)!=value.value_type:raise ValueError('Lower-timeframe result type differs.')
                except (ValueError,IndexError) as error:fail(str(error),value)
            if len(args)!=(len(PARAMETERS) if lower else len(policy["parameters"])):fail("Request parameter count differs from its version profile.",value)
            primary_target=value.overload_id.endswith('.primary.prepared')
            if primary_target:
                from .primary_targets import shape,resolve,PrimaryRequired
                for kind,arg in zip(('symbol','timeframe'),args[:2],strict=True):
                    shape(program,arg.expression,allow_functions=value.overload_id.endswith('.function.primary.prepared'),allow_scalars=value.overload_id.endswith('.scalar.function.primary.prepared'),allow_numeric=value.overload_id.endswith('.numeric.scalar.function.primary.prepared'))
                    try:resolve(program,arg.expression,kind,allow_functions=value.overload_id.endswith('.function.primary.prepared'),allow_scalars=value.overload_id.endswith('.scalar.function.primary.prepared'),allow_numeric=value.overload_id.endswith('.numeric.scalar.function.primary.prepared'))
                    except PrimaryRequired:pass
                    except ValueError as error:fail(str(error),value)
            elif value.overload_id.endswith('.prepared'):
                from .prepared_requests import target
                symbol,timeframe=(target(program,args[i].expression,computed=value.overload_id.endswith('.computed.prepared'))[0] for i in (0,1))
                if value.overload_id.endswith('.computed.prepared'):
                    from .computed_targets import validate_text
                    try:validate_text('symbol',symbol);validate_text('timeframe',timeframe)
                    except ValueError as error:fail(str(error),value)
            else:
                if any(not isinstance(args[i].expression,LiteralIR) or type(args[i].expression.value) is not str for i in (0,1)):
                    fail("Request symbol/timeframe must be string literals.",value)
                symbol,timeframe=(args[i].expression.value for i in (0,1))
            if not primary_target:
                if not symbol or ":" not in symbol or len(symbol)>256:fail("Use an explicit qualified request symbol.",value)
                try:validate_timeframe(timeframe)
                except ValueError as error:fail(str(error),value)
            request_expression(program,args[2].expression)
            for argument in (() if lower else args[3:]):
                flag=argument.expression
                if not isinstance(flag,LiteralIR) or (type(flag.value) is not bool if policy["flags"]=="bool" else type(flag.value) is not str or flag.value not in ("off","on")):
                    fail("Request flags differ from their version profile.",value)
            result.append(value)
            if len(result)>policy["max_requests"]:fail("At most 16 written requests are supported.",value)
            return
        forbidden=forbidden or isinstance(value,(ConditionalIR,IfIR,UserCallIR,ForIR,WhileIR,ForInIR,ArrayIR)) or (
            isinstance(value,BinaryIR) and value.operator in ("and","or")) or (
            isinstance(value,StoreIR) and value.binding.mode in ('var','varip'))
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name),forbidden)
        elif type(value) is tuple:
            for child in value:walk(child,forbidden)
    walk(program.statements)
    return tuple(result)


def request_flags(node,version):
    if node.operation=='context.lower_tf':return 'off','off'
    values=[a.expression.value for a in node.arguments[3:]]
    flags=[("on" if v else "off") if type(v) is bool else v for v in values]
    return flags[0],flags[1] if len(flags)>1 else request_policy(version)["lookahead_default"]


def missing_request(typ,version):
    """No visible sample: numeric NA; v6 bool false, component by component."""
    if isinstance(typ,TupleType):return tuple(missing_request(t,version) for t in typ.elements)
    return False if typ.base=="bool" and version==6 else None
