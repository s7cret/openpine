"""Opt-in sampled-price paper broker; candle ordering remains a separate contract."""

PROFILE='openpine.observed-broker.1'

def policy(version):
    if type(version) is not int or version not in range(1,7):raise ValueError('Invalid observed broker language version.')
    return {'profile_id':PROFILE,'language_version':version,
        'qualification':'local_paper_model_not_exchange_execution_or_TradingView_equivalence',
        'version_basis':'explicit_reconstruction' if version<3 else 'existing_pinned_order_signatures_with_explicit_observed_execution',
        'calculation':'Pine_and_new_commands_on_primary_confirmed_close_only',
        'price':'last_price_in_each_primary_event_including_first_received_cumulative_candle',
        'missing_path':'cumulative_high_low_and_exchange_open_are_informational_never_replayed_as_ticks',
        'fills':'existing_orders_at_known_sample_price_with_pinned_fees_slippage_and_limits',
        'ordering':'risk_then_sequence_ordered_entries_market_closes_then_protection_then_close_commands',
        'stop_limit':'activation_and_fill_may_share_one_sample_only_if_both_conditions_hold_at_that_price',
        'protection':'bound_to_exact_lots_parent_fill_can_activate_protection_at_same_known_sample',
        'trailing':'sampled_extremes_only_no_retroactive_high_low_touch',
        'risk':'shared_chart_UTC_period_rules_observed_sample_checks_and_immediate_known_price_liquidation',
        'clocks':'bar_index_and_trade_time_are_chart_bar_coordinates_event_sequence_revision_and_available_at_are_separate_provenance',
        'commit':'broker_effects_are_committed_per_observation_independently_of_Pine_bar_rollback',
        'recovery':'input_cursor_broker_output_prefix_and_journal_publish_atomically',
        'warmup':'standalone_confirmed_candles_supply_one_close_sample_each_no_reconstructed_path',
        'limitations':['no_calc_on_every_tick','no_calc_on_order_fills','no_exchange_orders','no_volume_partial_fills','no_unknown_tick_interpolation']}

def promote(selected,version,timeframe):
    """Use the existing full lot/economic broker for every admitted order shape."""
    from dataclasses import replace
    from .economics import EconomicsPolicy,CommissionPolicy
    from .risk import RiskPolicy
    from .performance import PerformancePolicy
    if type(selected) in (RiskPolicy,PerformancePolicy):return replace(selected,timeframe=timeframe)
    if type(selected) is EconomicsPolicy:return selected
    return EconomicsPolicy(selected.initial_capital,selected.default_quantity,'0',selected.slippage_ticks,selected.settings,
        pyramiding=getattr(selected,'pyramiding',0),close_entries_rule=getattr(selected,'close_entries_rule','FIFO'),
        language_version=version,commission=CommissionPolicy('percent',selected.commission_percent))
