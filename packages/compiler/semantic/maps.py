"""Map binding through the shared Binder and its argument/source-order rules."""
from dataclasses import dataclass
from packages.contracts import ValueType
from packages.contracts.maps import OPERATIONS,base,parts,is_map,parameters,argument_types,result_type,admit

@dataclass(frozen=True)
class BoundMap:
    operation:str
    key_type:str
    element:str
    arguments:tuple
    evaluation_order:tuple
    value_type:ValueType
    span:object
    namespace:str=''

def bind(binder,call,*,prepared=None,is_na=False):
    from .binder import BoundArgument,BoundNA
    from .specification import ROOT
    v=binder.catalog.language_version
    fail=lambda code,text:binder.fail('LANGUAGE_INVALID',code,text,call.span)
    if v<5:fail('MAP_VERSION','Maps require Pine v5/v6.')
    try:admit(ROOT,binder.catalog.snapshot,v)
    except ValueError as error:binder.fail('SPEC_UNRESOLVED','MAP_PROFILE',str(error),call.span)
    name=call.name.removeprefix('map.');pair=None
    if name.startswith('new<'):
        fields=name[4:-1].split(',')
        if len(fields)!=2:fail('MAP_TYPE','Map constructor requires key and value types.')
        pair=tuple(binder.type_base(t) for t in fields);op='new'
    else:op='is_na' if is_na else name
    if op not in OPERATIONS or op=='is_na' and not is_na:binder.fail('PROFILE_UNCOVERED','MAP_OPERATION','Map operation is outside this profile.',call.span)
    expressions=tuple(binder.expression(a.expression) for a in call.arguments) if prepared is None else prepared
    names=parameters(op);bound={};position=0;named=False
    for i,(argument,expression) in enumerate(zip(call.arguments,expressions,strict=True)):
        if argument.name is None:
            if named:fail('POSITIONAL_AFTER_NAMED','Positional arguments must precede named arguments.')
            slot=position;position+=1
        else:
            named=True
            name='id' if is_na and argument.name=='x' else argument.name
            if name not in names:fail('UNKNOWN_ARGUMENT','Unknown map argument.')
            slot=names.index(name)
        if slot>=len(names):fail('TOO_MANY_ARGUMENTS','Too many map arguments.')
        if slot in bound:fail('DUPLICATE_ARGUMENT','Map argument supplied twice.')
        bound[slot]=BoundArgument(names[slot],expression,'source',i)
    if len(bound)!=len(names):fail('MISSING_ARGUMENT','Missing map argument.')
    if op!='new':pair=parts(bound[0].expression.value_type.base)
    if pair is None or not is_map(base(*pair)):fail('MAP_TYPE','A typed map with admitted key/value types is required.')
    key,element=pair
    for i,t in enumerate(argument_types(op,key,element)):
        a=bound[i]
        # Missing strings are admitted inside a typed collection, as in arrays.
        expression=BoundNA(ValueType('string'),a.expression.span) if isinstance(a.expression,BoundNA) and t=='string' else binder.coerce(a.expression,t,'MAP_ARGUMENT_TYPE')
        bound[i]=BoundArgument(a.parameter_id,expression,a.origin,a.source_index)
    args=tuple(bound[i] for i in range(len(names)))
    order=tuple(sorted(range(len(args)),key=lambda i:args[i].source_index))
    return BoundMap(op,key,element,args,order,result_type(op,key,element),call.span,binder.namespace)
