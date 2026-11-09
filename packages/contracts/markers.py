"""Versioned, bar-aligned signal outputs. No mutable drawing identities."""
from dataclasses import fields,is_dataclass
from .wire import digest,read_json
from .colors import valid
from .visual_panes import (FLAG, WINDOW, WINDOW_SUFFIX, OFFSET_LIMIT, overlay_api, placement,
                           window_api, validate_window, validate_call_view, matches_default)
from .visual_ohlc import OPERATIONS as OHLC_OPERATIONS
from .visual_display import DESTINATION_SUFFIX, with_display

PROFILE='openpine.visual-signals.1'
SCHEMA='openpine.signal-layer.1'
ARROW = 'plotarrow'
SHAPE_KINDS = ('plotshape', 'plotchar')
MARKER_KINDS = (*SHAPE_KINDS, ARROW)
SHAPE_OPERATIONS = tuple('markers.' + name for name in SHAPE_KINDS)
ARROW_HEIGHTS = ('minheight', 'maxheight')
KINDS=('bgcolor','barcolor',*MARKER_KINDS)
WINDOW_KINDS = KINDS
OUTPUTS=tuple('markers.'+k for k in KINDS)
GLOBAL_OUTPUTS=(*OUTPUTS,*OHLC_OPERATIONS)
SHAPES=('xcross','cross','circle','triangleup','triangledown','flag','arrowup','arrowdown','square','diamond','labelup','labeldown')
LOCATIONS=('abovebar','belowbar','top','bottom','absolute')
SIZES=('auto','tiny','small','normal','large','huge')
STATIC=('linewidth','style','kind','location','size','offset','text','char')

def has_source_samples(call):
    return call.operation in SHAPE_OPERATIONS and call.overload_id.endswith(DESTINATION_SUFFIX)


def policy(v):
    return dict(profile_id=PROFILE,language_version=v,scope='direct_global_calls_only',
        legacy_basis='v1 primary archive; v2 compatibility reconstruction; v3-v6 primary pinned reference',
        series='bool/int/float; zero/false/na hidden except numeric zero at location.absolute',
        color='series RGBA; na does not paint; barcolor last non-na source call wins',
        locations=list(LOCATIONS),shapes=list(SHAPES),sizes=list(SIZES),
        offset='input/const int -10000..10000; static subset of pre-v6 series offsets',
        size_argument=v>=3,size_qualifier='const',style_location_char_qualifier='input',text_qualifier='const',
        transp=v<6,positional='legacy order through textcolor v1-v4; modern order v5-v6; extra args named only',
        default_choices=dict(color='#2196F3FF',textcolor='#2196F3FF',char='★',text='',title='',bgcolor_transp=90,marker_transp=0),
        defaults_evidence='offset=0,shape.xcross,location.abovebar,size.auto explicit pinned claims; other defaults are OpenPine choices',
        missing_ohlc='close line retained; above/below fall back to close; barcolor never colors the close line',
        arrow=dict(series='int/float; signed magnitude; zero and missing are hidden',
            color_qualifier='const' if v<=3 else 'series',
            heights='1..1000 CSS pixels; defaults 5 and 100',transp=v<=4),
        show_last='named nonnegative int applied before offset' if v>=3 else None,
        excluded=['editable',*(['display'] if v<4 else ['display_destinations'] if v==4 else []),'format','precision',
                  *([FLAG] if v<5 else []),'dynamic_offsets','drawing_objects'],
        external_execution_verified=False)

