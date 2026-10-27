"""Separate numeric admission layered on the unchanged scalar target policy."""
from dataclasses import replace
from .wire import digest,read_json
from .numeric_functions import apis,policy as numeric_policy
PROFILE='openpine.numeric-targets.1'
SUFFIX='.numeric.scalar.function.primary.prepared'

def policy(version):
    from .scalar_targets import policy as scalar_policy
    return {**scalar_policy(version),'profile_id':PROFILE,'numeric_policy':numeric_policy(version),
        'numeric_registry':'captured_legacy_ABI_plus_eight_reviewed_functions','body':'same_registered_pure_numeric_kernels'}

def contract(base,version):
    return replace(base,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
        signature_sha256=digest({'base':base.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('simple','simple',*base.qualifier_limits[2:]))

def admit_profile(root,snapshot,version):
    p=read_json(root/'spec/numeric-targets-profile.json');row=p['versions'][str(version)]
    if (p['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or row['policy']!=policy(version)
            or row['reference_sha256']!=digest(snapshot.references[version]) or row['apis']!=apis(version)
            or p['evidence_sha256']!=digest(read_json(root/'spec/numeric-functions-evidence.json'))
            or p['legacy_abi_sha256']!=digest(read_json(root/'spec/numeric-legacy-abi.json'))
            or p['scalar_profile_sha256']!=digest(read_json(root/'spec/scalar-targets-profile.json'))):raise ValueError('Numeric policy differs from pinned evidence/ABI.')

def trace(program,expression):
    from .computed_targets import evaluate
    result={};evaluate(program,expression,allow_primary=True,allow_functions=True,allow_scalars=True,allow_numeric=True,shape_only=True,trace=result)
    return result
