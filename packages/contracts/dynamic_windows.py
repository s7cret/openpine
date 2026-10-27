"""Versioned changing-length windows; fixed-length contracts remain intact."""
from dataclasses import asdict,replace
from .wire import digest,read_json

PROFILE='openpine.dynamic-windows.1'
SUFFIX='.dynamic-window'
BASE_OPERATIONS=frozenset(('signals.sum','signals.wma','series.highest','series.lowest',
    'series.variance','series.stdev','series.variance.population','series.stdev.population'))
OPERATIONS=frozenset('window.'+op.split('.',1)[1] for op in BASE_OPERATIONS)
HORIZON=100000

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'length':'defined_series_int_1_100000',
        'activation':'only_length_argument_with_series_qualifier','history_horizon':HORIZON,
        'history_budget_cells':1000000,'retention':'all_reached_samples_up_to_horizon_independent_of_selected_length',
        'extrema':'last_length_reached_calls_including_na_available_warmup',
        'statistics':'last_length_defined_samples_full_warmup','source_na':'skip_statistics_requery_current_length',
        'invalid_length':'error_before_state_update','skipped_call':'no_update',
        'state_owner':'written_call_and_requested_context','numeric':'exact_binary_prefix_sums_final_finite_float',
        'requested_bar_index':'private_only_inside_window_length_expressions_including_existing_sma',
        'extrema_cost':'log_horizon_update_and_query','statistics_cost':'constant_update_and_query',
        'count_limit':2**63-1,'historical_qualifier_status':'attested_series_int_v5_v6_reconstructed_v1_v4',
        'external_execution_verified':False}

def contract(base,version):
    limits=list(base.qualifier_limits);limits[-2 if base.operation in ('series.variance','series.stdev') else -1]='series'
    return replace(base,symbol_id=base.symbol_id+SUFFIX,overload_id=base.overload_id+SUFFIX,
        assertion_id=base.assertion_id+SUFFIX,signature_sha256=digest({'base':asdict(base),'policy':policy(version)}),
        operation='window.'+base.operation.split('.',1)[1],
        parameter_ids=tuple(p+SUFFIX for p in base.parameter_ids),qualifier_limits=tuple(limits))

def admit_profile(root,snapshot,version):
    p=read_json(root/'spec/dynamic-windows-profile.json');row=p['versions'][str(version)]
    if (p['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or row['policy']!=policy(version)
        or row['reference_sha256']!=digest(snapshot.references[version])
        or p['evidence_sha256']!=digest(read_json(root/'spec/dynamic-windows-evidence.json'))
        or p['legacy_abi_sha256']!=digest(read_json(root/'spec/dynamic-windows-legacy-abi.json'))):
        raise ValueError('Dynamic windows differ from the pinned evidence and ABI.')

def validate_intrinsic(node):
    from .model import ReferenceIR,LiteralIR
    if node.operation not in OPERATIONS:return
    if node.arguments[1].expression.value_type.qualifier!='series':
        raise ValueError('Changing-window contract requires a series-qualified length.')
    if node.operation in ('window.highest','window.lowest') and len(node.arguments)==2 and node.arguments[0].origin=='default':
        a=node.arguments[0];slot='high' if node.operation=='window.highest' else 'low'
        if not isinstance(a.expression,ReferenceIR) or a.expression.contract.slot!=slot or a.source_index is not None:
            raise ValueError('Dynamic extremum implicit source differs from physical market input.')
    for i,a in enumerate(node.arguments):
        if a.origin=='default' and not (node.operation in ('window.highest','window.lowest') and i==0):
            if node.operation not in ('window.variance','window.stdev') or i!=2 or not isinstance(a.expression,LiteralIR) or a.expression.value is not True:
                raise ValueError('Dynamic window default differs from the reviewed ABI.')
