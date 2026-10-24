"""Versioned, opt-in absolute-price entry orders for confirmed historical bars."""
from dataclasses import dataclass

PROFILE='openpine.pending-entry-broker.1'
OPERATION='simulation.entry_pending'
NOTE='Pending entries use a bounded historical OHLC model; incomplete v1-v2 signatures are reconstructed and intrabar activation never implies a known tick path.'

def entry_apis(version):
    return [('strategy.entry',OPERATION,[('id','string','const',None),
        ('long' if version<5 else 'direction','bool' if version<5 else 'string','const',None),
        ('qty','float','series',{'kind':'na'}),('limit','float','series',{'kind':'na'}),('stop','float','series',{'kind':'na'})]
        +([('when','bool','series',True)] if version<6 else []))]

def entry_policy(version):
    return {'language_version':version,'profile_id':f'openpine.pending-entries.v{version}.1',
        'basis':'reconstructed' if version<3 else 'artifact_defined_bounded_subset','when':version<6,
        'activation':'explicit_named_limit_or_stop','position':'one_entry_no_pyramiding',
        'orders':'market_limit_stop_stop_limit','first_eligible':'next_available_primary_bar',
        'limit':'open_improvement_else_touch_at_level_no_slippage',
        'stop':'open_crossing_else_touch_at_level_adverse_slippage',
        'stop_limit':'open_activation_allows_limit_same_bar_intrabar_activation_defers_limit_to_next_bar',
        'ordering':'sequence_at_open_then_existing_protection_then_at_most_one_intrabar_entry_fill',
        'new_protection':'open_entry_same_bar_intrabar_entry_next_bar',
        'modification':'replace_exact_order_reset_activation_cancel_attached_protection',
        'lifetime':'persist_until_filled_replaced_or_explicitly_cancelled_no_expiry',
        'quantities':'positive_fixed_step_aligned_no_volume_partial_fills',
        'accounting':'partial_broker_exact_decimal_slices_and_cumulative_entry_fee_allocation',
        'max_pending_entries':16,'max_active_brackets':16,'max_commands_per_bar':16,
        'replay':'confirmed_history_only','external_execution_verified':False}

@dataclass(frozen=True)
class EntryIntent:
    call_site_id: str
    kind: str
    entry_id: str
    direction: int
    quantity: str | None
    limit: str | None=None
    stop: str | None=None

@dataclass(frozen=True)
class EntryOrder:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: EntryIntent
    activated_bar: int | None=None
    activated_time: int | None=None
    activation_basis: str | None=None

    @property
    def order_kind(self):
        return 'stop_limit' if self.intent.stop is not None and self.intent.limit is not None else 'stop' if self.intent.stop is not None else 'limit' if self.intent.limit is not None else 'market'
