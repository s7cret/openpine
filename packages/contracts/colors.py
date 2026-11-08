"""Bounded color ABI. Values use canonical RGBA tokens; types remain nominal.

The wire token is also CSS-compatible. A Pine string cannot implicitly become
a color, and a color cannot participate in string or numeric operations.
"""
import math
import re
from fractions import Fraction
from .wire import digest, read_json
from .visual_display import with_display
from .visual_panes import (FLAG, VIEW_FIELDS, WINDOW, OFFSET, overlay_api, placement, window_api,
                           validate_plot_view, validate_call_view, matches_default)

PROFILE = 'openpine.colors.1'
PRESENTATION_SCHEMA = 'openpine.plot-presentation.1'
STYLES = ('line', 'linebr', 'stepline', 'histogram', 'columns', 'circles', 'cross', 'area',
          'areabr', 'steplinebr', 'stepline_diamond')
PLOT_LINE_STYLE = 'plot_line_style'
LINE_STYLES = ('solid', 'dashed', 'dotted')
PLOT_OPTIONS = ('trackprice', 'histbase', 'join', 'linestyle')
APPEARANCE_SUFFIX = '.appearance'
ARCHIVE_STYLE_FIRST = '.style_first'
PALETTE = dict(aqua='00BCD4', black='363A45', blue='2196F3', fuchsia='E040FB',
    gray='787B86', green='4CAF50', lime='00E676', maroon='880E4F', navy='311B92',
    olive='808000', orange='FF9800', purple='9C27B0', red='FF5252', silver='B2B5BE',
    teal='00897B', white='FFFFFF', yellow='FFEB3B')

def valid(value):
    return value is None or type(value) is str and re.fullmatch(r'#[0-9A-F]{8}', value) is not None

def literal(text):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', text):
        raise ValueError('Color literals require six or eight hexadecimal digits.')
    return text.upper() + ('FF' if len(text) == 7 else '')

def styles(version):
    return [s for s in STYLES if (version>=3 or s!='stepline')
            and (version>=5 or s not in ('steplinebr','stepline_diamond'))]

def constants(version):
    palette = PALETTE | (dict(red='F23645', teal='089981', yellow='FDD835') if version == 6 else {})
    # The early palette is an explicitly documented reconstruction, not a
    # claim that current TradingView colors reproduce its historical renderer.
    result = {(('color.' if version >= 4 else '') + n): ('#' + c + 'FF', 'color') for n,c in palette.items()}
    result.update({('plot.style_' + s if version >= 4 else s): ('plot_style.' + s, 'plot_style') for s in styles(version)})
    if version==6:
        result.update({f'plot.linestyle_{s}':(f'{PLOT_LINE_STYLE}.{s}',PLOT_LINE_STYLE) for s in LINE_STYLES})
    return result

def policy(v):
    return {'profile_id':PROFILE, 'language_version':v, 'constants_namespace':'color' if v>=4 else 'global',
        'palette':{k:a for k,(a,t) in constants(v).items() if t=='color'},
        'legacy_palette':'v1-v3 reconstruction from historical names and v5 RGB baseline',
        'hex':'six_or_eight_digits_RGBA;early_versions_reconstructed', 'representation':'canonical_uppercase_RGBA_token_typed_as_color',
        'legacy_constructor':v in (2,3), 'modern_functions':v>=4, 'containers':'existing_version_boundaries',
        'channels':'round_half_up;RGB0..255;transparency0..100;alpha=255*(100-transparency)/100;strict_finite_bounds',
        'gradient':'clamped_linear_RGBA;exact_ratio_of_int64_or_finite_binary_float_inputs;round_half_up;missing_endpoint_transparent_other_RGB;missing_numeric_missing_color',
        'plot_styles':styles(v), 'linewidth':'0..64_before_v6;1..64_v6;input_or_const',
        'transp_parameter':'v1-v5_input_int_0..100;only_overrides_opaque_color',
        'plot_positional_parameters':6 if v<3 else 5,
        'plot_window':v>=3,
        'plot_appearance':{'legacy_positional':v<3,'histbase':0.0,'trackprice':False,'join':False,
                           'linestyle':list(LINE_STYLES) if v==6 else []},
        'requests':'existing_numeric_bool_request_profile_unchanged', 'external_execution_verified':False}