def apis(v):
    rows=[]
    def row(kind,ps,defaults):
        rows.append(dict(id=f'openpine.visual-signals.v{v}.{kind}',name=kind,operation='markers.'+kind,
            parameters=[dict(name=n,types=list(t),qualifier=q) for n,t,q in ps],defaults=defaults,
            result='void',result_rule='fixed',effects=['emits_plot'],named=v>=3))
    color=('color',);string=('string',);integer=('int',)
    for kind in ('bgcolor','barcolor'):
        ps=[('color',color,'series')];ds=[]
        if kind=='bgcolor' and v<=4:ps.append(('transp',integer,'input'));ds.append(90)
        ps.append(('offset',integer,'input'));ds.append(0)
        if v>=3:ps.append(('title',string,'const'));ds.append('')
        if kind=='bgcolor' and v==5:ps.append(('transp',integer,'input'));ds.append(90)
        row(kind,ps,ds)
    for kind in ('plotshape','plotchar'):
        ps=[('series',('int','float','bool'),'series'),('title',string,'const'),
            ('style' if kind=='plotshape' else 'char',string,'input'),('location',string,'input'),('color',color,'series')]
        ds=['','xcross' if kind=='plotshape' else '★','abovebar','#2196F3FF']
        if v<=4:ps.append(('transp',integer,'input'));ds.append(0)
        ps.extend([('offset',integer,'input'),('text',string,'const'),('textcolor',color,'series')]);ds.extend([0,'','#2196F3FF'])
        if v>=3:ps.append(('size',string,'const'));ds.append('auto')
        if v==5:ps.append(('transp',integer,'input'));ds.append(0)
        row(kind,ps,ds)
    qualifier = 'const' if v<=3 else 'series'
    params = [('series', ('int','float'), 'series'), ('title',string,'const'),
              ('colorup',color,qualifier), ('colordown',color,qualifier)]
    defaults = ['', '#00BCD4FF', '#F23645FF']
    if v<=4:
        params.append(('transp',integer,'input'))
        defaults.append(0)
    params.extend((name, integer, 'input') for name in ('offset', *ARROW_HEIGHTS))
    row(ARROW, params, [*defaults, 0, 5, 100])

    base = tuple(rows)
    if v>=5:
        rows.extend(overlay_api(a) for a in base if a['name']!='barcolor')
    if v>=3:
        for api in base:
            window = window_api(api, v)
            rows.append(overlay_api(window) if v>=5 and api['name']!='barcolor' else window)
    return with_display(rows,v)

def contracts(v):
    from .model import CallContract,ValueType
    return {a['id']:CallContract('symbol.'+a['id'],'overload.'+a['id'],'assertion.'+a['id'],digest(a),a['operation'],
        tuple('parameter.'+a['id']+'.'+p['name'] for p in a['parameters']),tuple(tuple(p['types']) for p in a['parameters']),
        tuple(p['qualifier'] for p in a['parameters']),None,tuple(a['effects']),ValueType('void'),'fixed') for a in apis(v)}

def constants(v):
    result={f'shape.{s}':(s,'string') for s in SHAPES}
    result.update({f'location.{s}':(s,'string') for s in LOCATIONS})
    if v>=3:result.update({f'size.{s}':(s,'string') for s in SIZES})
    return result

def uses_visual_signals(value, operations=GLOBAL_OUTPUTS):
    if getattr(value, 'operation', '') in operations:
        return True
    if is_dataclass(value):
        return any(uses_visual_signals(getattr(value, f.name), operations) for f in fields(value))
    return type(value) is tuple and any(uses_visual_signals(child, operations) for child in value)


def uses_markers(value):
    return uses_visual_signals(value, OUTPUTS)

def admit(root,snapshot,v):
    p=read_json(root/'spec/visual-signals-profile.json')
    if (p.get('schema_version')!='openpine.visual-signals-spec.1' or p['source_lock_sha256']!=digest(snapshot.lock)
        or p['versions'][str(v)]!={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(snapshot.references[v])}):
        raise ValueError('Visual signal profile differs from pinned evidence.')

def validate_call(node,v):
    a=next((a for a in apis(v) if 'overload.'+a['id']==node.overload_id),None)
    if a is None or node.operation!=a['operation']:raise ValueError('Unknown signal ABI.')
    start=len(a['parameters'])-len(a['defaults'])
    for i,arg in enumerate(node.arguments):
        if arg.origin != 'default':
            continue
        if i >= start and matches_default(arg.expression, a['defaults'][i-start]):
            continue
        raise ValueError('Signal default differs from its closed signature.')

