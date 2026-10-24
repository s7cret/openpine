"""Explicit partial-exit ABI; legacy broker contracts remain independently pinned."""
from dataclasses import dataclass

PROFILE='openpine.partial-broker.1'
OPERATIONS=('simulation.exit_partial','simulation.close_partial')
NOTE='Partial exits use an explicit bounded historical OHLC model; incomplete v1-v3 quantity signatures are reconstructed and external equivalence is unverified.'

def partial_apis(version):
    when=[('when','bool','series',True)] if version<6 else []
    return [('strategy.exit','simulation.exit_partial',[('id','string','const',None),('from_entry','string','const',None),
        ('stop','float','series',{'kind':'na'}),('limit','float','series',{'kind':'na'}),
        ('qty','float','series',{'kind':'na'}),('qty_percent','float','series',100.0)]+when),
        ('strategy.close','simulation.close_partial',[('id','string','const',None),
        ('qty','float','series',{'kind':'na'}),('qty_percent','float','series',100.0)]+when)]

def partial_policy(version):
    return {'language_version':version,'profile_id':f'openpine.partial-exits.v{version}.1',
        'basis':'reconstructed' if version<4 else 'artifact_defined_bounded_subset','when':version<6,
        'activation':'explicit_named_qty_or_qty_percent','position':'one_entry_no_pyramiding',
        'quantity_priority':'defined_qty_else_defined_percent_else_100_percent',
        'quantity':'nonnegative_decimal_floor_to_step_clip_to_available',
        'percent':'0_to_100_of_current_target_quantity_frozen_at_command',
        'reservation':'source_order_no_double_booking_no_automatic_reallocation',
        'modification':'release_old_reservation_then_reserve_available_next_bar',
        'market_close':'frozen_quantity_exact_fill_target_next_open_clipped_to_remainder',
        'protection':'absolute_stop_limit_exact_pending_entry_or_open_fill',
        'intrabar':'all_open_crossings_first_then_stop_touches_then_limit_touches_sequence_ties',
        'ambiguity':'stop_first_per_bracket_and_global_stop_before_limit_for_unknown_touch_order',
        'fees':'rounded_per_fill_cumulative_pro_rata_entry_fee_final_slice_receives_residue',
        'accounting':'exact_decimal_slice_pnl_audit_totals_rounded_display_totals',
        'lifetime':'exact_target_no_future_rebinding_cancel_on_close_or_reversal',
        'max_active_brackets':16,'max_commands_per_bar':16,'replay':'confirmed_history_only'}

@dataclass(frozen=True)
class PartialCloseIntent:
    call_site_id: str
    kind: str
    entry_id: str
    direction: int
    quantity: str | None
    percent: str | None

@dataclass(frozen=True)
class PartialProtectionIntent:
    call_site_id: str
    kind: str
    entry_id: str
    exit_id: str
    stop: str | None=None
    limit: str | None=None
    quantity: str | None=None
    percent: str | None=None

@dataclass(frozen=True)
class PartialMarketOrder:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: object
    target_id: str | None=None

@dataclass(frozen=True)
class ReservedExit:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: PartialProtectionIntent
    target_kind: str
    target_id: str
    direction: int
    quantity: str
