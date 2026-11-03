"""Color input defaults are closed constant expressions, never runtime code."""
from packages.compiler.frontend import parser
from packages.contracts import InputDefinition,ValueType
from packages.contracts.inputs import validate_definition
from packages.contracts.bands import PROFILE,admit
from .specification import ROOT

def selected(binder,node,args,typed):
    v=binder.catalog.language_version;default=args['defval'];raw=args.get('type')
    explicit=getattr(raw,'name',getattr(raw,'value',None)) in ('color','input.color')
    info=binder.resolver.references.get(id(default)) if isinstance(default,parser.Reference) else None
    candidate=(isinstance(default,parser.ColorLiteral) or isinstance(default,parser.Reference) and
        (getattr(binder.catalog,'color_constants',{}).get(default.name,(None,None))[1]=='color' or getattr(getattr(info,'value_type',None),'base',None)=='color') or
        isinstance(default,parser.Call) and default.name in ('color','color.new','color.rgb'))
    if not (candidate or explicit or typed=='color'):return None
    def fail(message):binder.fail('PROFILE_UNCOVERED','COLOR_INPUT',message,node.span)
    if v<4:fail('Color inputs were introduced in Pine v4.')
    if typed not in (None,'color'):fail('Color default requires a color input.')
    if raw is not None:
        expected='input.color' if v==4 and isinstance(raw,parser.Reference) else 'color'
        if getattr(raw,'name',getattr(raw,'value',None))!=expected:fail('Wrong legacy color input type for this version.')
    if any(k in args for k in ('options','minval','maxval','step')):fail('Color inputs have no options or numeric constraints.')
    try:admit(ROOT,binder.catalog.snapshot,v)
    except ValueError as error:fail(str(error))
    from .binder import BoundLiteral,BoundLoad,BoundCall,BoundConversion,BoundUnary,BoundInput
    from packages.contracts.colors import invoke
    def constant(value,depth=0):
        if depth>16 or value.value_type.qualifier!='const':fail('Color input default must be a bounded constant.')
        if isinstance(value,BoundLiteral):return value.value
        if isinstance(value,BoundLoad):
            other=binder.shared.get('constant_bindings',{}).get(value.binding.binding_id)
            if other is not None:return constant(other,depth+1)
        if isinstance(value,BoundConversion):return float(constant(value.operand,depth+1))
        if isinstance(value,BoundUnary) and value.operator in ('+','-'):
            n=constant(value.operand,depth+1);return n if value.operator=='+' else -n
        if isinstance(value,BoundCall) and value.contract.operation.startswith('colors.') and not value.contract.effects:
            try:return invoke(value.contract.operation,tuple(constant(a.expression,depth+1) for a in value.arguments))
            except ValueError as error:fail(str(error))
        fail('Color default permits HEX, palette constants and closed color constructors.')
    expression=binder.expression(default)
    if expression.value_type.base!='color':fail('Color input default is a color, not a HEX-looking string.')
    value=constant(expression)
    def literal(key,fallback):
        if key not in args:return fallback
        n=args[key]
        if type(n) is not parser.Literal:fail('Input metadata must be literal.')
        return n.value
    if literal('confirm',False) is not False:fail('Interactive confirmation is outside this profile.')
    definition=InputDefinition('input:'+node.name,node.name,literal('title',node.name),'color',value,
        tooltip=literal('tooltip',''),group=literal('group',''),inline=literal('inline',''),active=literal('active',True),profile_id=PROFILE)
    validate_definition(definition,v)
    return BoundInput(definition,ValueType('color','input'),node.expression.span)
