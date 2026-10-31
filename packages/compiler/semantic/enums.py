"""Enum declarations and member resolution share the existing Binder scopes."""
from dataclasses import dataclass
from packages.compiler.frontend import parser
from packages.contracts import ValueType,EnumTypeContract,EnumMemberContract
from packages.contracts.enums import MAX_TYPES,PROFILE,type_id,token,member_qualifier,is_enum,admit,RESERVED,NAMESPACES,unique_members

@dataclass(frozen=True)
class BoundEnum:
    operation: str
    operand: object
    value_type: ValueType
    span: object

def register(binder,statements):
    from .specification import ROOT
    definitions={}
    object_names={n.name for n in statements if isinstance(n,parser.ObjectType)}
    functions={n.name for n in statements if isinstance(n,parser.Function)}
    reserved=RESERVED;namespaces=NAMESPACES
    for n in statements:
        if not isinstance(n,parser.EnumType):continue
        if binder.catalog.language_version<5:binder.fail('LANGUAGE_INVALID','ENUM_VERSION','Enums require Pine v5/v6.',n.span)
        try:admit(ROOT,binder.catalog.snapshot,binder.catalog.language_version)
        except ValueError as e:binder.fail('SPEC_UNRESOLVED','ENUM_PROFILE',str(e),n.span)
        if n.name in definitions or n.name in object_names|functions|reserved or binder.catalog.language_version==6 and n.name in namespaces:
            binder.fail('LANGUAGE_INVALID','ENUM_NAME','Enum name conflicts with another type, function or reserved namespace.',n.span)
        if len(definitions)>=MAX_TYPES:binder.fail('PROFILE_UNCOVERED','ENUM_LIMIT','Too many enum types.',n.span)
        if len({m.name for m in n.members})!=len(n.members):binder.fail('LANGUAGE_INVALID','ENUM_DUPLICATE_MEMBER','Enum member names must be unique.',n.span)
        if any(m.name in RESERVED for m in n.members):binder.fail('LANGUAGE_INVALID','ENUM_MEMBER','Enum member name is reserved.',n.span)
        if not unique_members(n.members):binder.fail('LANGUAGE_INVALID','ENUM_DUPLICATE_TITLE','Member names and titles must not collide with another member.',n.span)
        if any(len(m.title)>4096 for m in n.members):binder.fail('PROFILE_UNCOVERED','ENUM_TITLE','Enum title exceeds 4096 characters.',n.span)
        definitions[n.name]=EnumTypeContract(type_id(n.span,len(n.members)),n.name,tuple(EnumMemberContract(m.name,m.title,m.span) for m in n.members),n.span)
    binder.shared['enum_definitions']=definitions
    binder.shared['namespace_prefixes'].update(definitions)
    for n in statements:
        if isinstance(n,parser.Declaration) and n.name in definitions:binder.fail('LANGUAGE_INVALID','ENUM_NAME','Variable name conflicts with an enum type.',n.span)

def member(binder,node):
    from .binder import BoundLiteral
    if not isinstance(node,parser.Reference) or '.' not in node.name:return None
    if binder.resolver.references.get(id(node)) is not None:return None
    name,_,field=node.name.partition('.');n=binder.shared.get('enum_definitions',{}).get(name)
    if n is None:return None
    if node.span.start<n.span.start:binder.fail('LANGUAGE_INVALID','ENUM_DECLARATION_ORDER','Declare the enum before use.',node.span)
    i=next((i for i,m in enumerate(n.members) if m.name==field),None)
    if i is None:binder.fail('LANGUAGE_INVALID','ENUM_UNKNOWN_MEMBER','Unknown enum member.',node.span)
    return BoundLiteral(token(n.type_id,i),ValueType(n.type_id,member_qualifier(binder.catalog.language_version)),node.span)

def call(binder,node,expressions):
    if node.name not in ('str.tostring','na') or len(expressions)!=1 or not is_enum(expressions[0].value_type.base):return None
    if node.arguments[0].name not in (None,'value' if node.name=='str.tostring' else 'x'):
        binder.fail('LANGUAGE_INVALID','ENUM_ARGUMENT','Unknown enum operation argument.',node.span)
    operand=expressions[0]
    from packages.contracts.enums import operation_type
    op='title' if node.name=='str.tostring' else 'is_na'
    return BoundEnum(op,operand,operation_type(op,operand.value_type),node.span)
