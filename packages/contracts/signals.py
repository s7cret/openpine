"""Versioned bounded event, accumulation and window contracts."""

NOTE='Signal/event and weighted-window functions follow a bounded artifact-defined model; external platform equivalence is unverified.'
NAMES=('barssince','valuewhen','cum','sum','wma','vwma','mom','roc','rising','falling','highestbars','lowestbars')
OPERATIONS=frozenset('signals.'+n for n in NAMES)


def signal_policy(version):
    return {'language_version':version,'length':'simple_fixed_int_1_100000','occurrence':'simple_fixed_int_0_99999',
        'state_owner':'written_call_and_context','skipped_call':'no_update','barssince':'reached_calls_since_last_true_or_na',
        'valuewhen':'retain_true_events_including_na_source_zero_is_latest','valuewhen_types':['int','float','bool'],
        'sum_wma':'last_length_non_na_full_warmup_skip_and_hold','cum':'skip_na_hold_na_before_first',
        'vwma':'last_length_defined_source_volume_pairs_zero_denominator_na_negative_volume_error',
        'mom_roc':'length_reached_calls_ago_include_na_zero_roc_denominator_na',
        'trend':'strict_current_vs_previous_length_non_na_full_warmup',
        'extremum_offset':'nonpositive_reached_call_offset_latest_tie_available_warmup_na_holes',
        'missing_bool':'false' if version==6 else 'na','numeric':'finite_outputs_exact_integer_accumulators',
        'memory_cells':1000000,'external_execution_verified':False}


def signal_apis(version):
    prefix='' if version<5 else 'ta.'
    def p(name,types,qualifier='series'):return {'name':name,'types':types,'qualifier':qualifier}
    source=p('source',['float']);length=p('length',['int'],'simple')
    apis=[]
    def add(name,params,result='float',implicit=(),suffix='',rule='fixed_series'):
        apis.append({'id':f'openpine.signals.v{version}.{name}'+suffix,'name':('math.sum' if name=='sum' and version>=5 else prefix+name),
            'operation':'signals.'+name,'parameters':params,'implicit':list(implicit),'result':result,'result_rule':rule,
            'named':version>=3,'effects':['reads_history','updates_state']})
    add('barssince',[p('condition',['bool'])],'int')
    add('valuewhen',[p('condition',['bool']),p('source',['int','float','bool']),p('occurrence',['int'],'simple')],rule='parameter_series')
    add('cum',[source])
    for name in ('sum','wma','mom','roc','rising','falling'):
        add(name,[source,length],'bool' if name in ('rising','falling') else 'float')
    add('vwma',[source,length],implicit=('volume',))
    for name,slot in (('highestbars','high'),('lowestbars','low')):
        add(name,[source,length],'int',suffix='.source')
        add(name,[length],'int',implicit=(slot,),suffix='.default')
    return apis


def validate_intrinsic(node,version):
    from .model import ReferenceIR
    api=next((a for a in signal_apis(version) if node.overload_id=='overload.'+a['id']),None)
    if api is None:return
    for i,name in enumerate(api['implicit']):
        a=node.arguments[i]
        if a.origin!='default' or a.source_index is not None or not isinstance(a.expression,ReferenceIR) or a.expression.contract.slot!=name:
            raise ValueError('Signal implicit input differs from the physical context field.')
