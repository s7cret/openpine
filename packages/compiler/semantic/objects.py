"""Flat nominal types bind through the existing lexical resolver and call engine."""
from dataclasses import dataclass
from packages.compiler.frontend import parser
from packages.contracts import ValueType,ObjectTypeContract,ObjectFieldContract
from packages.contracts.objects import FIELDS,MAX_TYPES,is_object,admit
from packages.contracts.scopes import site_id
from packages.contracts.overloads import Header,bind_positions

@dataclass(frozen=True)
class BoundObject:
    operation: str
    type_id: str
    field_index: int | None
    arguments: tuple
    evaluation_order: tuple[int,...]
    value_type: ValueType
    span: object
    namespace: str

def type_base(binder,name):
    definitions=binder.shared.get('object_definitions',{})
    if name and name.startswith('array<'):return 'array.'+type_base(binder,name[6:-1])
    if name and name.startswith('array.') and name[6:] in definitions:return 'array.'+type_base(binder,name[6:])
    return site_id('udt',definitions[name].span) if name in definitions else name

def register(binder,statements):
    from .specification import ROOT
    from .binder import BoundLiteral,BoundNA,BoundReference,BoundConversion
    definitions={};defaults={}
    for node in statements:
        if not isinstance(node,parser.ObjectType):continue
        if binder.catalog.language_version<5:binder.fail('LANGUAGE_INVALID','UDT_VERSION','Objects require Pine v5 or v6.',node.span)
        try:admit(ROOT,binder.catalog.snapshot,binder.catalog.language_version)
        except ValueError as e:binder.fail('SPEC_UNRESOLVED','UDT_PROFILE',str(e),node.span)
        if (node.name in definitions or node.name in binder.catalog.signatures or node.name in binder.catalog.references or node.name in binder.shared['namespace_prefixes']
            or node.name in (*FIELDS,'array','type','method','var','varip','if','else','true','false','na')
            or any(isinstance(n,parser.Function) and n.name==node.name for n in statements)):
            binder.fail('LANGUAGE_INVALID','UDT_NAME','Type name must be unique and must not shadow a builtin or function.',node.span)
        if len(definitions)>=MAX_TYPES:binder.fail('PROFILE_UNCOVERED','UDT_LIMIT','At most 32 user-defined types.',node.span)
        if len({f.name for f in node.fields})!=len(node.fields):binder.fail('LANGUAGE_INVALID','UDT_DUPLICATE_FIELD','Type fields must be unique.',node.span)
        definitions[node.name]=node;values=[]
        for f in node.fields:
            value=f.default
            if value is None:
                result=BoundLiteral(False,ValueType('bool'),f.span) if f.annotation=='bool' and binder.catalog.language_version==6 else BoundNA(ValueType(f.annotation),f.span)
            else:
                signed=isinstance(value,parser.Unary) and value.operator in ('+','-') and isinstance(value.operand,parser.Literal) and type(value.operand.value) in (int,float)
                builtin=isinstance(value,parser.Reference) and (value.name in binder.catalog.references or value.name in binder.catalog.constants)
                if not isinstance(value,parser.Literal) and not signed and not builtin:
                    binder.fail('PROFILE_UNCOVERED','UDT_FIELD_DEFAULT','Field defaults require closed literals or admitted builtin variables.',value.span)
                if signed:
                    raw=value.operand.value*(-1 if value.operator=='-' else 1)
                    result=BoundLiteral(raw,ValueType('int' if type(raw) is int else 'float'),value.span)
                else:result=binder.expression(value)
                result=coerce_field(binder,result,f.annotation)
                if isinstance(result,BoundConversion) and isinstance(result.operand,BoundLiteral):
                    result=BoundLiteral(float(result.operand.value),ValueType('float'),result.span)
            values.append(result)
        defaults[site_id('udt',node.span)]=tuple(values)
    binder.shared['object_definitions']=definitions;binder.shared['object_defaults']=defaults
    binder.shared['namespace_prefixes'].update(definitions)
    for node in statements:
        if isinstance(node,parser.Declaration) and node.name in definitions:
            binder.fail('LANGUAGE_INVALID','UDT_NAME','Variable name conflicts with a user-defined type.',node.span)

def contracts(binder):
    from packages.compiler.lowering import lower
    return tuple(ObjectTypeContract(site_id('udt',n.span),n.name,
        tuple(ObjectFieldContract(f.name,f.annotation,f.intrabar,lower(d),f.span) for f,d in zip(n.fields,binder.shared['object_defaults'][site_id('udt',n.span)],strict=True)),n.span)
        for n in binder.shared.get('object_definitions',{}).values())

