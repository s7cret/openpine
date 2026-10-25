"""Versioned relative exits and per-target trailing protection contracts."""
from dataclasses import dataclass
from decimal import Decimal
from .trading import BrokerPolicy,decimal_text

PROFILE='openpine.trailing-broker.1'
OPERATION='simulation.exit_extended'
NOTE='Relative exits and trailing use explicit per-lot historical rules: v1/v2 signatures and early parameter precedence are reconstructed; v5 absolute priority and v6 earliest directional level follow the pinned migration decision. OHLC activation/ratchet timing is a bounded model, not external execution equivalence.'
PARAMETERS=('profit','loss','trail_price','trail_points','trail_offset')
MIGRATION_URL='https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/'
SOURCE_FACTS={'v5':'Absolute parameters override their related relative parameters.',
    'v6':'Related absolute and relative levels are both evaluated; the expected first trigger is selected.',
    'units':'profit, loss and trail_points measure ticks from entry; limit, stop and trail_price are prices.'}

def source_review():
    from .wire import digest
    return {'url':MIGRATION_URL,'accessed':'2026-10-25','kind':'reviewed_parameter_pair_facts','facts':dict(SOURCE_FACTS),'selected_facts_sha256':digest(SOURCE_FACTS)}

@dataclass(frozen=True)
class TrailingPolicy(BrokerPolicy):
    profile_id:str=PROFILE
    pyramiding:int=0
    close_entries_rule:str='FIFO'
    language_version:int=6

    def __post_init__(self):
        super().__post_init__()
        if self.profile_id!=PROFILE or type(self.pyramiding) is not int or not 0<=self.pyramiding<=16 or self.close_entries_rule not in ('FIFO','ANY') or type(self.language_version) is not int or not 1<=self.language_version<=6:
            raise ValueError('Invalid versioned trailing policy.')
        if self.settings.tick_size is None:raise ValueError('Relative/trailing exits require pinned tick_size metadata.')

@dataclass(frozen=True)
class TrailingExitIntent:
    call_site_id:str
    kind:str
    entry_id:str
    exit_id:str
    stop:str|None=None
    limit:str|None=None
    quantity:str|None=None
    percent:str|None=None
    oca_name:str=''
    profit:str|None=None
    loss:str|None=None
    trail_price:str|None=None
    trail_points:str|None=None
    trail_offset:str|None=None

def validate_distances(intent):
    for name in ('profit','loss','trail_points','trail_offset'):
        raw=getattr(intent,name)
        if raw is not None and (type(raw) is not str or decimal_text(raw)!=raw or Decimal(raw)<0 or Decimal(raw)!=Decimal(raw).to_integral_value()):
            raise ValueError('Relative/trailing distances require nonnegative whole ticks.')
    activation=intent.trail_price is not None or intent.trail_points is not None
    if activation!=(intent.trail_offset is not None):raise ValueError('Trailing requires an activation level and trail_offset together.')
    if not activation and all(getattr(intent,n) is None for n in ('stop','limit','profit','loss')):raise ValueError('Exit requires a fixed, relative or complete trailing leg.')

def trailing_apis(version):
    p=[('id','string','const',None),('from_entry','string','const',None),('stop','float','series',{'kind':'na'}),('limit','float','series',{'kind':'na'}),
       ('qty','float','series',{'kind':'na'}),('qty_percent','float','series',100.),('oca_name','string','const','')]
    p += [(n,'float','series',{'kind':'na'}) for n in PARAMETERS]
    return [('strategy.exit',OPERATION,p+([('when','bool','series',True)] if version<6 else []))]

def trailing_policy(version):
    return {'language_version':version,'profile_id':f'openpine.trailing.v{version}.1',
        'basis':'reconstructed_names_only' if version==1 else 'incomplete_examples_with_reconstructed_trailing' if version==2 else 'artifact_signatures_with_explicit_execution_model',
        'pair_priority':'directional_first_level_absolute_on_tie' if version==6 else 'absolute_over_relative',
        'pair_priority_basis':'official_v5_v6_migration' if version>=5 else 'explicit_early_version_reconstruction',
        'when':version<6,'distance':'nonnegative_whole_ticks_from_exact_target_entry_fill',
        'activation':'trail_price_or_entry_plus_signed_trail_points_requires_trail_offset',
        'prices':'positive_tick_aligned_absolute_and_resolved_prices',
        'open':'eligible_known_open_can_activate_or_ratchet_before_open_and_intrabar_crossings',
        'intrabar':'previous_effective_stop_first_then_extreme_update_new_stop_eligible_next_bar',
        'intrabar_activation':'extreme_can_activate_but_new_stop_waits_next_bar',
        'ambiguous':'stop_before_take_no_interpolated_tick_path',
        'fixed_and_trailing_stop':'most_protective_active_stop_fixed_on_tie',
        'lots':'one_state_per_exact_reserved_target_no_average_entry_substitution',
        'fills':'batch_same_order_role_level_and_basis_once_other_target_levels_execute_separately',
        'ordering':'open_crossing_before_intrabar_stop_before_take_then_order_sequence_and_oldest_target_sequence',
        'quantity_changes':'retain_surviving_target_activation_and_extreme_new_targets_start_fresh',
        'replacement':'same_price_configuration_preserves_surviving_target_state_changed_configuration_resets',
        'oca':'existing_group_membership_actual_fill_quantity_applied_before_next_batch',
        'bounds':{'protections':16,'targets_per_protection':16,'states':256,'commands_per_bar':16},
        'replay':'confirmed_history_only','external_execution_verified':False}
