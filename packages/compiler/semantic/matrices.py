"""Matrix arguments use the shared Binder and closed default descriptors."""
from dataclasses import dataclass
from packages.contracts import ValueType
from packages.contracts.matrices import OPERATIONS,signature,argument_type,result_type,base,element,is_matrix,admit

@dataclass(frozen=True)
class BoundMatrix:
    operation:str
    element:str
    arguments:tuple
    evaluation_order:tuple
    value_type:ValueType
    span:object
    namespace:str=''

def bind(binder,call,*,prepared=None,is_na=False):
    from .binder import BoundArgument,BoundNA,BoundLiteral
    from .specification import ROOT
    from packages.contracts.objects import is_reference
    v=binder.catalog.language_version
    fail=lambda code,text:binder.fail('LANGUAGE_INVALID',code,text,call.span)
    if v<5:fail('MATRIX_VERSION','Matrices require Pine v5/v6.')
    try:admit(ROOT,binder.catalog.snapshot,v)
    except ValueError as error:binder.fail('SPEC_UNRESOLVED','MATRIX_PROFILE',str(error),call.span)
    name=call.name.removeprefix('matrix.');t=None
    if name.startswith('new<'):t=binder.type_base(name[4:-1]);op='new'
    else:op='is_na' if is_na else name
    if op not in OPERATIONS or op=='is_na' and not is_na:binder.fail('PROFILE_UNCOVERED','MATRIX_OPERATION','Matrix operation is outside this profile.',call.span)
    expressions=tuple(binder.expression(a.expression) for a in call.arguments) if prepared is None else prepared
    specs=signature(op);names=tuple(s[0] for s in specs);bound={};position=0;named=False
    for i,(argument,expression) in enumerate(zip(call.arguments,expressions,strict=True)):
        if argument.name is None:
            if named:fail('POSITIONAL_AFTER_NAMED','Positional arguments must precede named arguments.')
            slot=position;position+=1
        else:
            named=True;name='id' if is_na and argument.name=='x' else argument.name
            if name not in names:fail('UNKNOWN_ARGUMENT','Unknown matrix argument.')
            slot=names.index(name)
        if slot>=len(specs):fail('TOO_MANY_ARGUMENTS','Too many matrix arguments.')
        if slot in bound:fail('DUPLICATE_ARGUMENT','Matrix argument supplied twice.')
        bound[slot]=BoundArgument(names[slot],expression,'source',i)
    if op!='new':
        if 0 not in bound:fail('MISSING_ARGUMENT','Missing matrix receiver.')
        t=element(bound[0].expression.value_type.base)
    if t is None or not is_matrix(base(t)):fail('MATRIX_TYPE','A matrix with an admitted element type is required.')
    for i,(name,kind,default) in enumerate(specs):
        typ=argument_type(kind,t)
        if i in bound:
            a=bound[i]
            expression=BoundNA(ValueType('string'),a.expression.span) if isinstance(a.expression,BoundNA) and typ=='string' else binder.coerce(a.expression,typ,'MATRIX_ARGUMENT_TYPE')
            bound[i]=BoundArgument(name,expression,a.origin,a.source_index)
        else:
            if default is None:fail('MISSING_ARGUMENT','Missing matrix argument: '+name)
            if default=='zero':e=BoundLiteral(0,ValueType('int'),call.span)
            elif default=='missing' and t=='bool' and v==6:e=BoundLiteral(False,ValueType('bool'),call.span)
            else:e=BoundNA(ValueType(typ,'series' if is_reference(typ) else 'const'),call.span)
            bound[i]=BoundArgument(name,e,'default',None)
    args=tuple(bound[i] for i in range(len(specs)))
    order=tuple(sorted((i for i,a in enumerate(args) if a.origin=='source'),key=lambda i:args[i].source_index))+tuple(i for i,a in enumerate(args) if a.origin=='default')
    return BoundMatrix(op,t,args,order,result_type(op,t),call.span,binder.namespace)
