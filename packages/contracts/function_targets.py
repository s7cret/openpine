"""Pure, finite user functions in frozen request targets; a separate admission."""
from dataclasses import replace
from .wire import digest,read_json

PROFILE='openpine.function-targets.1'
SUFFIX='.function.primary.prepared'

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'qualifiers':['const','input','simple'],
        'functions':'existing_specialized_UserCallIR','locals':'immutable_scalar_declarations',
        'captures':'actual_reads_of_globals_visible_at_definition','arguments':'once_in_source_order',
        'validation':'all_arguments_statements_and_branches','max_nodes':128,'max_depth':16,'max_call_depth':12,
        'effects':'no_state_history_kernels_collections_loops_or_requests','resolution':'before_data_io',
        'empty_targets':'inherit_primary','external_equivalence':False}

def contract(base,version):
    return replace(base,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
        signature_sha256=digest({'base':base.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('simple','simple',*base.qualifier_limits[2:]))

def admit_profile(root,snapshot,version):
    value=read_json(root/'spec/function-targets-profile.json');row=value['versions'][str(version)]
    lock=read_json(root/'spec/lock.json')
    if (value['source_lock_sha256']!=digest(lock) or row['policy']!=policy(version)
            or row['reference_sha256']!=digest(snapshot.references[version])
            or value['functions_sha256']!=digest(read_json(root/lock['function_release']['path']/'functions.json'))
            or value['primary_profile_sha256']!=digest(read_json(root/'spec/primary-targets-profile.json'))):
        raise ValueError('Function-target policy differs from pinned evidence.')

def trace(program,expression):
    from .computed_targets import evaluate
    result={};evaluate(program,expression,allow_primary=True,allow_functions=True,shape_only=True,trace=result)
    return result
