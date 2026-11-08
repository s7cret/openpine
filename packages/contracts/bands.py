"""Closed color inputs and declarative visual references; no mutable drawing heap."""
import re
from dataclasses import fields,is_dataclass
from .wire import digest,read_json
from .colors import (PLOT_OPTIONS, apis as color_apis, valid as valid_color,
                     validate_presentation as plot_presentation, validate_plot_call_options, stored_plot_version)
from .visual_panes import (FLAG, WINDOW, OFFSET, VIEW_FIELDS, placement,
                           window_api, validate_window, validate_call_view, matches_default)

PROFILE='openpine.visual-bands.1'
SCHEMA='openpine.visual-layer.1'
HANDLES=('plot_id','hline_id')
STYLE='hline_style'
STYLES=('solid','dotted','dashed')

def policy(v):
    return {'profile_id':PROFILE,'language_version':v,'color_input':'unavailable' if v<4 else 'legacy_input' if v==4 else 'generic_or_input.color',
        'legacy_type':'input.color' if v==4 else None,
        'legacy_reconstruction':v<3,'nz_default':'#00000000','input_default':'closed_const_color_expression',
        'handles':'direct_global_bar_declarations_and_aliases;stable_written_callsite;no_reassignment_history_containers_or_UDF',
        'hline':'input_or_const_price_width_style;const_color_v3_otherwise_input_or_const;finite_price;missing_price_hidden',
        'fill':'same_kind_owned_previously_declared_references;series_color;source_order_within_fill_layer',
        'fillgaps_argument':v>=4,'fillgaps_qualifier':'const','fillgaps_default':False,'positional_fill':'first_three_only',
        'fill_plot_window':v>=3,
        'styles':list(STYLES),'style_values':'const/input;global_bar_bindings;no_persistent_history_UDF_or_containers',
        'limits':'existing_output_input_memory_and_iteration_budgets',
        'external_execution_verified':False}

def apis(v):
    def row(k,name,ps,result,defaults=(),effects=(),rule='fixed'):
        return dict(id=f'openpine.visual-bands.v{v}.{k}',name=name,operation='bands.'+k,
            parameters=[dict(name=n,types=list(t),qualifier=q) for n,t,q in ps],result=result,
            defaults=list(defaults),result_rule=rule,effects=list(effects),named=v>=3)
    plots=[a for a in color_apis(v) if a['name']=='plot']
    plot=dict(plots[0])
    plot.update(id=f'openpine.visual-bands.v{v}.plot',operation='bands.plot',result='plot_id')
    rows=[plot,row('nz','nz',[(('source' if v>=5 else 'x'),('color',),'series'),(('replacement' if v>=5 else 'y'),('color',),'series')],'color',('#00000000',),rule='derived'),
        row('hline','hline',[('price',('int','float'),'input'),('title',('string',),'const'),('color',('color',),'const' if v==3 else 'input'),
            ('linestyle',(STYLE,),'input'),('linewidth',('int',),'input')],'hline_id',('', '#2196F3FF','hline_style.solid',1),('emits_plot',))]
    for kind in ('plot','hline'):
        ps=[(kind+'1',(kind+'_id',),'const'),(kind+'2',(kind+'_id',),'const'),('color',('color',),'series'),('title',('string',),'const')]
        defaults=['']
        if v>=4:ps.append(('fillgaps',('bool',),'const'));defaults.append(False)
        if v<6:ps.append(('transp',('int',),'input'));defaults.append(90)
        rows.append(row('fill_'+kind,'fill',ps,'void',defaults,('emits_plot',)))
        if kind=='plot' and v>=3:rows.append(window_api(rows[-1],v))
    for api in plots[1:]:
        suffix=api['id'].split('.plot',1)[1]
        rows.append(dict(api,id=plot['id']+suffix,operation='bands.plot',result='plot_id'))
    return rows

def contracts(v):
    from .model import CallContract,ValueType
    return {a['id']:CallContract('symbol.'+a['id'],'overload.'+a['id'],'assertion.'+a['id'],digest(a),a['operation'],
        tuple('parameter.'+a['id']+'.'+p['name'] for p in a['parameters']),tuple(tuple(p['types']) for p in a['parameters']),
        tuple(p['qualifier'] for p in a['parameters']),None,tuple(a['effects']),ValueType(a['result']),a['result_rule']) for a in apis(v)}