def apis(v):
    def row(kind,name,params,result,defaults=(),rule='derived',effects=()):
        return dict(id=f'openpine.colors.v{v}.{kind}', name=name, operation='colors.'+kind,
            parameters=[dict(name=n,types=list(t),qualifier=q) for n,t,q in params],
            result=result, defaults=list(defaults), result_rule=rule, effects=list(effects), named=v>=3)
    num=('int','float'); color=('color',); rows=[row('is_na','na',[('x',color,'series')],'bool')]
    if v in (2,3):rows.append(row('new','color',[('color',color,'series'),('transp',('int',),'const')],'color'))
    if v>=4:
        rows += [row('cast','color',[('x',color,'series')],'color'),
            row('new','color.new',[('color',color,'series'),('transp',num,'series')],'color'),
            row('rgb','color.rgb',[(n,num,'series') for n in ('red','green','blue','transp')],'color',(0,)),
            row('gradient','color.from_gradient',[(n,num,'series') for n in ('value','bottom_value','top_value')]+[(n,color,'series') for n in ('bottom_color','top_color')],'color',rule='fixed_series')]
        rows += [row(n,'color.'+n,[('color',color,'series')],'float') for n in ('r','g','b','t')]
    # A separate operation preserves the old two-argument plot ABI byte for
    # byte. Binder chooses this extension only for an explicitly styled plot.
    params=[('series',num,'series'),('title',('string',),'const'),('color',color,'series'),
        ('linewidth',('int',),'input'),('style',('plot_style',),'input')]
    defaults=['',constants(v)['color.blue' if v>=4 else 'blue'][0],1,'plot_style.line']
    if v<6:params.append(('transp',('int',),'input'));defaults.append(0)
    rows.append(row('plot','plot',params,'void',defaults,rule='fixed',effects=('emits_plot',)))
    plot = rows[-1]
    if v>=5:rows.append(overlay_api(plot))
    if v>=3:
        window = window_api(plot,v,shifted=True)
        rows.append(overlay_api(window) if v>=5 else window)
    rows.extend(appearance_apis(plot,v))
    return with_display(rows,v)


def appearance_apis(plot, version):
    track = dict(name='trackprice', types=['bool'], qualifier='input')
    base = dict(name='histbase', types=['float','int'], qualifier='input')
    join = dict(name='join', types=['bool'], qualifier='input')
    api = dict(plot, id=plot['id'] + APPEARANCE_SUFFIX)
    if version<3:
        series,title,color,width,style,transp=plot['parameters']
        api.update(parameters=[series,title,color,width,style,track,transp,base,
                               dict(name=OFFSET,types=['int'],qualifier='input'),join],
                   defaults=[*plot['defaults'][:-1],False,0,0.0,0,False])
        alternate = dict(api,id=api['id']+ARCHIVE_STYLE_FIRST,
                         parameters=[series,title,color,style,width,*api['parameters'][5:]],
                         defaults=[*api['defaults'][:2],api['defaults'][3],api['defaults'][2],*api['defaults'][4:]])
        return (api,alternate)
    window=window_api(plot,version,shifted=True)
    api.update(parameters=[*window['parameters'],track,base,join],defaults=[*window['defaults'],False,0.0,False])
    if version==6:
        api['parameters'].append(dict(name='linestyle',types=[PLOT_LINE_STYLE],qualifier='input'))
        api['defaults'].append(PLOT_LINE_STYLE+'.solid')
    return (overlay_api(api) if version>=5 else api,)

def contracts(v):
    from .model import CallContract, ValueType
    return {a['id']:CallContract('symbol.'+a['id'],'overload.'+a['id'],'assertion.'+a['id'],digest(a),a['operation'],
        tuple('parameter.'+a['id']+'.'+p['name'] for p in a['parameters']),tuple(tuple(p['types']) for p in a['parameters']),
        tuple(p['qualifier'] for p in a['parameters']),None,tuple(a['effects']),ValueType(a['result']),a['result_rule']) for a in apis(v)}

def validate_call(node,v):
    spec=next((a for a in apis(v) if 'overload.'+a['id']==node.overload_id),None)
    if spec is None:raise ValueError('Color operation is unavailable in this version.')
    start=len(spec['parameters'])-len(spec['defaults'])
    for i,arg in enumerate(node.arguments):
        if arg.origin=='default' and (i<start or not matches_default(arg.expression,spec['defaults'][i-start])):
            raise ValueError('Color default differs from its closed signature.')

def uses_colors(program):
    from dataclasses import fields,is_dataclass
    from .model import ValueType,ParameterContract
    if isinstance(program,ParameterContract) and program.annotation and ('color' in program.annotation or program.annotation==PLOT_LINE_STYLE):return True
    if isinstance(program,ValueType):return any(t in program.base for t in ('color','plot_style',PLOT_LINE_STYLE))
    if getattr(program,'operation','').startswith('colors.'):return True
    if is_dataclass(program):return any(uses_colors(getattr(program,f.name)) for f in fields(program))
    if type(program) is tuple:return any(map(uses_colors,program))
    return False

def admit(root,snapshot,v):
    value=read_json(root/'spec/colors-profile.json')
    if (value.get('schema_version')!='openpine.colors-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock)
        or value['versions'][str(v)]!={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(snapshot.references[v])}):
        raise ValueError('Color profile differs from pinned evidence.')

def _number(value,lo,hi):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError(f'Color component requires a finite number in {lo}..{hi}.')
    return value

def _round(value):
    if isinstance(value,Fraction):return (2*value.numerator+value.denominator)//(2*value.denominator)
    return math.floor(value+.5)
def components(value):return tuple(int(value[i:i+2],16) for i in (1,3,5,7))
def rgba(values):return '#'+''.join(f'{_round(x):02X}' for x in values)

