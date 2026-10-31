"""Pinned shared windows over the existing finite array element families."""
from .wire import digest,read_json

PROFILE='openpine.array-slice.1'

def policy(v):
    return {'profile_id':PROFILE if v>=4 else None,'language_version':v,'available':v>=4,
        'operations':['slice'] if v>=4 else [],'methods':v>=5,
        'elements':['int','float','bool','string']+(['enum','flat_udt'] if v>=5 else []),
        'parameters':['id','index_from','index_to'],'defaults':[],
        'creation':'defined_int;0<=from<to<=size;negative_bounds_rejected_in_all_versions',
        'identity':'new_array_id;shared_root_buffer;fixed_absolute_half_open_window',
        'nested':'normalize_to_root;only_mutated_window_changes_extent;other_windows_keep_absolute_bounds',
        'mutation':'splice_root_range;insert_remove_resize_receiver_window;copy_is_independent_container',
        'parent_resize':'access_fails_ARRAY_SLICE_BOUNDS_if_window_exceeds_root;growth_can_make_window_valid_again',
        'empty':'creation_forbidden;clear_or_removal_can_leave_empty_window;push_can_refill',
        'storage':'view_metadata_and_parent_edge;physical_slots_counted_once;acyclic_view_to_root_to_flat_record',
        'preview':'existing_root_buffer_COW;immutable_window_metadata',
        'checkpoint':'runtime-state.9;array-heap.2;legacy_program_formats_unchanged',
        'limits_increased':False,'external_execution_verified':False}

def uses_slice(program):
    from dataclasses import fields,is_dataclass
    from .model import ArrayIR
    if isinstance(program,ArrayIR) and program.operation=='slice':return True
    if is_dataclass(program):return any(uses_slice(getattr(program,f.name)) for f in fields(program))
    return type(program) is tuple and any(map(uses_slice,program))

def admit(root,snapshot,v):
    value=read_json(root/'spec/array-slice-profile.json')
    if (value.get('schema_version')!='openpine.array-slice-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock)
        or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}):
        raise ValueError('Array slice profile differs from pinned evidence.')
