"""Additive array operations; the original immutable array release stays intact."""
from .model import ValueType,LiteralIR,NAIR
from .wire import digest,read_json

PROFILE='openpine.array-operations.1'
OPERATIONS=('insert','remove','reverse','fill','includes','indexof','lastindexof','concat')
SEARCH=('includes','indexof','lastindexof')

def parameters(op):
    return {'insert':('id','index','value'),'remove':('id','index'),'reverse':('id',),
        'fill':('id','value','index_from','index_to'),'concat':('id1','id2'),
        **{name:('id','value') for name in SEARCH}}[op]

def result_type(op,element):
    return ValueType('array.'+element,'series') if op=='concat' else ValueType(element,'series') if op=='remove' else ValueType('bool','series') if op=='includes' else ValueType('int','series') if op in SEARCH else ValueType('void')

def argument_types(op,element):
    return tuple('array.'+element if n in ('id','id1','id2') else 'int' if n in ('index','index_from','index_to') else element for n in parameters(op))

def valid_default(op,index,value):
    return op=='fill' and (index==2 and type(value) is LiteralIR and type(value.value) is int and value.value==0 and value.value_type==ValueType('int')
        or index==3 and type(value) is NAIR and value.value_type==ValueType('int'))

def policy(v):
    return {'profile_id':PROFILE if v>=4 else None,'language_version':v,'available':v>=4,
        'operations':list(OPERATIONS) if v>=4 else [],'methods':v>=5,
        'elements':['int','float','bool','string']+(['enum','flat_udt'] if v>=5 else []),
        'indices':'insert_positive_0_through_size;remove_0_through_size_minus_one',
        'negative_indices':['insert','remove'] if v==6 else [],
        'fill':'nonnegative_half_open_range;0<=from<=to<=size;equal_range_noop',
        'fill_defaults':{'index_from':0,'index_to':'na_means_current_size_after_explicit_arguments'},
        'search':'first_or_last_matching_slot;-1_if_missing;includes_bool',
        'equality':'defined_scalar_equality;enum_nominal;udt_identity;missing_matches_missing',
        'concat':'same_element_type;mutate_and_return_first_id;shallow;self_concat_original_contents_once',
        'mutation':'preflight_work_storage_and_memory_before_atomic_buffer_replacement',
        'ownership':'existing_shared_heap_and_array_to_record_edges;no_new_reference_kind',
        'schemas':'existing_ArrayIR_and_heap_checkpoints;profile_and_implementation_identity_change',
        'limits_increased':False,'excluded':['slice','sort','nested_collections','new_request_expression_types'],
        'external_execution_verified':False}

def admit(root,snapshot,v):
    value=read_json(root/'spec/array-operations-profile.json')
    if (value.get('schema_version')!='openpine.array-operations-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock)
        or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}):
        raise ValueError('Array operations profile differs from pinned evidence.')
