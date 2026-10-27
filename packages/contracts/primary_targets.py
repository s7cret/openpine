"""Symbolic request targets bound to an explicit, immutable primary market."""
from dataclasses import replace
from .contexts import MarketContext
from .wire import digest,read_json

PROFILE='openpine.primary-targets.1'
SUFFIX='.primary.prepared'
SLOTS={'environment.syminfo.tickerid':'symbol','environment.timeframe.period':'timeframe'}

class PrimaryRequired(ValueError):
    def __init__(self,field):
        self.fields=(field,) if type(field) is str else tuple(sorted(set(field)))
        super().__init__('Select the primary market context before resolving '+', '.join(self.fields)+'.')

def input_profile(version):return f'openpine.primary-inputs.v{version}.1'
def relative_input(definition):return definition.profile_id in (input_profile(5),input_profile(6)) and definition.input_type in ('symbol','timeframe')

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'qualifiers':['const','input','simple'],
        'references':list(SLOTS),'resolution':'primary_metadata_before_data_io','input_profile':input_profile(version) if version>=5 else None,
        'empty_symbol_basis':'artifact_text' if version>=5 else 'adopted_historical_reconstruction',
        'empty_timeframe_basis':'artifact_text' if version>=3 else 'adopted_historical_reconstruction',
        'metadata_fields':['symbol','timeframe','timezone','session','time_rule'],'max_nodes':128,'max_depth':16,
        'runtime_targets':'fixed_for_run','external_equivalence':False}

def contract(base,version):
    return replace(base,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
        signature_sha256=digest({'base':base.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('simple','simple',*base.qualifier_limits[2:]))

def admit_profile(root,snapshot,version):
    profile=read_json(root/'spec/primary-targets-profile.json');row=profile['versions'][str(version)]
    if (profile['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or row['policy']!=policy(version)
            or row['reference_sha256']!=digest(snapshot.references[version])
            or profile['evidence_sha256']!=digest(read_json(root/'spec/primary-targets-evidence.json'))):
        raise ValueError('Primary-target policy differs from pinned evidence.')

def uses_primary(program):
    from .context_ir import ir_nodes
    return any(getattr(n,'operation',None) in ('context.request','context.lower_tf') and n.overload_id.endswith(SUFFIX) for n in ir_nodes(program.statements))

def metadata(primary):
    if primary is None:return None
    c=primary if type(primary) is MarketContext else MarketContext.from_payload(primary)
    # Dataset/context IDs and provenance remain pinned in the existing bindings.
    # Only these immutable logical fields influence primary target evaluation.
    return {k:getattr(c,k) for k in ('symbol','timeframe','timezone','session','time_rule')}

def shape(program,expression,*,allow_functions=False,allow_scalars=False,allow_numeric=False):
    from .computed_targets import evaluate
    return evaluate(program,expression,allow_primary=True,shape_only=True,allow_functions=allow_functions,allow_scalars=allow_scalars,allow_numeric=allow_numeric)[1]

def resolve(program,expression,kind,settings=None,*,primary=None,allow_functions=False,allow_scalars=False,allow_numeric=False):
    from .computed_targets import evaluate,validate_text
    value,deps=evaluate(program,expression,settings,allow_primary=True,primary=metadata(primary),allow_functions=allow_functions,allow_scalars=allow_scalars,allow_numeric=allow_numeric)
    raw=value
    if value=='':
        if primary is None:raise PrimaryRequired(kind)
        value=metadata(primary)[kind];deps=tuple(sorted(set(deps)|{'primary:'+kind}))
    if allow_scalars and kind=="timeframe" and value in ("1D","1W","1M"):value=value[1:]
    try:validate_text(kind,value)
    except ValueError as error:raise ValueError(f'Resolved request {kind} {value!r}: {error}') from error
    return value,deps,raw
