"""Arrival-time execution is a separate profile from logical-boundary replay."""

from .realtime import PROFILE, policy

RECEIVED_PROFILE='openpine.received-realtime.1'


def event_policy(version,profile=PROFILE):
    base=policy(version)
    if profile==PROFILE:return base
    if profile!=RECEIVED_PROFILE:raise ValueError('Unknown event availability profile.')
    return {**base,'profile_id':RECEIVED_PROFILE,
        'contexts':'one_explicit_primary_context',
        'order':'durable_arrival_sequence_nondecreasing_local_availability',
        'open':'first_observed_cumulative_candle_not_a_fabricated_exchange_open_event',
        'close':'confirmed_exchange_close_available_when_received',
        'clock':'max_local_received_at_previous_availability_candle_boundary_raw_timestamps_preserved',
        'strategy':'calculate_on_confirmed_close_simulated_fill_next_bar_open_from_OHLCV',
        'gaps':'durable_gap_record_rest_reconciliation_of_closed_bars_missing_previews_not_reconstructed',
        'warmup':'confirmed_close_only_snapshot_at_recorded_availability',
        'corrections':'confirmed_hash_conflict_halts_requires_new_session',
        'evidence_status':'local_paper_simulation_not_exchange_execution_or_tradingview_conformance'}
