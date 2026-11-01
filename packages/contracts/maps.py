"""Closed, additive typed maps over existing scalar/enum/flat-record types."""
from dataclasses import fields,is_dataclass
from .model import ValueType
from .wire import read_json,digest

PROFILE='openpine.maps.1'
SCALARS=('int','float','bool','string')
OPERATIONS=('new','put','put_all','get','contains','remove','clear','size','copy','keys','values','is_na')
MAX_PAIRS=50000

def parts(base):
    if type(base) is not str or not base.startswith('map<') or not base.endswith('>'):return None
    values=base[4:-1].split(',')
    return tuple(values) if len(values)==2 else None

def is_map(base):
    from .enums import is_enum
    from .objects import is_object
    pair=parts(base)
    return pair is not None and (pair[0] in SCALARS or is_enum(pair[0])) and (pair[1] in SCALARS or is_enum(pair[1]) or is_object(pair[1]))

def base(key,value):return 'map<'+key+','+value+'>'

def uses_maps(value):
    from .model import MapIR,ParameterContract
    if isinstance(value,ParameterContract) and is_map(value.annotation):return True
    if isinstance(value,MapIR) or isinstance(value,ValueType) and is_map(value.base):return True
    if is_dataclass(value):return any(uses_maps(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_maps,value))

def parameters(op):
    if op=='new':return ()
    if op=='put':return ('id','key','value')
    if op=='put_all':return ('id','id2')
    if op in ('get','contains','remove'):return ('id','key')
    return ('id',)

def argument_types(op,key,value):
    return tuple(base(key,value) if n in ('id','id2') else key if n=='key' else value for n in parameters(op))

def result_type(op,key,value):
    result=base(key,value) if op in ('new','copy') else 'void' if op in ('put_all','clear') else 'int' if op=='size' else 'bool' if op in ('contains','is_na') else 'array.'+(key if op=='keys' else value) if op in ('keys','values') else value
    return ValueType(result,'const' if result=='void' else 'series')

def validate(node,version,nominal):
    from .model import NAIR
    if version<5 or node.operation not in OPERATIONS or not is_map(base(node.key_type,node.element)):raise ValueError('Map operation/type/version is unavailable.')
    from .enums import is_enum
    from .objects import is_object
    if any((is_enum(t) or is_object(t)) and t not in nominal for t in (node.key_type,node.element)):raise ValueError('Foreign nominal map type.')
    names=parameters(node.operation);types=argument_types(node.operation,node.key_type,node.element)
    if len(node.arguments)!=len(names) or tuple(a.parameter_id for a in node.arguments)!=names:raise ValueError('Map signature differs.')
    if any(a.origin!='source' or type(a.source_index) is not int for a in node.arguments):raise ValueError('Maps have no default arguments.')
    indices=[a.source_index for a in node.arguments]
    if sorted(indices)!=list(range(len(names))) or node.evaluation_order!=tuple(sorted(range(len(names)),key=indices.__getitem__)):raise ValueError('Map source evaluation order differs.')
    if any(a.expression.value_type.base!=t for a,t in zip(node.arguments,types,strict=True)):raise ValueError('Map argument type differs.')
    if node.value_type!=result_type(node.operation,node.key_type,node.element):raise ValueError('Map result type differs.')

def policy(v):
    return {'profile_id':PROFILE if v>=5 else None,'available':v>=5,'language_version':v,
        'keys':['int','float','bool','string','enum'],'values':['int','float','bool','string','enum','flat_udt'],
        'operations':list(OPERATIONS) if v>=5 else [],'methods':v>=5,'max_pairs':MAX_PAIRS,
        'missing_key':'na;bool_v6_false','put_result':'previous_value_or_missing','order':'insertion;overwrite_retains_position;remove_reinsert_appends',
        'missing_keys':'typed_na_canonical_key;bool_v6_na_forbidden','float_keys':'finite_numeric_equality;negative_zero_equals_zero',
        'copies':'independent_container;shallow_flat_udt_values','iteration':'two_targets_key_value;key_snapshot_at_entry;current_value_or_missing_if_removed',
        'storage':'shared_reference_heap;two_physical_cells_per_pair;acyclic_map_to_flat_record;lookup_index_accounted',
        'mutation':'stage_entire_map;preflight_work_scratch_storage_and_cancel;atomic_publication',
        'checkpoint':'typed-ir.17;runtime-state.10;array-heap.3;legacy_programs_unchanged',
        'checkpoint_udt_values':'packed_object_ids;keys_and_order_preserved',
        'excluded':['color','graphics','nested_collections','reference_udt_fields','map_requests','map_tuple_results'],
        'external_execution_verified':False,'limits_increased':False}

def admit(root,snapshot,v):
    value=read_json(root/'spec/maps-profile.json')
    if value.get('schema_version')!='openpine.maps-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock) or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}:raise ValueError('Map profile differs from pinned evidence.')
