"""Additive nominal reference fields; a graph never embeds Python containers."""
from .objects import is_object,is_reference
from .arrays import is_array
from .maps import is_map,parts
from .matrices import is_matrix,element
from .enums import is_enum
from .wire import read_json,digest

PROFILE='openpine.object-graphs.1'

def uses_object_graphs(program):
    return any(is_reference(getattr(f,'base',None)) for t in getattr(program,'object_types',()) for f in getattr(t,'fields',()))

def nominal_types(base):
    if is_object(base) or is_enum(base):return (base,)
    if is_array(base):return nominal_types(base[6:])
    if is_matrix(base):return nominal_types(element(base))
    if is_map(base):return tuple(n for t in parts(base) for n in nominal_types(t))
    return ()

def collection_varip(base,types):
    """Bounded eligibility: UDT fields are values or value-only collections.

    Direct UDT-to-UDT fields (including recursive ones) are outside varip
    collection eligibility; a varip UDT variable itself still carries its ID.
    """
    def value(t):return t in ('int','float','bool','string','color') or is_enum(t)
    def collection(t):
        return value(t[6:]) if is_array(t) else value(element(t)) if is_matrix(t) else all(value(x) for x in parts(t)) if is_map(t) else False
    t=base[6:] if is_array(base) else element(base) if is_matrix(base) else parts(base)[1] if is_map(base) else None
    if t is None:return True
    if value(t):return True
    layout=types.get(t)
    return layout is not None and all(value(f.base) or collection(f.base) for f in layout.fields)

def policy(v):
    return {'profile_id':PROFILE if v>=5 else None,'available':v>=5,'language_version':v,
        'fields':['scalar','enum','declared_udt','array','map','matrix'],'defaults':'typed_na_for_reference_fields;existing_scalar_defaults',
        'type_recursion':'self_reference_and_previously_declared_nominal_types;no_forward_type_names',
        'copy':'new_outer_identity_same_field_values_and_reference_ids','graph':'shared_ids;cycles;iterative_reachability_gc;constructor_baseline_edges',
        'intrabar':'ordinary_fields_restore_baseline;marked_reference_fields_carry_ids;collections_reached_by_carried_fields_persist;UDT_fields_need_own_marks',
        'varip_collection_eligibility':'value_or_udt_with_value_fields_and_value_only_collection_fields;direct_udt_fields_rejected',
        'checkpoint':'typed-ir.19;runtime-state.12;array-heap.5;two_pass_typed_id_edges',
        'limits':'existing_heap_memory_work_and_decode_limits;bounded_iterative_graph_walks',
        'excluded':['direct_collection_elements','graphics','color','object_requests','object_tuples','recursive_functions'],
        'external_execution_verified':False,'limits_increased':False}

def admit(root,snapshot,v):
    value=read_json(root/'spec/object-graphs-profile.json')
    if value.get('schema_version')!='openpine.object-graphs-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock) or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}:
        raise ValueError('Object graph profile differs from pinned evidence.')