def constants(v):
    return {('hline.style_'+s if v>=4 else s):('hline_style.'+s,STYLE) for s in STYLES}

def handle(value,base):
    return type(value) is str and base in HANDLES and re.fullmatch(base.removesuffix('_id')+r'\|call:[0-9]{1,6}:[0-9]{1,6}',value) is not None

def style(value):return type(value) is str and value in tuple('hline_style.'+s for s in STYLES)

def uses_bands(value):
    from .model import ValueType,InputDefinition
    if isinstance(value,ValueType):return value.base in (*HANDLES,STYLE)
    if isinstance(value,InputDefinition):return value.input_type=='color'
    if getattr(value,'operation','').startswith('bands.'):return True
    if is_dataclass(value):return any(uses_bands(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_bands,value))

def admit(root,snapshot,v):
    p=read_json(root/'spec/visual-bands-profile.json')
    if (p.get('schema_version')!='openpine.visual-bands-spec.1' or p['source_lock_sha256']!=digest(snapshot.lock)
        or p['versions'][str(v)]!={'policy':policy(v),'apis':apis(v),'reference_sha256':digest(snapshot.references[v])}):
        raise ValueError('Visual bands profile differs from pinned evidence.')

def validate_call(node,v):
    a=next((a for a in apis(v) if 'overload.'+a['id']==node.overload_id),None)
    if a is None:raise ValueError('Unknown visual layer ABI.')
    start=len(a['parameters'])-len(a['defaults'])
    for i,arg in enumerate(node.arguments):
        if arg.origin=='default' and (i<start or not matches_default(arg.expression,a['defaults'][i-start])):
            raise ValueError('Visual default differs from its closed signature.')

def presentation(value,v):
    if type(value) is not dict or value.keys()-{FLAG,*VIEW_FIELDS,*PLOT_OPTIONS}!={'color','linewidth','style','kind','references','fillgaps'}:
        raise ValueError('Invalid visual layer fields.')
    if not valid_color(value['color']) or value['kind'] not in ('plot','hline','fill') or type(value['fillgaps']) is not bool:
        raise ValueError('Invalid visual layer kind/color/gaps.')
    if value['kind']!='fill' and value['fillgaps'] or value['kind']=='fill' and value['linewidth']!=1:
        raise ValueError('Only fills have gap policy; fill width is fixed.')
    refs=value['references']
    if type(refs) not in (tuple,list) or (len(refs)!=2 if value['kind']=='fill' else len(refs)!=0):raise ValueError('Invalid visual endpoints.')
    if any(type(r) is not str or not re.fullmatch(r'call:[0-9]{1,6}:[0-9]{1,6}',r) for r in refs):raise ValueError('Invalid output reference.')
    placement(value,v,allowed=value['kind']=='plot')
    if value['kind']=='plot':
        plot_presentation({k:value[k] for k in ('color','linewidth','style',*VIEW_FIELDS,*PLOT_OPTIONS) if k in value},v)
    else:
        if (any(k in value for k in (OFFSET,*PLOT_OPTIONS)) or type(value['linewidth']) is not int or not 1<=value['linewidth']<=64
                or value['style'] not in (STYLES if value['kind']=='hline' else ('fill',))):
            raise ValueError('Invalid hline/fill style, width or offset.')
        validate_window(value,v,allowed=value['kind']=='fill')
    return value

def metadata(value):
    if type(value) is not dict or value.get('schema_version')!=SCHEMA or 'color' in value:
        raise ValueError('Unknown visual descriptor schema.')
    presentation({'color':None,**{k:v for k,v in value.items() if k!='schema_version'}},stored_plot_version(value))
    return {'presentation_schema':SCHEMA,**{k:v for k,v in value.items() if k!='schema_version'}}

from .markers import GLOBAL_OUTPUTS, OUTPUTS as SIGNAL_OUTPUTS
from .visual_ohlc import OPERATIONS as OHLC_OPERATIONS
OUTPUTS=(*GLOBAL_OUTPUTS,'visual.plot','colors.plot','bands.plot','bands.hline','bands.fill_plot','bands.fill_hline')

def output_type(call):
    from .model import ValueType
    return ValueType('float','series') if call.operation in GLOBAL_OUTPUTS or call.operation.startswith('bands.fill_') else call.arguments[0].expression.value_type

