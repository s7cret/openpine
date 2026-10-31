"""Closed nominal object operations over the shared bounded reference heap."""
import re
from packages.contracts import (ObjectProgramIR,ObjectTypeContract,ObjectFieldContract,ObjectIR,
    LiteralIR,NAIR,ReferenceIR,ConvertIR,ValueType,HISTORICAL_PROFILE_ID)
from packages.contracts.objects import FIELDS,MAX_TYPES,MAX_FIELDS,is_object,uses_objects
from packages.contracts.scopes import site_id

def admit_types(runtime,program,fail):
    if not isinstance(program,ObjectProgramIR):
        if uses_objects(program):fail('IR_OBJECT_SCHEMA','Objects require the closed object program record and typed IR .14.')
        return {}
    from packages.contracts.object_arrays import uses_object_arrays
    from packages.contracts import EnumProgramIR
    from packages.contracts.enums import is_enum
    enum_types={t.type_id for t in getattr(program,'enum_types',())}
    if isinstance(program,EnumProgramIR) and program.object_types==():return {}
    expected='openpine.typed-ir.16' if isinstance(program,EnumProgramIR) else 'openpine.typed-ir.15' if uses_object_arrays(program) else 'openpine.typed-ir.14'
    if program.schema_version!=expected:fail('IR_OBJECT_SCHEMA','Object program record requires typed IR .14.')
    if runtime.identity.language_version<5 or runtime.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
        fail('IR_OBJECT_VERSION','Objects require the historical v5/v6 profile.')
    if type(program.object_types) is not tuple or not 1<=len(program.object_types)<=MAX_TYPES:fail('IR_OBJECT_TYPE','Invalid type registry.')
    found={};names=set()
    for n in program.object_types:
        if (type(n) is not ObjectTypeContract or not is_object(n.type_id) or n.type_id!=site_id('udt',n.span)
            or n.type_id in found or n.name in names or not re.fullmatch(r'[A-Za-z_]\w*',n.name)
            or n.name in (*FIELDS,'array','type','method','if','else','var','varip','true','false','na')
            or type(n.fields) is not tuple or not 1<=len(n.fields)<=MAX_FIELDS):fail('IR_OBJECT_TYPE','Invalid nominal type declaration.')
        fields=set()
        for f in n.fields:
            if (type(f) is not ObjectFieldContract or f.name in fields or not re.fullmatch(r'[A-Za-z_]\w*',f.name)
                or f.base not in FIELDS and f.base not in enum_types or type(f.intrabar) is not bool or not n.span.start<f.span.start<f.span.end<=n.span.end):
                fail('IR_OBJECT_FIELD','Invalid field declaration.')
            d=f.default;inner=d.operand if isinstance(d,ConvertIR) else d
            if (not isinstance(inner,(LiteralIR,NAIR,ReferenceIR)) or d.value_type.base!=f.base
                or isinstance(d,ConvertIR) and (inner.value_type.base!='int' or d.value_type.base!='float')):
                fail('IR_OBJECT_DEFAULT','Field default differs from the closed literal/builtin-variable profile.')
            fields.add(f.name)
        names.add(n.name);found[n.type_id]=n
    if any(f.name in names for f in program.functions):fail('IR_OBJECT_NAME','Function and type names must be distinct.')
    return found

def admit_operation(node,types,visit,scopes,depth,namespace,fail):
    n=types.get(node.type_id)
    if n is None:fail('IR_OBJECT_TYPE','Unknown nominal type.',node)
    if node.call_site_id!=site_id('object',node.span,namespace):fail('IR_OBJECT_SITE','Object operation identity differs.',node)
    op=node.operation
    if op not in ('new','copy','get','set','is_na'):fail('IR_OBJECT_OPERATION','Unknown object operation.',node)
    if op in ('get','set'):
        if type(node.field_index) is not int or not 0<=node.field_index<len(n.fields):fail('IR_OBJECT_FIELD','Invalid field index.',node)
    elif node.field_index is not None:fail('IR_OBJECT_FIELD','Unexpected field index.',node)
    names=tuple(f.name for f in n.fields) if op=='new' else ('id','value') if op=='set' else ('id',)
    if len(node.arguments)!=len(names) or tuple(a.parameter_id for a in node.arguments)!=names:fail('IR_OBJECT_ARGUMENT','Object arguments differ from their fields/operation.',node)
    sources=[a.source_index for a in node.arguments if a.origin=='source']
    if (any(a.origin not in ('source','default') for a in node.arguments) or any(type(i) is not int for i in sources)
        or sorted(sources)!=list(range(len(sources))) or any(a.origin=='default' and a.source_index is not None for a in node.arguments)):
        fail('IR_OBJECT_ORDER','Invalid source positions.',node)
    order=tuple(sorted(range(len(names)),key=lambda i:(node.arguments[i].origin=='default',node.arguments[i].source_index if node.arguments[i].source_index is not None else i)))
    if type(node.evaluation_order) is not tuple or any(type(i) is not int for i in node.evaluation_order) or node.evaluation_order!=order:
        fail('IR_OBJECT_ORDER','Invalid evaluation order.',node)
    if op!='new' and (any(a.origin!='source' for a in node.arguments) or tuple(a.source_index for a in node.arguments)!=tuple(range(len(names)))):
        fail('IR_OBJECT_RECEIVER','Object receiver must be evaluated first, once.',node)
    for i,a in enumerate(node.arguments):
        if a.origin=='default':
            from packages.contracts.codec import encode
            from packages.contracts.wire import canonical
            if op!='new' or canonical(encode(a.expression))!=canonical(encode(n.fields[i].default)):
                fail('IR_OBJECT_DEFAULT','Inserted default differs from the type declaration.',node)
        visit(a.expression,scopes,depth+1)
        expected=n.fields[i].base if op=='new' else node.type_id if i==0 else n.fields[node.field_index].base
        if a.expression.value_type.base!=expected:fail('IR_OBJECT_ARGUMENT','Object/field argument type differs.',node)
    expected=(ValueType(n.fields[node.field_index].base,'series') if op=='get' else ValueType('void') if op=='set'
        else ValueType('bool','series') if op=='is_na' else ValueType(node.type_id,'series'))
    if node.value_type!=expected:fail('IR_OBJECT_RESULT','Object operation result type differs.',node)

def evaluate(evaluation,node):
    heap=evaluation.heap;heap.owner=evaluation.storage_owner+' / objects / '+str(evaluation.context_id)
    args=[None]*len(node.arguments)
    for position in node.evaluation_order:args[position]=evaluation.evaluate(node.arguments[position].expression)
    if node.operation=='is_na':return args[0] is None
    if node.operation=='new':
        heap.charge(len(args),evaluation,node)
        return heap.create_record(node.type_id,args,evaluation,node)
    ref=args[0];obj=heap.record(ref,node)
    if node.operation=='get':return obj.items[node.field_index]
    if node.operation=='copy':
        heap.charge(len(obj.items),evaluation,node)
        return heap.create_record(node.type_id,obj.items.copy(),evaluation,node)
    from .arrays import string_bytes
    delta=string_bytes(args[1])-string_bytes(obj.items[node.field_index])
    heap.capacity(len(heap.objects),heap.cells,len(obj.items),heap.bytes+delta,node)
    obj=heap.writable(ref,evaluation,node)
    heap.sync(ref.object_id,len(obj.items),obj.string_bytes+delta)
    obj.items[node.field_index]=args[1];obj.string_bytes+=delta;heap.bytes+=delta
    if heap.object_types[node.type_id].fields[node.field_index].intrabar:heap.intrabar_dirty.add(ref.object_id)
    return None
