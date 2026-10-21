"""Market identity and acquisition contracts; independent of transports/storage."""

import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation


def timestamp(value):
    return type(value) is int and 0 <= value < 2**63


def symbol_text(value):
    return type(value) is str and 2<=len(value)<=30 and value.isalnum() and value.upper()==value


def asset_text(value):
    return type(value) is str and 0<len(value)<=32 and value.replace('_','').isalnum() and value.upper()==value


@dataclass(frozen=True)
class InstrumentKey:
    exchange: str
    market: str
    symbol: str
    price_kind: str = 'trade'

    def __post_init__(self):
        if (type(self.exchange) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', self.exchange)
                or self.market not in ('spot', 'usdm', 'coinm')
                or not symbol_text(self.symbol)
                or self.price_kind not in ('trade', 'mark', 'index')):
            raise ValueError('Invalid canonical instrument identity.')

    @property
    def key(self): return '/'.join((self.exchange, self.market, self.symbol, self.price_kind))

    @property
    def pine_symbol(self):
        if (self.exchange, self.market, self.price_kind) != ('binance', 'spot', 'trade'):
            raise ValueError('This acquisition profile supports Binance Spot trade candles only.')
        return 'BINANCE:' + self.symbol

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.keys() != set(cls.__dataclass_fields__):
            raise ValueError('Invalid instrument fields.')
        return cls(**value)

    @classmethod
    def from_pine(cls, value):
        if type(value) is not str or not value.startswith('BINANCE:'):
            raise ValueError('Use an explicit BINANCE:SYMBOL Spot identity.')
        return cls('binance', 'spot', value[8:])


def positive_decimal(value):
    if type(value) is not str or len(value) > 64: raise ValueError('Expected a decimal string.')
    try: number = Decimal(value)
    except InvalidOperation as error: raise ValueError('Invalid decimal string.') from error
    if not number.is_finite() or not 0 < number <= Decimal('1e24') or number.as_tuple().exponent < -16:
        raise ValueError('Market step exceeds supported precision/range.')
    return format(number.normalize(), 'f')


@dataclass(frozen=True)
class InstrumentMetadata:
    instrument: InstrumentKey
    base_asset: str
    quote_asset: str
    status: str
    tick_size: str
    quantity_step: str
    observed_at: int
    first_available_ms: int | None = None
    schema_version: str = 'openpine.instrument-metadata.1'

    def __post_init__(self):
        if (type(self.instrument) is not InstrumentKey or self.schema_version != 'openpine.instrument-metadata.1'
                or not asset_text(self.base_asset) or not asset_text(self.quote_asset)
                or type(self.status) is not str or not re.fullmatch(r'[A-Z0-9_]{1,32}',self.status)
                or not timestamp(self.observed_at)
                or self.first_available_ms is not None and not timestamp(self.first_available_ms)):
            raise ValueError('Invalid instrument metadata.')
        for value in (self.tick_size, self.quantity_step):
            if positive_decimal(value) != value: raise ValueError('Market steps must be canonical decimal strings.')

    def payload(self): return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.keys() != set(cls.__dataclass_fields__): raise ValueError('Invalid metadata fields.')
        return cls(**{**value, 'instrument': InstrumentKey.from_payload(value['instrument'])})


@dataclass(frozen=True)
class HistoryRequest:
    instrument: InstrumentKey
    timeframe: str
    start_ms: int
    end_ms: int
    gap_policy: str = 'fail'
    refresh: bool = False
    source: str = 'rest'
    schema_version: str = 'openpine.history-request.1'

    def __post_init__(self):
        if (type(self.instrument) is not InstrumentKey or self.schema_version != 'openpine.history-request.1'
                or type(self.timeframe) is not str or not self.timeframe or len(self.timeframe) > 8
                or not timestamp(self.start_ms) or not timestamp(self.end_ms) or self.start_ms >= self.end_ms
                or self.gap_policy not in ('fail', 'allow_with_metadata') or type(self.refresh) is not bool
                or self.source not in ('rest', 'archive')):
            raise ValueError('Invalid history request.')
        self.instrument.pine_symbol

    def payload(self): return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.keys() != set(cls.__dataclass_fields__): raise ValueError('Invalid history request fields.')
        return cls(**{**value, 'instrument': InstrumentKey.from_payload(value['instrument'])})


@dataclass(frozen=True)
class CandleObservation:
    instrument: InstrumentKey
    timeframe: str
    open_time: int
    close_time: int
    exchange_event_time: int
    received_at: int
    closed: bool
    open: float
    high: float
    low: float
    close: float
    volume: float
    schema_version: str = 'openpine.candle-observation.1'

    def __post_init__(self):
        if (type(self.instrument) is not InstrumentKey or self.schema_version != 'openpine.candle-observation.1'
                or type(self.timeframe) is not str or not 0 < len(self.timeframe) <= 8
                or any(not timestamp(x) for x in (self.open_time, self.close_time, self.exchange_event_time, self.received_at))
                or self.close_time <= self.open_time or self.exchange_event_time < self.open_time
                or self.closed is True and self.exchange_event_time <= self.close_time
                or type(self.closed) is not bool
                or any(type(x) is not float or not math.isfinite(x) for x in (self.open, self.high, self.low, self.close, self.volume))
                or self.low <= 0 or self.low > min(self.open, self.close)
                or self.high < max(self.open, self.close) or self.volume < 0):
            raise ValueError('Invalid cumulative candle observation.')

    def payload(self): return asdict(self)

    @classmethod
    def from_payload(cls, value):
        if type(value) is not dict or value.keys() != set(cls.__dataclass_fields__): raise ValueError('Invalid observation fields.')
        return cls(**{**value, 'instrument': InstrumentKey.from_payload(value['instrument'])})
