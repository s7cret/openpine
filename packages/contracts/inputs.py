"""Closed input schema and immutable execution settings, independent of adapters."""
from dataclasses import asdict,fields,is_dataclass,dataclass,field
import math
import re

from .model import InputDefinition,InputIR,RunResult,ValueType,Diagnostic,OpenPineError
from .wire import digest

SOURCES=('open','high','low','close','volume')
MAX_INPUTS=64


def input_policy(version):
    return {'language_version':version,'profile_id':f'openpine.inputs.v{version}.1',
        'generic':'input','typed_functions':version>=5,'legacy_type_argument':version<5,
        'legacy_type_constants':'input.namespace' if version==4 else 'bare_or_literal' if version<4 else 'none',
        'types':['int','float','bool','string','source'],'scope':'direct_global_bar_declaration',
        'positional':'defval_title_and_legacy_type','constraints':'named_literal_min_max_step_or_options',
        'options':version>=3,'presentation':version>=4,'active':version==6,
        'source_slots':list(SOURCES),'numeric_qualifier':'input','source_qualifier':'series',
        'step':'positive_ui_increment_not_execution_grid','confirm':'false_only',
        'max_inputs':MAX_INPUTS,'max_options':64,'symbol_timeframe_requests':'literal_only'}


def invalid(message):raise OpenPineError(Diagnostic('INPUT_INVALID','INPUT_SETTINGS',message))


def scalar(kind,value,*,relative=False):
    from .enums import is_enum,valid_value
    if is_enum(kind):
        if value is None or not valid_value(value,kind):invalid('Input requires a member of its declared enum type.')
        return value
    if kind=='float' and type(value) is int:
        try:value=float(value)
        except OverflowError:invalid('Input float exceeds its finite range.')
    expected={'int':int,'float':float,'bool':bool,'string':str,'source':str,'symbol':str,'timeframe':str}.get(kind)
    if (expected is None or type(value) is not expected or type(value) is float and not math.isfinite(value)
            or type(value) is int and not -(2**63)<=value<2**63 or type(value) is str and len(value)>4096):
        invalid(f'Input value must be a finite {kind} within its supported range.')
    if kind=='source' and value not in SOURCES:invalid('Input source must select a supported OHLCV field.')
    if kind in ('symbol','timeframe'):
        from .computed_targets import validate_text
        if relative and value=='':return value
        try:validate_text(kind,value)
        except ValueError as error:invalid(str(error))
    return value


def validate_definition(definition,version):
    d=definition
    from .enums import is_enum,PROFILE,token
    from .model import EnumInputDefinition
    enum=isinstance(d,EnumInputDefinition)
    from .computed_targets import input_profile
    from .primary_targets import input_profile as primary_input_profile,relative_input
    expected_profile=input_profile(version) if getattr(d,'input_type',None) in ('symbol','timeframe') and version>=5 else input_policy(version)['profile_id']
    if getattr(d,'input_type',None) in ('symbol','timeframe') and version>=5 and getattr(d,'profile_id',None)==primary_input_profile(version):expected_profile=primary_input_profile(version)
    if enum:
        expected_profile=PROFILE
        if (version<5 or not is_enum(d.input_type) or type(d.enum_name) is not str or not re.fullmatch(r'[A-Za-z_]\w*',d.enum_name)
            or type(d.member_names) is not tuple or type(d.member_titles) is not tuple
            or len(d.member_names)!=int(d.input_type.rsplit(':',1)[1]) or len(d.member_names)!=len(d.member_titles)
            or len(set(d.member_names))!=len(d.member_names) or any(type(n) is not str or not re.fullmatch(r'[A-Za-z_]\w*',n) for n in d.member_names)
            or any(type(t) is not str or len(t)>4096 for t in d.member_titles)
            or type(d.options) is not tuple or not d.options or any(v not in tuple(token(d.input_type,i) for i in range(len(d.member_names))) for v in d.options)):
            invalid('Invalid closed enum input metadata.')
    elif is_enum(getattr(d,'input_type',None)):invalid('Enum inputs require their closed metadata record.')
    if (type(d) is not (EnumInputDefinition if enum else InputDefinition) or type(d.name) is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,127}',d.name)
            or d.input_id!='input:'+d.name or d.profile_id!=expected_profile
            or any(type(getattr(d,k)) is not str or len(getattr(d,k))>4096 for k in ('title','tooltip','group','inline'))
            or type(d.active) is not bool or not d.active and version!=6 or type(d.options) is not tuple or len(d.options)>64):
        invalid('Invalid input definition identity, presentation or limits.')
    if version<4 and any(getattr(d,k) for k in ('tooltip','group','inline')):invalid('Presentation metadata is unavailable in this version.')
    default=scalar(d.input_type,d.default,relative=relative_input(d))
    if type(default) is not type(d.default):invalid('Input default must have its normalized type.')
    for key in ('minimum','maximum','step'):
        value=getattr(d,key)
        if value is not None:
            if d.input_type not in ('int','float'):invalid('Only numeric inputs support numeric constraints.')
            scalar(d.input_type,value)
    if d.step is not None and d.step<=0:invalid('Input step must be positive.')
    if d.minimum is not None and d.maximum is not None and d.minimum>d.maximum:invalid('Input minimum exceeds maximum.')
    if d.input_type in ('symbol','timeframe') and version<5:invalid('Specialized target inputs require v5/v6.')
    if d.options and (version<3 or d.input_type in ('source','bool','symbol') or any(getattr(d,k) is not None for k in ('minimum','maximum','step'))):
        invalid('Input options cannot be combined with range constraints or this input type/version.')
    seen=set()
    for option in d.options:
        value=scalar(d.input_type,option,relative=relative_input(d))
        if type(value) is not type(option) or (type(value),value) in seen:invalid('Input options must be normalized and unique.')
        seen.add((type(value),value))
    validate_value(d,default)
    return d


