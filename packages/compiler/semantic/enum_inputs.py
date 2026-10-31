"""Enum inputs reuse frozen scalar settings and the existing input adapters."""
from packages.compiler.frontend import parser
from packages.contracts import EnumInputDefinition,ValueType
from packages.contracts.enums import PROFILE,is_enum,token
from packages.contracts.inputs import validate_definition

def bind(binder,node):
    from .binder import BoundInput,BoundLiteral
    call=node.expression;version=binder.catalog.language_version
    def fail(code,text):binder.fail('LANGUAGE_INVALID',code,text,call.span)
    if version<5:fail('ENUM_VERSION','Enum inputs require Pine v5/v6.')
    if node.mode!='bar':fail('INPUT_DECLARATION','Inputs require ordinary direct global declarations.')
    args={};position=0;named=False;positions=('defval','title');allowed={'defval','title','options','tooltip','inline','group','confirm'}|({'active'} if version==6 else set())
    for a in call.arguments:
        if a.name is None:
            if named or position>=len(positions):fail('ENUM_INPUT_ARGUMENT','Only defval and title are positional.')
            name=positions[position];position+=1
        else:name=a.name;named=True
        if name not in allowed or name in args:fail('ENUM_INPUT_ARGUMENT','Unknown, duplicate or unavailable enum input argument.')
        args[name]=a.expression
    if 'defval' not in args:fail('MISSING_ARGUMENT','Enum input requires defval.')
    def member(n):
        from .enums import member as bind_member
        value=bind_member(binder,n)
        if not isinstance(value,BoundLiteral) or not is_enum(value.value_type.base):fail('ENUM_INPUT_MEMBER','Enum input values require declared members.')
        return value
    default=member(args['defval']);base=default.value_type.base
    enum=next(e for e in binder.enum_types if e.type_id==base)
    options=tuple(token(base,i) for i in range(len(enum.members)))
    if 'options' in args:
        n=args['options']
        if not isinstance(n,parser.InputOptions) or not n.values:fail('ENUM_INPUT_OPTIONS','Expected a nonempty member list.')
        values=tuple(member(x) for x in n.values)
        if any(v.value_type.base!=base for v in values):fail('ENUM_INPUT_TYPE','Enum options must have the default nominal type.')
        options=tuple(v.value for v in values)
    def literal(n):
        if not isinstance(n,parser.Literal) or n.value is None:fail('INPUT_DECLARATION','Input metadata requires literals.')
        return n.value
    if 'confirm' in args and literal(args['confirm']) is not False:fail('INPUT_DECLARATION','Interactive confirm is outside this profile.')
    metadata={k:literal(args[k]) for k in ('tooltip','inline','group','active') if k in args}
    definition=EnumInputDefinition('input:'+node.name,node.name,literal(args['title']) if 'title' in args else node.name,
        base,default.value,options=options,profile_id=PROFILE,enum_name=enum.name,
        member_names=tuple(m.name for m in enum.members),member_titles=tuple(m.title for m in enum.members),**metadata)
    try:validate_definition(definition,version)
    except ValueError as e:fail('ENUM_INPUT_DEFINITION',str(e))
    return BoundInput(definition,ValueType(base,'input'),call.span)