def graph(program):
    """Resolve only prior global declarations. Runtime strings never choose outputs."""
    from .model import CallIR,StoreIR,LoadIR,ValueType,UserCallIR
    aliases={};result={};allowed=set()
    for binding in program.bindings:
        if binding.value_type.base==STYLE and (binding.mode!='bar' or binding.namespace or binding.scope_id!='scope:root'):
            raise ValueError('Hline styles require immutable global bar bindings.')
    for statement in program.statements:
        node=statement.expression if isinstance(statement,StoreIR) else statement
        if isinstance(statement,StoreIR) and statement.binding.value_type.base in HANDLES:
            b=statement.binding
            if not statement.declaration or b.mode!='bar' or b.namespace or b.scope_id!='scope:root' or b.value_type.qualifier!='const':
                raise ValueError('Visual references require immutable global declarations.')
            if isinstance(node,LoadIR) and node.binding_id in aliases:
                aliases[b.binding_id]=aliases[node.binding_id];allowed.add(id(node))
            elif isinstance(node,CallIR) and node.operation in ('bands.plot','bands.hline'):
                aliases[b.binding_id]=(node.value_type.base,node.call_site_id);allowed.add(id(node))
            else:raise ValueError('Visual reference requires a declared plot/hline or prior alias.')
        if isinstance(node,CallIR) and node.operation.startswith('bands.') and node.operation!='bands.nz':
            kind='plot' if node.operation=='bands.plot' else 'hline' if node.operation=='bands.hline' else 'fill'
            refs=()
            if kind=='fill':
                expected='plot_id' if node.operation=='bands.fill_plot' else 'hline_id'
                ends=[]
                for argument in node.arguments[:2]:
                    e=argument.expression
                    if not isinstance(e,LoadIR) or e.binding_id not in aliases or aliases[e.binding_id][0]!=expected:
                        raise ValueError('Fill endpoints must be prior global references of the same kind.')
                    ends.append(aliases[e.binding_id][1]);allowed.add(id(e))
                refs=tuple(ends)
            allowed.add(id(node));result[node.call_site_id]={'kind':kind,'references':refs}
    def walk(value):
        if isinstance(value,ValueType) and value.base==STYLE and value.qualifier not in ('const','input'):
            raise ValueError('Hline styles are constant/input metadata, not series state.')
        if getattr(value,'operation','') in ('bands.plot','bands.hline','bands.fill_plot','bands.fill_hline') and id(value) not in allowed:
            raise ValueError('Visual effects must be direct global declarations/calls.')
        typ=getattr(value,'value_type',None)
        if isinstance(value,UserCallIR) and isinstance(typ,ValueType) and typ.base==STYLE:
            raise ValueError('Hline styles cannot be function results.')
        if type(value).__name__.endswith('IR') and isinstance(typ,ValueType) and typ.base in HANDLES and id(value) not in allowed:
            raise ValueError('Visual references cannot enter expressions, history, functions or collections.')
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name))
        elif type(value) is tuple:
            for child in value:walk(child)
    walk(program)
    return result


def validate_event(value,operation,version,program=None,site=None):
    if operation in SIGNAL_OUTPUTS:
        from .markers import validate_event as validate_signal
        return validate_signal(value,operation,version,program,site)
    if operation in OHLC_OPERATIONS:
        from .visual_ohlc import validate_event as validate_ohlc
        validate_ohlc(value,operation,version)
        return value
    if operation=='colors.plot':
        return plot_presentation(value,version)
    if operation not in OUTPUTS or not operation.startswith('bands.'):raise ValueError('Unknown styled output operation.')
    presentation(value,version)
    if operation=='bands.fill_hline' and WINDOW in value:
        raise ValueError('Horizontal level fills do not have a window.')
    if program is not None:
        expected=graph(program).get(site)
        if expected is None or value['kind']!=expected['kind'] or tuple(value['references'])!=expected['references']:raise ValueError('Visual layer endpoints differ from the program.')
    return value


def validate_call_event(value, node):
    from .visual_panes import validate_call_placement
    validate_call_placement(value, node)
    if node.operation in SIGNAL_OUTPUTS:
        from .markers import validate_call_presentation
        validate_call_presentation(value, node)
    elif node.operation in ('colors.plot','bands.plot'):
        validate_plot_call_options(value,node)
    else:
        validate_call_view(value, node)
