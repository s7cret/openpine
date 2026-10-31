"""Nominal enum values are closed scalar tokens, never reference-heap objects."""
import re
from .wire import digest,read_json

PROFILE='openpine.enums.1'
MAX_TYPES=32
MAX_MEMBERS=64
RESERVED=frozenset(('int','float','bool','string','array','type','enum','method','if','else','var','varip','true','false','na','for','while','switch','break','continue','to','by','and','or','not'))
NAMESPACES=frozenset(('ta','math','str','input','request','strategy','syminfo','timeframe','barstate','color','line','linefill','label','box','table','matrix','map','polyline','array','chart','runtime'))

def unique_members(members):
    seen=set()
    for m in members:
        labels={m.name,m.title}
        if seen&labels:return False
        seen.update(labels)
    return True

def is_enum(base):
    if type(base) is not str:return False
    m=re.fullmatch(r'enum:[0-9]{1,6}:[0-9]{1,6}:([1-9][0-9]?)',base)
    return m is not None and int(m[1])<=MAX_MEMBERS

def is_enum_array(base):return type(base) is str and base.startswith('array.') and is_enum(base[6:])

def type_id(span,count):return f'enum:{span.line}:{span.column}:{count}'

def token(base,index):
    if not is_enum(base) or type(index) is not int or not 0<=index<int(base.rsplit(':',1)[1]):raise ValueError('Invalid enum member identity.')
    return f'{base}:{index}'

def valid_value(value,base):
    if not is_enum(base):return False
    if value is None:return True
    if type(value) is not str:return False
    head,_,tail=value.rpartition(':')
    return head==base and re.fullmatch(r'0|[1-9][0-9]?',tail) is not None and int(tail)<int(base.rsplit(':',1)[1])

def index(value,base):
    if value is None or not valid_value(value,base):raise ValueError('Invalid enum member value.')
    return int(value.rsplit(':',1)[1])

def member_qualifier(version):return 'simple' if version==5 else 'const'

def operation_type(operation,operand):
    from .model import ValueType
    return ValueType('string' if operation=='title' else 'bool',('series' if operand.qualifier=='series' else 'simple') if operation=='title' else operand.qualifier)

def policy(version):
    return {'profile_id':PROFILE if version>=5 else None,'language_version':version,'available':version>=5,
        'identity':'global_declaration_span_and_member_count_nominal','members':'closed_type_and_member_index_scalar_token',
        'member_qualifier':member_qualifier(version) if version>=5 else None,
        'titles':'literal_string_or_field_name_unique_across_other_member_names_and_titles','max_types':MAX_TYPES,'max_members':MAX_MEMBERS,
        'operations':['assignment','na','equality','inequality','conditional','switch','history','str.tostring'],
        'title_qualifier':'existing_tostring_series_if_series_otherwise_simple',
        'containers':['flat_udt_fields','arrays'],'input':'input.enum_frozen_member_selection',
        'v5_namespace_shadowing':'admitted_if_unambiguous','v6_namespace_shadowing':'forbidden',
        'excluded':['libraries','maps','matrices','enum_dependent_requests','enum_tuples'],
        'external_execution_verified':False}

def admit(root,snapshot,version):
    v=read_json(root/'spec/enums-profile.json')
    if (v.get('schema_version')!='openpine.enums-spec.1' or v['source_lock_sha256']!=digest(snapshot.lock)
        or v['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}):
        raise ValueError('Enum profile differs from pinned evidence.')
