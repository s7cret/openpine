"""Bind the versioned input slice without evaluating arbitrary source expressions."""
from packages.compiler.frontend import parser
from packages.contracts import InputDefinition,ValueType
from packages.contracts.inputs import input_policy,validate_definition


def is_input(name):return name=='input' or name.startswith('input.')


def bind(binder,node):
    if node.expression.name=='input.enum':
        from .enum_inputs import bind as bind_enum
        return bind_enum(binder,node)
    from .binder import BoundInput
    call=node.expression;version=binder.catalog.language_version;policy=input_policy(version)
    release=binder.catalog.snapshot.inputs
    if not release or release['versions'][str(version)]['policy']!=policy:
        binder.fail('SPEC_UNRESOLVED','INPUT_PROFILE','No admitted input policy for this version.',call.span)
    def fail(message):binder.fail('PROFILE_UNCOVERED','INPUT_DECLARATION',message,call.span,'rule.inputs')
    if node.mode!='bar':fail('Inputs require an ordinary direct global declaration.')
    typed=call.name.removeprefix('input.') if call.name!='input' else None
    if typed is not None and (version<5 or typed not in ('int','float','bool','string','source','symbol','timeframe')):fail('This input function is unavailable in the selected version/profile.')
    if typed in ('symbol','timeframe'):
        from packages.contracts.primary_targets import admit_profile
        from .specification import ROOT
        try:admit_profile(ROOT,binder.catalog.snapshot,version)
        except ValueError as error:fail(str(error))
    args={};positional=0;named=False;positions=['defval','title']+(['type'] if version<5 else [])
    allowed={'defval','title','confirm'}
    if version<5:allowed|={'type','minval','maxval','step'}
    if version>=3:allowed.add('options')
    if typed in ('int','float'):allowed|={'minval','maxval','step'}
    if version>=4:allowed|={'tooltip','inline','group'}
    if version==6:allowed.add('active')
    for argument in call.arguments:
        if argument.name is None:
            if named or positional>=len(positions):fail('Only defval/title and legacy type are positional in this profile; name other arguments.')
            name=positions[positional];positional+=1
        else:name=argument.name;named=True
        if name not in allowed or name in args:fail('Unknown, unavailable or duplicate input argument: '+name)
        args[name]=argument.expression
    if 'defval' not in args:fail('Input requires defval.')
    def literal(value):
        if isinstance(value,parser.Literal) and value.value is not None:return value.value
        if isinstance(value,parser.Unary) and value.operator in ('+','-') and isinstance(value.operand,parser.Literal) and type(value.operand.value) in (int,float):
            return value.operand.value if value.operator=='+' else -value.operand.value
        fail('Input metadata requires scalar literals; source requires a supported OHLCV name.')
    default_node=args['defval'];source=isinstance(default_node,parser.Reference) and default_node.name in policy['source_slots']
    default=default_node.name if source else literal(default_node)
    kind=typed or ('source' if source else {int:'int',float:'float',bool:'bool',str:'string'}[type(default)])
    if 'type' in args:
        raw=args['type'];value=raw.name if isinstance(raw,parser.Reference) else literal(raw)
        types={'integer':'int','float':'float','bool':'bool','string':'string','source':'source'}
        if isinstance(raw,parser.Reference):
            if version==4:
                if not value.startswith('input.'):fail('v4 type constants require input.*.')
                value=value.removeprefix('input.')
            elif value.startswith('input.'):fail('This version uses bare legacy input type constants.')
        if value not in types:fail('Unsupported legacy input type.')
        kind=types[value]
    if kind=='source' and not source or source and kind!='source':fail('Source inputs require a supported OHLCV default.')
    if source and default not in binder.catalog.references:fail('Source inputs require the historical profile.')
    if kind=='float' and type(default) is int:default=float(default)
    if 'confirm' in args and literal(args['confirm']) is not False:fail('Interactive confirm is outside this input profile; use false.')
    options=()
    if 'options' in args:
        if not isinstance(args['options'],parser.InputOptions):fail('Input options require a literal list.')
        options=tuple(literal(v) for v in args['options'].values)
        if not options:fail('Input options cannot be empty.')
        if kind=='float':options=tuple(float(v) if type(v) is int else v for v in options)
    metadata={key:literal(args[key]) for key in ('tooltip','group','inline','active') if key in args}
    ranges={key:literal(args[name]) for name,key in (('minval','minimum'),('maxval','maximum'),('step','step')) if name in args}
    if kind=='float':ranges={k:float(v) if type(v) is int else v for k,v in ranges.items()}
    from packages.contracts.primary_targets import input_profile
    definition=InputDefinition('input:'+node.name,node.name,literal(args['title']) if 'title' in args else node.name,
        kind,default,**ranges,options=options,**metadata,profile_id=input_profile(version) if kind in ('symbol','timeframe') else policy['profile_id'])
    try:validate_definition(definition,version)
    except ValueError as error:fail(str(error))
    return BoundInput(definition,ValueType('float' if kind=='source' else 'string' if kind in ('symbol','timeframe') else kind,'series' if kind=='source' else 'input'),call.span)
