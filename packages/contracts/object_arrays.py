"""Additive arrays of declared flat nominal records; no recursive edges."""
from dataclasses import fields,is_dataclass
from .objects import is_object
from .wire import read_json,digest

PROFILE='openpine.object-arrays.1'

def is_object_array(base):return type(base) is str and base.startswith('array.') and is_object(base[6:])

def uses_object_arrays(value):
    from .model import ArrayIR,ValueType,ParameterContract
    if isinstance(value,ArrayIR) and is_object(value.element):return True
    if isinstance(value,ValueType) and is_object_array(value.base):return True
    if isinstance(value,ParameterContract) and is_object_array(value.annotation):return True
    if is_dataclass(value):return any(uses_object_arrays(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_object_arrays,value))

def policy(version):
    return {'profile_id':PROFILE if version>=5 else None,'available':version>=5,'language_version':version,
        'elements':'declared_flat_scalar_field_udt_or_typed_na','invariance':'nominal_element_identity',
        'constructors':['array.new<T>','array.from'],'operations':['size','get','set','push','pop','shift','unshift','clear','copy','is_na'],
        'copy':'new_array_same_object_references','roots':'persistent_history_and_array_element_edges',
        'graph':'arrays_to_scalar_records_only_acyclic','budgets':'existing_shared_heap_and_job_ledger',
        'checkpoint_edges':'compact_nominal_object_ids; legacy_tagged_items_reader',
        'intrabar':'varip_array_structure_and_declared_varip_record_fields_only',
        'new_unconfirmed_objects':'constructor_baseline_for_ordinary_fields',
        'history':'past_array_id_not_content_snapshot','ir':'openpine.typed-ir.15','checkpoint':'openpine.runtime-state.7',
        'excluded':['reference_fields','nested_arrays','recursive_udt','numeric_object_aggregation','object_dependent_requests'],
        'external_execution_verified':False}

def admit(root,snapshot,version):
    v=read_json(root/'spec/object-arrays-profile.json')
    if v.get('schema_version')!='openpine.object-arrays-spec.1' or v['source_lock_sha256']!=digest(snapshot.lock) or v['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}:
        raise ValueError('Object-array policy differs from pinned evidence.')