def invoke(operation,args):
    kind=operation.removeprefix('colors.')
    if kind=='is_na':return args[0] is None
    if kind=='cast':return args[0]
    if kind=='gradient':
        x,lo,hi,a,b=args
        if any(n is None for n in (x,lo,hi)) or a is None and b is None:return None
        if not all(type(n) in (int,float) and math.isfinite(n) for n in (x,lo,hi)) or lo>=hi:
            raise ValueError('Color gradient requires finite increasing bounds.')
        if x<=lo:t=0
        elif x>=hi:t=1
        else:
            # Mixed subtraction can round a positive width to zero. Inputs are
            # bounded int64/finite doubles, so their exact ratio has bounded
            # precision; no source-controlled rational representation enters IR.
            t=(Fraction(x)-Fraction(lo))/(Fraction(hi)-Fraction(lo))
        ac=components(a) if a is not None else (*components(b)[:3],0)
        bc=components(b) if b is not None else (*components(a)[:3],0)
        return rgba([p+(q-p)*t for p,q in zip(ac,bc,strict=True)])
    if any(n is None for n in args):return None
    if kind=='new':return args[0][:7]+f'{_round(255*(100-_number(args[1],0,100))/100):02X}'
    if kind=='rgb':return rgba([_number(n,0,255) for n in args[:3]]+[255*(100-_number(args[3],0,100))/100])
    if kind in ('r','g','b','t'):
        c=components(args[0]);return float(c['rgb'.index(kind)]) if kind!='t' else 100*(255-c[3])/255
    raise ValueError('Unknown color operation.')

def plot_style(value):return type(value) is str and value.removeprefix('plot_style.') in STYLES and value.startswith('plot_style.')

def plot_line_style(value):
    return type(value) is str and value.startswith(PLOT_LINE_STYLE+'.') and value.rsplit('.',1)[1] in LINE_STYLES


def validate_plot_options(value, version):
    if not any(name in value for name in PLOT_OPTIONS):
        return
    if (not all(name in value for name in (*PLOT_OPTIONS,*VIEW_FIELDS))
            or type(value['trackprice']) is not bool or type(value['join']) is not bool
            or type(value['histbase']) not in (int,float) or not math.isfinite(value['histbase'])
            or value['linestyle'] not in (LINE_STYLES if version==6 else ('solid',))):
        raise ValueError('Invalid plot appearance.')


def validate_plot_call_options(value, node):
    params = {arg.parameter_id.rsplit('.',1)[-1]:arg.expression for arg in node.arguments}
    appearance=APPEARANCE_SUFFIX in node.overload_id
    if appearance:
        from .model import LiteralIR
        for name in PLOT_OPTIONS:
            expr=params.get(name)
            if name not in value or expr is None and value[name]!='solid':
                raise ValueError('Plot appearance differs from its call.')
            if isinstance(expr,LiteralIR):
                expected=expr.value.rsplit('.',1)[1] if name=='linestyle' else expr.value
                if value[name]!=expected:
                    raise ValueError('Plot appearance differs from its call.')
    elif any(name in value for name in PLOT_OPTIONS):
        raise ValueError('Plot appearance differs from its call.')
    view = value
    if appearance and WINDOW not in params and value.get(WINDOW) is None:
        view={k:v for k,v in value.items() if k!=WINDOW}
    validate_call_view(view,node,shifted=True)


def validate_presentation(value,version):
    if (type(value) is not dict or value.keys()-{FLAG,*VIEW_FIELDS,*PLOT_OPTIONS}!={'color','linewidth','style'} or not valid(value['color'])
        or type(value['linewidth']) is not int or not (1 if version==6 else 0)<=value['linewidth']<=64
        or value['style'] not in styles(version)):raise ValueError('Invalid bounded plot presentation.')
    placement(value,version)
    validate_plot_options(value,version)
    validate_plot_view(value,version,legacy='histbase' in value)
    return value


def stored_plot_version(value):
    # Durable descriptors predate the v6 width restriction.
    return 6 if 'linestyle' in value and value['linestyle']!='solid' else 5

def presentation_metadata(value):
    from .visual_display import FIELD, split_metadata
    value, display = split_metadata(value)
    meta = _presentation_metadata(value)
    if display is not None:
        meta[FIELD] = display
    return meta


def _presentation_metadata(value):
    if type(value) is dict and value.get('schema_version')=='openpine.gradient-fill.1':
        from .gradient_fill import metadata
        return metadata(value)
    if type(value) is dict and value.get('schema_version')=='openpine.ohlc-layer.1':
        from .visual_ohlc import metadata
        return metadata(value)
    if type(value) is dict and value.get('schema_version')=='openpine.signal-layer.1':
        from .markers import metadata
        return metadata(value)
    if type(value) is dict and value.get('schema_version')=='openpine.visual-layer.1':
        from .bands import metadata
        return metadata(value)
    if type(value) is not dict or value.get('schema_version')!=PRESENTATION_SCHEMA:
        raise ValueError('Unknown plot presentation metadata schema.')
    meta = {k:v for k,v in value.items() if k!='schema_version'}
    if 'color' in meta:
        raise ValueError('Color belongs to a plot row.')
    validate_presentation({**meta,'color':None},stored_plot_version(meta))
    return {'presentation_schema':PRESENTATION_SCHEMA,**meta}
