"""Closed, provider independent cumulative candle events for offline replay."""

import math
import re
from dataclasses import asdict, dataclass

PROFILE = "openpine.observed-realtime.1"
EVENT_SCHEMA = "openpine.market-event.1"


def integer(value, low=0, high=2**63-1):
    return type(value) is int and low <= value <= high


@dataclass(frozen=True)
class MarketEvent:
    sequence: int
    context_id: str
    kind: str
    time: int
    event_time: int
    revision: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        if (not integer(self.sequence, high=10_000_000) or not integer(self.time)
                or not integer(self.event_time, self.time) or not integer(self.revision, high=10_000_000)
                or type(self.context_id) is not str or not 0 < len(self.context_id) <= 256
                or self.kind not in ('open', 'update', 'close')
                or any(type(v) is not float or not math.isfinite(v) for v in
                    (self.open, self.high, self.low, self.close, self.volume))
                or self.low <= 0 or self.low > min(self.open, self.close)
                or self.high < max(self.open, self.close) or self.volume < 0):
            raise ValueError('Invalid cumulative OHLCV market event.')

    def payload(self):
        return {'schema_version': EVENT_SCHEMA, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if (type(value) is not dict or value.keys() != {'schema_version', *cls.__dataclass_fields__}
                or value['schema_version'] != EVENT_SCHEMA):
            raise ValueError('Invalid market event fields/schema.')
        return cls(**{k: v for k, v in value.items() if k != 'schema_version'})


def replay_id(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid replay identifier.')
    return value


def policy(version):
    if type(version) is not int or version not in range(1, 7):
        raise ValueError('Invalid Pine version.')
    return {'profile_id': PROFILE, 'language_version': version,
        'indicator': 'rollback_before_each_observed_update_commit_on_close',
        'strategy': 'calculate_on_close_market_fill_next_observed_open',
        'variables': 'existing_pinned_language_profile',
        'varip': 'uncovered', 'barstate': 'uncovered',
        'request_api': 'security' if version < 5 else 'request.security',
        'requests': 'existing_close_or_ema_profile',
        'request_visibility': 'observed_developing_for_gaps_off_closed_events_for_gaps_on',
        'lookahead': 'observed_only_in_both_modes_no_historical_future_injection',
        'historical_equivalence': 'single_context_or_explicit_lookahead_off_at_close',
        'order': 'global_contiguous_sequence_event_time_context_close_before_primary_close_before_open',
        'duplicates': 'identical_sequence_payload_idempotent_conflict_rejected',
        'gaps': 'preserve_missing_bars_reject_missing_sequence_or_revision',
        'corrections': 'closed_bars_immutable_rebuild_new_replay',
        'evidence_status': 'local_execution_profile_not_tradingview_conformance'}
