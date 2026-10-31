"""Bind ordering intrinsics through the ordinary array node and lexical binder."""
from packages.contracts import ValueType
from packages.contracts.arrays import is_array
from packages.contracts.objects import is_object
from packages.contracts.array_order import SORT,SEARCH,RANK,available,parameters,result_type,order_type,order_value,field_layout,admit
from .binder import BoundArgument,BoundArray,BoundLiteral,BoundLoad,BoundUnary,BoundBinary,BoundConditional

def constant(binder,node,seen=()):
    if node.value_type.qualifier!='const':raise ValueError('Sort field requires a const selector.')
    if isinstance(node,BoundLiteral):return node.value
    if isinstance(node,BoundLoad):
        key=node.binding.binding_id
        if key in seen:raise ValueError('Cyclic const selector.')
        if key in binder.shared.get('constant_bindings',{}):return constant(binder,binder.shared['constant_bindings'][key],(*seen,key))
        info=next((i for i in binder.resolver.bindings if i.contract().binding_id==key),None)
        if info is None or info.declaration.expression is None:raise ValueError('Const selector cannot be resolved in this scope.')
        return constant(binder,binder.expression(info.declaration.expression),(*seen,key))
    if isinstance(node,BoundUnary) and node.operator in ('+','-'):
        n=constant(binder,node.operand,seen);return n if node.operator=='+' else -n
    if isinstance(node,BoundBinary):
        a=constant(binder,node.left,seen);b=constant(binder,node.right,seen)
        if node.operator=='+':return a+b
        if node.operator=='-':return a-b
        if node.operator=='*':return a*b
        if node.operator=='%':return a%b
        if node.operator=='==':return a==b
        if node.operator=='!=':return a!=b
        if node.operator=='<':return a<b
        if node.operator=='<=':return a<=b
        if node.operator=='>':return a>b
        if node.operator=='>=':return a>=b
    if isinstance(node,BoundConditional):return constant(binder,node.when_true if constant(binder,node.condition,seen) else node.when_false,seen)
    raise ValueError('Sort field expression is outside the closed const selector profile.')

def bind(binder,call,op,expressions):
    v=binder.catalog.language_version
    def fail(code,text):binder.fail('LANGUAGE_INVALID',code,text,call.span)
    from .specification import ROOT
    try:admit(ROOT,binder.catalog.snapshot,v)
    except ValueError as e:binder.fail('SPEC_UNRESOLVED','ARRAY_ORDER_PROFILE',str(e),call.span)
    receiver=next((expressions[i] for i,a in enumerate(call.arguments) if a.name=='id'),expressions[0] if expressions and call.arguments[0].name is None else None)
    if receiver is None:fail('MISSING_ARGUMENT','Missing array id.')
    if not is_array(receiver.value_type.base):fail('ARRAY_TYPE','Ordering requires an array reference.')
    element=receiver.value_type.base[6:]
    if v<5 and op!='sort':fail('ARRAY_ORDER_VERSION','This array operation requires Pine v5/v6.')
    if is_object(element) and v<6:fail('ARRAY_ORDER_VERSION','UDT ordering requires Pine v6.')
    if not available(op,v,element,len(expressions)):fail('ARRAY_ORDER_TYPE','This operation does not order these elements.')
    names=parameters(op,v,element,len(expressions));bound={};position=0;named=False
    for i,(a,e) in enumerate(zip(call.arguments,expressions,strict=True)):
        if a.name is None:
            if named:fail('POSITIONAL_AFTER_NAMED','Positional arguments must precede named arguments.')
            index=position;position+=1
        else:
            named=True
            if a.name not in names:fail('UNKNOWN_ARGUMENT','Unknown ordering argument.')
            index=names.index(a.name)
        if index>=len(names):fail('TOO_MANY_ARGUMENTS','Too many ordering arguments.')
        if index in bound:fail('DUPLICATE_ARGUMENT','Repeated ordering argument.')
        bound[index]=BoundArgument(names[index],e,'source',i)
    for i,name in enumerate(names):
        if i in bound:continue
        if name=='order':value=BoundLiteral(order_value(v),ValueType(order_type(v)),call.span)
        elif name=='sort_field':value=BoundLiteral(0,ValueType('int'),call.span)
        else:fail('MISSING_ARGUMENT','Missing ordering argument: '+name)
        bound[i]=BoundArgument(name,value,'default',None)
    key=element
    if is_object(element):
        try:
            value=constant(binder,bound[2].expression)
            _,key=field_layout(element,value,binder.object_types)
        except (ValueError,TypeError,ArithmeticError) as e:fail('ARRAY_SORT_FIELD',str(e))
        old=bound[2];bound[2]=BoundArgument('sort_field',BoundLiteral(value,ValueType('int' if type(value) is int else 'string'),old.expression.span),old.origin,old.source_index)
    for i,a in list(bound.items()):
        e=a.expression
        if a.parameter_id=='order':e=binder.coerce(e,order_type(v),'ARRAY_ORDER_TYPE')
        elif a.parameter_id=='nth':e=binder.coerce(e,'int','ARRAY_RANK_TYPE')
        elif a.parameter_id=='val':
            from .binder import BoundNA
            if not(key=='int' and e.value_type.base=='float'):
                e=BoundNA(ValueType('string'),e.span) if isinstance(e,BoundNA) and key=='string' else binder.coerce(e,key,'ARRAY_SEARCH_TYPE')
        bound[i]=BoundArgument(a.parameter_id,e,a.origin,a.source_index)
    args=tuple(bound[i] for i in range(len(names)));order=tuple(sorted(range(len(args)),key=lambda i:(args[i].origin=='default',args[i].source_index if args[i].source_index is not None else i)))
    return BoundArray(op,element,args,order,result_type(op,element),call.span,binder.namespace)
