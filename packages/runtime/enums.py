"""Closed enum registry admission and scalar enum intrinsics."""
import re
from packages.contracts import EnumProgramIR,EnumTypeContract,EnumMemberContract,HISTORICAL_PROFILE_ID,ValueType
from packages.contracts.enums import MAX_TYPES,MAX_MEMBERS,type_id,index,RESERVED,NAMESPACES,unique_members

def admit_types(runtime,program,fail):
    if not isinstance(program,EnumProgramIR):return {}
    if program.schema_version!='openpine.typed-ir.16' or runtime.identity.language_version<5 or runtime.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
        fail('IR_ENUM_SCHEMA','Enums require typed IR .16 and the historical v5/v6 profile.')
    if type(program.enum_types) is not tuple or not 1<=len(program.enum_types)<=MAX_TYPES:fail('IR_ENUM_TYPE','Invalid enum registry.')
    result={};names={t.name for t in program.object_types}|{f.name for f in program.functions}
    namespaces=NAMESPACES;reserved=RESERVED
    for t in program.enum_types:
        if (type(t) is not EnumTypeContract or type(t.members) is not tuple or not 1<=len(t.members)<=MAX_MEMBERS
            or t.type_id!=type_id(t.span,len(t.members)) or t.type_id in result or t.name in names|reserved
            or runtime.identity.language_version==6 and t.name in namespaces or not re.fullmatch(r'[A-Za-z_]\w*',t.name)):
            fail('IR_ENUM_TYPE','Invalid nominal enum declaration.')
        members=set()
        for m in t.members:
            if (type(m) is not EnumMemberContract or type(m.name) is not str or not re.fullmatch(r'[A-Za-z_]\w*',m.name)
                or m.name in members|reserved or type(m.title) is not str or len(m.title)>4096
                or not t.span.start<m.span.start<m.span.end<=t.span.end):fail('IR_ENUM_MEMBER','Invalid enum member.')
            members.add(m.name)
        if not unique_members(t.members):fail('IR_ENUM_MEMBER','Enum member names and titles collide.')
        names.add(t.name);result[t.type_id]=t
    return result

def evaluate(evaluation,node):
    value=evaluation.evaluate(node.operand)
    if node.operation=='is_na':return value is None
    if value is None:return 'NaN'
    t=next(t for t in evaluation.program.enum_types if t.type_id==node.operand.value_type.base)
    return t.members[index(value,t.type_id)].title
