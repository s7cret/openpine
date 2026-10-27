"""Explicit historical lower-candle reconstruction, independent of requests."""
import re

PROFILE='openpine.bar-magnifier.1'
OPERATION='simulation.strategy_magnifier'

def policy(version):
    if type(version) is not int or version not in range(1,7):raise ValueError('Invalid Pine version.')
    return {'profile_id':PROFILE,'language_version':version,'source_flag':version>=5,
        'early_versions':'explicit_OpenPine_reconstruction_only','default':False,
        'timeframes':'divisible_UTC_minute_pairs_1_to_1440','identity':'same_symbol_provider_and_instrument',
        'coverage':'complete_slots_and_matching_parent_OHLCV_or_explicit_whole_parent_fallback',
        'conflict':'reject_duplicates_disorder_and_OHLCV_conflicts_even_with_fallback',
        'path':'open_nearer_extreme_other_extreme_close_high_first_on_tie',
        'fills':'shared_sampled_price_broker_at_four_model_points_no_interpolation',
        'calculation':'after_atomic_fill_batch_if_enabled_and_once_at_parent_close',
        'availability':'cumulative_model_points_only_no_future_parent_fields_or_requested_closes',
        'ticks':'calc_on_every_tick_does_not_enable_calculation_on_synthetic_points',
        'history':'one_commit_per_parent','checkpoint':'after_any_model_point_including_first_parent',
        'external_equivalence':False}

def source_setting(program):
    h=program.statements[0]
    return h.arguments[13].expression.value if h.operation==OPERATION else None

def configuration(value):
    from .strategy_cycle import FLAGS
    names={'schema_version','snapshot_id','missing','cycle_settings','checkpoint_every_children'}
    if (type(value) is not dict or value.keys()!=names or value['schema_version']!=PROFILE
            or type(value['snapshot_id']) is not str or not re.fullmatch('[0-9a-f]{64}',value['snapshot_id'])
            or value['missing'] not in ('reject','parent_ohlc')
            or type(value['checkpoint_every_children']) is not int or not 0<=value['checkpoint_every_children']<=1000000
            or value['cycle_settings'] is not None and (type(value['cycle_settings']) is not dict or not value['cycle_settings']
                or not value['cycle_settings'].keys()<=set(FLAGS) or any(type(v) is not bool for v in value['cycle_settings'].values()))):
        raise ValueError('Invalid versioned bar magnifier configuration.')
    return value

def admit(program,value):
    from .strategy_cycle import settings
    from .trading import STRATEGY_DECLARATIONS
    if value is None:
        if source_setting(program):raise ValueError('use_bar_magnifier=true requires a pinned lower-candle snapshot.')
        return None
    configuration(value)
    if program.statements[0].operation not in STRATEGY_DECLARATIONS:raise ValueError('Bar magnifier requires a strategy.')
    if source_setting(program) is False:raise ValueError('Magnifier input conflicts with use_bar_magnifier=false.')
    settings(program,value['cycle_settings'])
    return value

def minute_pair(parent,child):
    if (type(parent) is not str or type(child) is not str or not parent.isdecimal() or not child.isdecimal()
            or str(int(parent))!=parent or str(int(child))!=child or not 1<=int(child)<int(parent)<=1440
            or int(parent)%int(child)):raise ValueError('Magnifier requires a divisible lower minute timeframe.')
    return int(parent)//int(child)