def graph(program):
    from .model import CallIR
    allowed={id(n) for n in program.statements if isinstance(n,CallIR) and n.operation in GLOBAL_OUTPUTS}
    def walk(value):
        if getattr(value,'operation','') in GLOBAL_OUTPUTS and id(value) not in allowed:raise ValueError('Signals require direct global calls.')
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name))
        elif type(value) is tuple:
            for item in value:walk(item)
    walk(program)
    return {n.call_site_id:n.operation.split('.',1)[1] for n in program.statements if id(n) in allowed}

def presentation(value,v):
    if type(value) is not dict or value.get('kind') not in KINDS:
        raise ValueError('Invalid signal fields.')
    kind = value['kind']
    extras = ARROW_HEIGHTS if kind == ARROW else ()
    if value.keys()-{FLAG,WINDOW} != {*STATIC,'color','textcolor',*extras}:
        raise ValueError('Invalid signal fields.')
    validate_window(value,v)
    if kind == ARROW:
        minimum, maximum = (value[key] for key in ARROW_HEIGHTS)
        if (type(minimum) is not int or type(maximum) is not int or not 1<=minimum<=maximum<=1000
                or value['location']!='relative' or value['size']!='auto' or value['text'] or value['textcolor'] is not None):
            raise ValueError('Invalid arrow height or placement.')
    if not valid(value['color']) or not valid(value['textcolor']):raise ValueError('Invalid signal color.')
    if type(value['offset']) is not int or abs(value['offset'])>OFFSET_LIMIT or type(value['linewidth']) is not int or value['linewidth']!=1:raise ValueError('Invalid signal offset/width.')
    if value['location'] not in (('relative',) if kind==ARROW else LOCATIONS) or value['size'] not in SIZES:raise ValueError('Invalid signal location/size.')
    if type(value['text']) is not str or len(value['text'])>4096 or type(value['char']) is not str or len(value['char'])>1:raise ValueError('Signal text/character exceeds the bounded profile.')
    if value['kind']=='plotshape':
        if value['style'] not in SHAPES or value['char']!='':raise ValueError('Invalid shape.')
    elif value['style']!=value['kind']:raise ValueError('Invalid signal style.')
    if value['kind']!='plotchar' and value['char']!='':raise ValueError('Only plotchar has a character.')
    if value['kind'] in ('barcolor','bgcolor') and (value['text'] or value['textcolor'] is not None or value['location']!='absolute' or value['size']!='auto'):raise ValueError('Color layers cannot carry markers.')
    if v<3 and value['size']!='auto':raise ValueError('Marker size is unavailable before v3.')
    placement(value,v,allowed=value['kind']!='barcolor')
    return value

def metadata(value):
    if type(value) is not dict or value.get('schema_version')!=SCHEMA:raise ValueError('Unknown signal descriptor schema.')
    presentation({'color':None,'textcolor':None,**{k:v for k,v in value.items() if k!='schema_version'}},6)
    return {'presentation_schema':SCHEMA,**{k:v for k,v in value.items() if k!='schema_version'}}

def validate_call_presentation(value, node):
    from .model import LiteralIR
    validate_call_view(value, node)
    params = {arg.parameter_id.rsplit('.',1)[-1]: arg.expression for arg in node.arguments}
    for field in ARROW_HEIGHTS:
        expr = params.get(field)
        if expr is None:
            if field in value:
                raise ValueError('Signal presentation differs from its call.')
        elif isinstance(expr, LiteralIR) and value.get(field) != expr.value:
            raise ValueError('Signal presentation differs from its call.')


def validate_event(value,operation,v,program=None,site=None):
    presentation(value,v)
    if operation not in OUTPUTS or value['kind']!=operation.removeprefix('markers.'):raise ValueError('Signal kind differs from its operation.')
    if program is not None and graph(program).get(site)!=value['kind']:raise ValueError('Signal site does not belong to program.')
    return value
