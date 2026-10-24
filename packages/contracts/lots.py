"""Bounded, explicitly selected multi-entry historical accounting contracts."""
from dataclasses import dataclass
from .trading import BrokerPolicy

PROFILE='openpine.lot-broker.1'
OPERATION='simulation.strategy_lots'
NOTE='Lot accounting is a bounded historical reconstruction: v1 header and v1-v3 close-entry selection gaps are explicit; no external TradingView execution oracle was used.'

@dataclass(frozen=True)
class LotBrokerPolicy(BrokerPolicy):
    profile_id: str=PROFILE
    pyramiding: int=1
    close_entries_rule: str='FIFO'

    def __post_init__(self):
        super().__post_init__()
        if self.profile_id!=PROFILE or type(self.pyramiding) is not int or not 0<=self.pyramiding<=16 or self.close_entries_rule not in ('FIFO','ANY'):
            raise ValueError('Lot broker requires pyramiding 0..16 and FIFO or ANY selection.')

def lot_apis(version):
    return [('strategy',OPERATION,[('title','string','const',None),('overlay','bool','const',False),
        ('pyramiding','int','const',0),('default_qty_type','string','const','fixed' if version<6 else 'percent_of_equity'),
        ('default_qty_value','float','const',1.0),('initial_capital','float','const',1000000.0 if version==5 else 100000.0),
        ('slippage','int','const',0),('commission_type','string','const','percent'),('commission_value','float','const',0.0),
        ('close_entries_rule','string','const','FIFO')])]

def lot_policy(version):
    return {'language_version':version,'profile_id':f'openpine.lots.v{version}.1',
        'basis':'reconstructed_header' if version==1 else 'artifact_header_with_reconstructed_close_rule' if version<4 else 'artifact_defined_bounded_subset',
        'activation':'positive_literal_pyramiding_or_explicit_close_entries_rule','max_open_lots':16,'max_pending_entries':16,
        'max_active_brackets':16,'max_commands_per_bar':16,'pyramiding_zero':'one_open_lot',
        'pyramiding':'cap_checked_at_eligible_fill_excess_ignored','replacement':'same_pending_entry_id_only_open_lots_preserved',
        'position':'same_direction_lots_aggregate_signed_quantity_and_weighted_price',
        'close_quantity':'matching_entry_id_quantity_at_command_then_floor_clip_and_freeze',
        'FIFO':'allocate_oldest_open_lots_globally_at_binding','ANY':'allocate_oldest_matching_entry_id_lots_at_binding',
        'protection':'bind_existing_lots_else_exact_pending_order_freeze_lots_on_activation',
        'reservations':'per_lot_no_double_booking_no_future_rebinding','close':'frozen_lot_ids_clipped_to_remaining_next_open',
        'reverse':'close_all_old_lots_then_open_requested_new_quantity_two_fills',
        'fees':'one_rounded_fee_per_fill_cumulative_entry_allocation_and_exit_fee_residue_on_last_slice',
        'orders':'market_limit_stop_stop_limit_confirmed_OHLC_pending_entry_phase_order',
        'intrabar':'at_most_one_entry_fill_new_intrabar_protection_waits_next_bar',
        'state':'active_lots_only_closed_slices_in_journal','replay':'confirmed_history_only','external_execution_verified':False}

@dataclass(frozen=True)
class LotTarget:
    kind: str
    target_id: str
    quantity: str

@dataclass(frozen=True)
class LotCloseOrder:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: object
    targets: tuple[LotTarget,...]

@dataclass(frozen=True)
class LotProtection:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: object
    direction: int
    targets: tuple[LotTarget,...]
    eligible_bar: int
