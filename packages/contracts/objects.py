"""Nominal, flat UDT contracts; reference storage is shared with arrays."""
import re
from dataclasses import fields,is_dataclass
from .wire import read_json,digest

FIELDS=('int','float','bool','string')
PROFILE='openpine.flat-objects.1'
MAX_TYPES=32
MAX_FIELDS=32

def is_object(base):return type(base) is str and re.fullmatch(r'udt:[0-9]{1,6}:[0-9]{1,6}',base) is not None

def is_reference(base):
    from .arrays import is_array
    return is_array(base) or is_object(base)

def uses_objects(value):
    from .model import ObjectProgramIR,ObjectIR,ValueType,ParameterContract
    from .object_arrays import is_object_array
    if isinstance(value,(ObjectProgramIR,ObjectIR)):return True
    if isinstance(value,ValueType) and (is_object(value.base) or is_object_array(value.base)):return True
    if isinstance(value,ParameterContract) and (is_object(value.annotation) or is_object_array(value.annotation)):return True
    if is_dataclass(value):return any(uses_objects(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_objects,value))

def policy(version):
    return {'profile_id':PROFILE if version>=5 else None,'language_version':version,'available':version>=5,'fields':list(FIELDS),
        'type_identity':'global_declaration_span_nominal','max_types':MAX_TYPES,'max_fields':MAX_FIELDS,
        'defaults':'closed_literals_signed_numeric_literals_or_compatible_admitted_builtin_reference_at_construction',
        'missing_bool':'false' if version==6 else 'na','new':'positional_named_sources_once_then_defaults_in_field_order',
        'assignment':'reference','copy':'new_object_same_scalar_field_values','qualifier':'series',
        'storage':'shared_flat_reference_heap_budgets_roots_and_copy_on_write',
        'intrabar':'varip_fields_only_reference_varip_does_not_promote_fields_new_reference_retains_constructor_scalar_baseline',
        'history':'object_reference_or_numeric_bool_scalar_alias_direct_field_v6_invalid_v5_profile_uncovered',
        'requests':'object_dependent_contexts_and_targets_profile_uncovered',
        'excluded':['reference_fields','object_arrays','nested_types','recursive_types','object_tuples','object_loop_results','object_equality','void_results','libraries'],
        'external_execution_verified':False}

def admit(root,snapshot,version):
    v=read_json(root/'spec/objects-profile.json')
    if (v.get('schema_version')!='openpine.objects-spec.1' or v['source_lock_sha256']!=digest(snapshot.lock)
        or v['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}):
        raise ValueError('Object profile differs from pinned evidence.')
