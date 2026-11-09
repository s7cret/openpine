"""Ephemeral products over the existing nominal types and reference heap."""
from .objects import is_reference
from .arrays import is_array
from .drawing_objects import is_drawing_array
from .enums import is_enum
from .wire import read_json,digest

PROFILE='openpine.product-results.1'
SCALARS=('int','float','bool','string')

def element(base,version):
    if base in (*SCALARS,'color'):return True
    if is_array(base):return version>=4 and not is_drawing_array(base)
    return version>=5 and (is_reference(base) or is_enum(base))

def uses_product_results(program):
    from .context_ir import ir_nodes
    from .model import TupleIR,TupleType,ForIR,WhileIR,ForInIR
    for node in ir_nodes(program.statements):
        if isinstance(node,TupleIR) and any(getattr(getattr(child,'value_type',None),'base',None) not in SCALARS for child in node.elements):return True
        if isinstance(node,(ForIR,WhileIR,ForInIR)) and node.result is not None:
            typ=node.result.value_type
            if any(t.base not in ('int','float','bool') for t in (typ.elements if isinstance(typ,TupleType) else (typ,))):return True
    return False

def policy(v):
    return {'profile_id':PROFILE,'language_version':v,'scalar_controls':True,'for':v>=2,'while_and_for_in':v>=5,'arrays':v>=4,'nominal_and_collections':v>=5,
        'arity':[2,16],'tuple':'ephemeral;new_bindings_or_underscore;no_nesting_parameters_storage_ternary_or_whole_history',
        'evaluation':'once_left_to_right;validate_all_before_unpack_publication',
        'qualifiers':'shared_strongest;references_always_series','branches':'same_nominal_and_invariant_reference_types;int_to_float_promotion',
        'loops':'last_completed_tail;break_continue_skip_tail;empty_typed_na_except_v6_bool_false',
        'ownership':'existing_heap;no_collection_during_bar;history_and_persistent_roots_at_commit',
        'wire':'typed-ir.20_only_for_new_constructs;existing_feature_selected_state_and_heap_codecs',
        'limits_increased':False,'external_execution_verified':False}

def admit(root,snapshot,v):
    value=read_json(root/'spec/product-results-profile.json')
    if value.get('schema_version')!='openpine.product-results-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock) or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}:
        raise ValueError('Product result profile differs from pinned evidence.')
