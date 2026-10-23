"""Bounded protective exits; policy identifiers select explicit OHLC semantics."""
from dataclasses import dataclass

PROFILE = 'openpine.protective-broker.1'
OPERATIONS = ('simulation.exit', 'simulation.cancel', 'simulation.cancel_all')


def exit_policy(version):
    return {'language_version':version,'profile_id':f'openpine.exits.v{version}.1',
        'basis':'reconstructed' if version<3 else 'artifact_defined_bounded_subset',
        'when':version<6,'ids':'nonempty_literal_128','from_entry':'explicit_literal_required',
        'levels':'absolute_positive_stop_limit_optional_tick_alignment',
        'quantity':'entire_single_position','groups':'one_bracket_bound_to_exact_entry',
        'activation':'next_bar_after_creation_market_orders_first',
        'gap':'first_open_crossing_at_open','intrabar':'level_touch_stop_first_when_both_ambiguous',
        'slippage':'adverse_market_and_stop_only_no_limit_slippage',
        'replacement':'same_exit_id_replace_other_exit_id_requires_cancel',
        'cancellation':'close_time_commands_no_retroactive_effect',
        'lifetime':'cancel_on_target_removal_close_or_reversal_no_future_entry_rebinding',
        'max_active_brackets':1,'max_commands_per_bar':16}


@dataclass(frozen=True)
class ProtectionIntent:
    call_site_id: str
    kind: str
    entry_id: str
    exit_id: str
    stop: str | None = None
    limit: str | None = None


@dataclass(frozen=True)
class ProtectionOrder:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: ProtectionIntent
    target_kind: str
    target_id: str
    direction: int
