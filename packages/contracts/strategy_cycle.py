"""Closed calculation phases; a market cursor is independent of Pine passes."""
PROFILE='openpine.strategy-cycle.1'
OPERATION='simulation.strategy_cycle'
FLAGS=('calc_on_every_tick','calc_on_order_fills','process_orders_on_close')
MAX_PASSES=32

def policy(version):
    if type(version) is not int or version not in range(1,7):raise ValueError('Invalid cycle language version.')
    return {'profile_id':PROFILE,'language_version':version,'source_flags':dict(zip(FLAGS,(version>=2,version>=2,version>=4))),
        'explicit_early_versions':'OpenPine_reconstruction_only',
        'phases':['price_and_existing_fill_batch','after_fill','regular_calculation','close_processing','after_close_fill','commit'],
        'fill_batch':'one_recalculation_after_each_atomic_broker_batch_not_each_lot_fragment',
        'normal_pass':'once_per_close_or_received_update_if_enabled',
        'close_processing':'one_extra_known_close_batch_after_regular_calculation_then_recalculate_if_filled',
        'new_commands':'next_price_observation_except_single_close_processing_batch',
        'after_close_fill_commands':'wait_next_price_observation_no_repeated_same_price_matching',
        'rollback':'ordinary_state_from_last_confirmed_bar_varip_between_all_primary_passes',
        'requests':'advance_only_on_own_market_events_never_on_primary_fill_recalculation',
        'history':'commit_last_primary_pass_once_per_bar',
        'historical':'explicit_existing_OHLC_batch_then_known_close_not_reconstructed_ticks',
        'limits':{'passes_per_observation':MAX_PASSES,'commands_per_pass':16},'external_equivalence':False}

def source_flags(program):
    h=program.statements[0]
    if h.operation not in (OPERATION,'simulation.strategy_magnifier'):return {}
    return {name:h.arguments[10+i].expression.value for i,name in enumerate(FLAGS) if h.arguments[10+i].origin=='source'}

def settings(program,requested=None):
    from .intrabar import OPERATION as INTRABAR
    from .trading import STRATEGY_DECLARATIONS
    h=program.statements[0]
    if h.operation not in STRATEGY_DECLARATIONS:raise ValueError('Strategy calculation phases require a strategy.')
    if requested is not None and (type(requested) is not dict or not requested or not requested.keys()<=set(FLAGS)
            or any(type(v) is not bool for v in requested.values())):raise ValueError('Cycle settings require known strict boolean flags.')
    source=source_flags(program)
    if h.operation==INTRABAR:source[FLAGS[0]]=h.arguments[-1].expression.value
    if any(k in source and source[k]!=v for k,v in (requested or {}).items()):raise ValueError('Cycle settings conflict with explicit strategy source flags.')
    return {name:(requested or {}).get(name,source.get(name,False)) for name in FLAGS}

def selected(program,profile=None,requested=None):
    return profile==PROFILE or requested is not None or program.statements[0].operation in (OPERATION,'simulation.strategy_magnifier')

def configuration(program,profile=None,requested=None):
    from .intrabar import resolve,PROFILE as OBSERVED
    resolved=resolve(program,profile)
    if not selected(program,profile,requested):return resolved,None
    if requested is not None:settings(program,requested)
    if profile==OBSERVED:
        if requested is not None and requested.get(FLAGS[0]) is False:raise ValueError('Observed profile conflicts with disabled update calculations.')
        requested={**(requested or {}),FLAGS[0]:True}
    return PROFILE,settings(program,requested)