def definition(binder,base,span):
    for n in binder.shared.get('object_definitions',{}).values():
        if site_id('udt',n.span)==base:return n
    binder.fail('LANGUAGE_INVALID','UDT_RECEIVER','Field access requires a declared object type.',span)

def coerce_field(binder,value,base):
    from .binder import BoundNA
    if isinstance(value,BoundNA) and base=='string':return BoundNA(ValueType('string'),value.span)
    return binder.coerce(value,base,'UDT_FIELD_TYPE')

def operation(binder,op,type_id,args,span,index=None,typ=None):
    from .binder import BoundArgument
    arguments=tuple(BoundArgument(name,e,'source',i) for i,(name,e) in enumerate(args))
    return BoundObject(op,type_id,index,arguments,tuple(range(len(arguments))),typ or ValueType(type_id,'series'),span,binder.namespace)

def field(binder,receiver,name,span,value=None):
    n=definition(binder,receiver.value_type.base,span)
    index=next((i for i,f in enumerate(n.fields) if f.name==name),None)
    if index is None:binder.fail('LANGUAGE_INVALID','UDT_UNKNOWN_FIELD','No field with this name exists in the receiver type.',span)
    args=[('id',receiver)];typ=ValueType(n.fields[index].annotation,'series');op='get'
    if value is not None:
        args.append(('value',coerce_field(binder,binder.expression(value),n.fields[index].annotation)));typ=ValueType('void');op='set'
    return operation(binder,op,site_id('udt',n.span),args,span,index,typ)

def receiver(binder,target):
    from .binder import BoundLoad
    if isinstance(target,parser.FieldAccess):return binder.expression(target.receiver),target.member
    info=binder.resolver.references.get(id(target));name=binder.resolver.field_references.get(id(target))
    if info is None or name is None:binder.fail('LANGUAGE_INVALID','UDT_RECEIVER','Field target requires a visible object variable.',target.span)
    if info.value_type is None:binder.fail('LANGUAGE_INVALID','FUNCTION_CAPTURE_ORDER','Object capture must be initialized before use.',target.span)
    return BoundLoad(info.contract(),info.value_type,target.span),name

def get(binder,node):
    source,name=receiver(binder,node)
    if isinstance(node,parser.FieldAccess) and isinstance(node.receiver,parser.History) and not node.history_parenthesized:
        binder.fail('LANGUAGE_INVALID','UDT_HISTORY_SYNTAX','Parenthesize the historic object before accessing its field.',node.span)
    return field(binder,source,name,node.span)

def assign(binder,node):
    source,name=receiver(binder,node.target)
    return field(binder,source,name,node.span,node.expression)

def named(binder,node):
    from .binder import BoundArgument
    prefix,sep,member=node.name.rpartition('.')
    n=binder.shared.get('object_definitions',{}).get(prefix)
    if n is None:return None
    if node.span.start<n.span.start:binder.fail('LANGUAGE_INVALID','UDT_DECLARATION_ORDER','Declare the type before constructing its objects.',node.span)
    type_id=site_id('udt',n.span)
    if member=='copy':
        if len(node.arguments)!=1 or node.arguments[0].name not in (None,'id'):
            binder.fail('LANGUAGE_INVALID','UDT_COPY_ARGUMENT','Type.copy requires exactly one object argument.',node.span)
        source=binder.coerce(binder.expression(node.arguments[0].expression),type_id,'UDT_COPY_TYPE')
        return operation(binder,'copy',type_id,[('id',source)],node.span)
    if member!='new':binder.fail('PROFILE_UNCOVERED','UDT_OPERATION','Only new/copy are admitted type namespace operations.',node.span)
    h=Header(type_id,tuple(f.name for f in n.fields),tuple(f.annotation for f in n.fields),(None,)*len(n.fields),(False,)*len(n.fields))
    supplied,error=bind_positions(h,tuple(a.name for a in node.arguments))
    if error:binder.fail('LANGUAGE_INVALID',error,'Constructor arguments do not match the declared fields.',node.span)
    prepared=tuple(binder.expression(a.expression) for a in node.arguments)
    args=[]
    for i,(f,default) in enumerate(zip(n.fields,binder.shared['object_defaults'][type_id],strict=True)):
        source=supplied.get(i)
        args.append(BoundArgument(f.name,coerce_field(binder,prepared[source],f.annotation) if source is not None else default,
            'source' if source is not None else 'default',source))
    order=tuple(sorted(range(len(args)),key=lambda i:(args[i].origin=='default',args[i].source_index if args[i].source_index is not None else i)))
    return BoundObject('new',type_id,None,tuple(args),order,ValueType(type_id,'series'),node.span,binder.namespace)