def validate_value(definition,value):
    from .primary_targets import relative_input
    d=definition;value=scalar(d.input_type,value,relative=relative_input(d))
    if d.minimum is not None and value<d.minimum or d.maximum is not None and value>d.maximum:invalid(f'Input {d.name} is outside its range.')
    if d.options and value not in d.options:invalid(f'Input {d.name} must select one of its options.')
    return value


def definitions(program):
    found=[]
    def walk(value):
        if isinstance(value,InputIR):found.append(validate_definition(value.definition,program.identity.language_version));return
        if is_dataclass(value):
            for f in fields(value):walk(getattr(value,f.name))
        elif type(value) is tuple:
            for child in value:walk(child)
    walk(program.statements)
    if len(found)>MAX_INPUTS or len({d.input_id for d in found})!=len(found):invalid('Input definitions must have unique identities, maximum 64.')
    return tuple(found)


def input_schema(program):
    rows=[asdict(d) for d in definitions(program)]
    sha=digest(rows)
    for row in rows:
        row['default_text']=str(row['default'])
        row['options_text']=[str(value) for value in row['options']]
        from .enums import is_enum,index
        if is_enum(row['input_type']):
            row['default_text']=row['member_titles'][index(row['default'],row['input_type'])]
            row['options_text']=[row['member_titles'][index(v,row['input_type'])] for v in row['options']]
        from .primary_targets import input_profile as primary_input_profile
        if row['profile_id'] in (primary_input_profile(5),primary_input_profile(6)):row['inherits_primary']=True
    return {'schema_version':'openpine.input-schema.1','sha256':sha,'definitions':rows}


def transport_values(values):
    """Preserve int64 values across JavaScript JSON parsers without changing settings."""
    return {key:{'$int':str(value)} if type(value) is int and abs(value)>2**53-1 else value for key,value in values.items()}


def enum_input_labels(program,*,values=None):
    """Presentation projection; immutable settings retain nominal member tokens."""
    from .enums import is_enum,index
    selected=None if values is None else {}
    if selected is not None:
        for row in values:
            for key,value in row.items():
                if type(value) is str:selected.setdefault(key,set()).add(value)
    return {d.input_id:{v:d.member_titles[index(v,d.input_type)] for v in d.options if selected is None or v in selected.get(d.input_id,())}
        for d in definitions(program) if is_enum(d.input_type) and (selected is None or d.input_id in selected)}


def settings_for(program,overrides=None):
    rows=definitions(program)
    if overrides is None:overrides={}
    if type(overrides) is not dict or len(overrides)>MAX_INPUTS:invalid('Input overrides require an object with at most 64 values.')
    if not rows:
        if overrides:invalid('This program declares no configurable inputs.')
        return None
    known={d.input_id:d for d in rows};aliases={d.name:d.input_id for d in rows};supplied={}
    for key,value in overrides.items():
        identifier=key if key in known else aliases.get(key)
        if identifier is None or identifier in supplied:invalid('Unknown or duplicate input override: '+str(key))
        supplied[identifier]=value
    def selected(d):
        value=supplied.get(d.input_id,d.default)
        if type(value) is dict and d.input_type=='int' and value.keys()=={'$int'}:
            raw=value['$int']
            if type(raw) is not str or not re.fullmatch(r'0|-?[1-9][0-9]{0,18}',raw):invalid('Use a canonical decimal int64 string in $int.')
            value=int(raw)
        return validate_value(d,value)
    values={d.input_id:selected(d) for d in rows}
    payload={'schema_version':'openpine.execution-settings.1','input_schema_sha256':input_schema(program)['sha256'],'values':values}
    return {**payload,'sha256':digest(payload)}


def validate_settings(value):
    if (type(value) is not dict or value.keys()!={'schema_version','input_schema_sha256','values','sha256'}
            or value['schema_version']!='openpine.execution-settings.1' or type(value['values']) is not dict
            or not 1<=len(value['values'])<=MAX_INPUTS or any(type(k) is not str or not k.startswith('input:') for k in value['values'])
            or any(type(value[k]) is not str or not re.fullmatch('[0-9a-f]{64}',value[k]) for k in ('input_schema_sha256','sha256'))
            or digest({k:v for k,v in value.items() if k!='sha256'})!=value['sha256']):invalid('Invalid frozen execution settings.')
    for item in value['values'].values():
        if type(item) not in (str,int,float,bool):invalid('Execution settings contain a non-scalar value.')
        scalar({str:'string',int:'int',float:'float',bool:'bool'}[type(item)],item)
    return value


def admitted_settings(program,settings=None):
    if settings is not None:
        validate_settings(settings)
        expected=settings_for(program,settings['values'])
        if expected!=settings:invalid('Execution settings differ from this program input schema.')
        return expected
    return settings_for(program)


@dataclass(frozen=True,kw_only=True)
class ConfiguredRunResult(RunResult):
    settings: dict
    primary_context: object = None
    contexts: tuple = ()
    requests: tuple = ()
    simulation: object = None
    schema_version: str = field(default='openpine.run-result.5',init=False)


def configured_result(result,settings):
    if settings is None:return result
    values={f.name:getattr(result,f.name) for f in fields(result) if f.init}
    return ConfiguredRunResult(**values,settings=settings)
