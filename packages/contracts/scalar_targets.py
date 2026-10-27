"""Explicit scalar-kernel whitelist extending frozen pure-function targets."""
from dataclasses import replace
from .wire import digest,read_json
from .scalar_functions import apis,policy as scalar_policy
PROFILE='openpine.scalar-targets.1'
SUFFIX='.scalar.function.primary.prepared'

def policy(version):
    from .function_targets import policy as function_policy
    return {**function_policy(version),'profile_id':PROFILE,'effects':'only_closed_pure_scalar_registry',
        'scalar_policy':scalar_policy(version),'kernels':sorted({a['operation'] for a in apis(version)})}

def contract(base,version):
    return replace(base,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
        signature_sha256=digest({'base':base.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('simple','simple',*base.qualifier_limits[2:]))

def admit_profile(root,snapshot,version):
    value=read_json(root/'spec/scalar-targets-profile.json');row=value['versions'][str(version)]
    if (value['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or row['policy']!=policy(version)
            or row['reference_sha256']!=digest(snapshot.references[version]) or row['apis']!=apis(version)
            or value['evidence_sha256']!=digest(read_json(root/'spec/scalar-targets-evidence.json'))
            or value['function_profile_sha256']!=digest(read_json(root/'spec/function-targets-profile.json'))):
        raise ValueError('Scalar target/API policy differs from pinned evidence.')

def trace(program,expression):
    from .computed_targets import evaluate
    result={};evaluate(program,expression,allow_primary=True,allow_functions=True,allow_scalars=True,shape_only=True,trace=result)
    return result
