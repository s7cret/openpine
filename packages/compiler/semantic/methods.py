"""Method sugar binds once and lowers through existing UDF/array nodes."""
from packages.compiler.frontend import parser
from packages.contracts import ValueType
from packages.contracts.methods import key,builtin,resolve,admit,RECEIVERS,INSTANCE_OPERATIONS
from packages.contracts.overloads import bind_positions,base
from packages.contracts.scopes import site_id
from packages.contracts.objects import is_object


def bind(binder,node):
    v=binder.catalog.language_version
    if v<5:binder.fail('LANGUAGE_INVALID','METHOD_VERSION','Methods require Pine v5 or v6.',node.span)
    from .specification import ROOT
    try:admit(ROOT,binder.catalog.snapshot,v)
    except ValueError as e:binder.fail('SPEC_UNRESOLVED','METHOD_PROFILE',str(e),node.span)
    receiver=binder.expression(node.receiver)
    if receiver.value_type.base not in RECEIVERS and not is_object(receiver.value_type.base):binder.fail('LANGUAGE_INVALID','METHOD_RECEIVER_TYPE','Method receiver requires an admitted scalar/reference type.',node.span)
    group=binder.visible_functions(key(node.member)) if key(node.member) in binder.shared['groups'] else []
    intrinsic=builtin(node.member,receiver.value_type.base)
    if not group and intrinsic is None:binder.fail('LANGUAGE_INVALID','METHOD_UNAVAILABLE','No method is admitted for this name/receiver.',node.span)
    arguments=(parser.Argument(None,node.receiver,node.receiver.span),*node.arguments)
    names=tuple(a.name for a in arguments)
    headers=tuple(binder.function_header(n) for n in group)
    matching=tuple(h for h in headers if h.types[0]==receiver.value_type.base)
    if not matching and intrinsic is None:binder.fail('LANGUAGE_INVALID','METHOD_RECEIVER_TYPE','No method has this receiver type.',node.span)
    shaped=(*matching,*((intrinsic,) if intrinsic else ()))
    errors=[bind_positions(h,names)[1] for h in shaped]
    if all(errors):
        code=errors[0] if len(set(errors))==1 else 'METHOD_NO_MATCH'
        binder.fail('LANGUAGE_INVALID',code,'No method accepts these argument names/count.',node.span)
    prepared=(receiver,*(binder.expression(a.expression) for a in node.arguments))
    try:chosen=resolve(matching,intrinsic,names,tuple(e.value_type for e in prepared),v)
    except ValueError as e:binder.fail('LANGUAGE_INVALID',str(e),'No unique method matches the receiver and arguments.',node.span)
    if chosen.function_id.startswith('builtin.'):
        if is_object(receiver.value_type.base):
            from .objects import operation
            return operation(binder,'copy',receiver.value_type.base,[('id',receiver)],node.span)
        from .arrays import bind as bind_array
        return bind_array(binder,parser.Call('array.'+node.member,arguments,node.span),prepared=prepared)
    return binder.user_call(parser.Call(key(node.member),arguments,node.span),
        definition=binder.shared['definitions'][chosen.function_id],prepared=prepared)


def static_edges(binder,node,definition,function_id,resolver,local):
    group=binder.visible_functions(key(node.member),function_id) if key(node.member) in binder.shared['groups'] else []
    if not group and node.member not in INSTANCE_OPERATIONS:
        binder.fail('LANGUAGE_INVALID','METHOD_UNAVAILABLE','Unknown method in a function body.',node.span)
    params={p.name:p for p in definition.parameters}
    def typ(e):
        if isinstance(e,parser.Reference) and e.name in params and resolver.references.get(id(e)) is local[e.name]:
            p=params[e.name]
            return ValueType(binder.type_base(base(p.annotation)),'series' if local[e.name].mutable else p.qualifier or 'series') if p.annotation else None
        if isinstance(e,parser.Literal):
            from .binder import literal_type
            return ValueType('na') if e.value is None else literal_type(e.value)
        return None
    types=tuple(typ(e) for e in (node.receiver,*(a.expression for a in node.arguments)))
    names=(None,*(a.name for a in node.arguments))
    if all(t is not None for t in types):
        try:h=resolve(tuple(binder.function_header(n) for n in group),builtin(node.member,types[0].base),names,types,binder.catalog.language_version)
        except ValueError:return set()
        return set() if h.function_id.startswith('builtin.') else {h.function_id}
    shaped=[n for n in group if bind_positions(binder.function_header(n),names)[1] is None]
    if len(shaped)==1 and node.member not in INSTANCE_OPERATIONS:return {site_id('function',shaped[0].span)}
    return set()
