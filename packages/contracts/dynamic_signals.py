"""Separate changing-length signal contracts; preserve every fixed ABI."""
from dataclasses import asdict,replace
from .wire import digest,read_json
NAMES=('vwma','mom','roc','rising','falling','highestbars','lowestbars')
BASE_OPERATIONS=frozenset('signals.'+n for n in NAMES)
OPERATIONS=frozenset('swindow.'+n for n in NAMES)
PROFILE='openpine.dynamic-signals.1'
SUFFIX='.dynamic-signals'
HORIZON=100000

def policy(v):
 return {'profile_id':PROFILE,'language_version':v,'length':'defined_series_int_1_100000','activation':'series_length_only',
 'history':'retained_independently_of_selected_length','vwma':'last_length_defined_pairs_full_warmup_zero_volume_na_negative_volume_error',
 'mom_roc':'length_reached_calls_ago_include_na_zero_roc_denominator_na',
 'trend':'strict_current_vs_previous_length_defined_samples_full_warmup',
 'offset':'nonpositive_reached_call_offset_latest_tie_available_warmup_na_holes',
 'missing_bool':'false' if v==6 else 'na','history_horizon':HORIZON,'history_budget_cells':1000000,
 'invalid_length':'error_before_state_update','state_owner':'written_call_and_context','skipped_call':'no_update',
 'count_limit':2**63-1,'cost':'constant_vwma_mom_roc_log_horizon_trend_offsets',
 'evidence':'series_length_attested_v4_v6_qualifier_reconstruction_v1_v3','external_execution_verified':False}

def contract(base,v):
 return replace(base,symbol_id=base.symbol_id+SUFFIX,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
 signature_sha256=digest({'base':asdict(base),'policy':policy(v)}),operation='swindow.'+base.operation.split('.',1)[1],
 parameter_ids=tuple(p+SUFFIX for p in base.parameter_ids),qualifier_limits=(*base.qualifier_limits[:-1],'series'))

def admit_profile(root,snapshot,v):
 p=read_json(root/'spec/dynamic-signals-profile.json')
 if (p['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or p['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}
 or p['evidence_sha256']!=digest(read_json(root/'spec/dynamic-signals-evidence.json'))
 or p['legacy_abi_sha256']!=digest(read_json(root/'spec/dynamic-signals-legacy-abi.json'))):raise ValueError('Dynamic signals differ from pinned evidence or ABI.')

def validate_intrinsic(node):
 from .model import ReferenceIR
 if node.operation not in OPERATIONS:return
 if node.arguments[-1].expression.value_type.qualifier!='series':raise ValueError('Changing signals require a series length.')
 for i,a in enumerate(node.arguments):
  implicit='volume' if node.operation=='swindow.vwma' and i==0 else ('high' if node.operation=='swindow.highestbars' else 'low') if node.operation in ('swindow.highestbars','swindow.lowestbars') and i==0 and a.origin=='default' else None
  if implicit:
   if a.origin!='default' or a.source_index is not None or not isinstance(a.expression,ReferenceIR) or a.expression.contract.slot!=implicit:raise ValueError('Changing signal implicit field differs from physical context.')
  elif a.origin=='default':raise ValueError('Unexpected changing signal default.')
