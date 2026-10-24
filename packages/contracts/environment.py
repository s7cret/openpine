"""Closed UTC/24x7 execution environment, independent of the host clock."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from .calendar import timeframe,aligned
from .wire import digest

CALENDAR = ('year', 'month', 'dayofmonth', 'dayofweek', 'hour', 'minute', 'second')
FLAGS = ('isfirst', 'islast', 'ishistory', 'isrealtime', 'isnew', 'isconfirmed', 'islastconfirmedhistory')
TIMEFRAMES = ('period', 'multiplier', 'isintraday', 'isminutes', 'isseconds', 'isdaily', 'isweekly', 'ismonthly', 'isdwm')
INSTRUMENT_FIELDS = ('mintick', 'pointvalue', 'currency', 'basecurrency', 'type')


def environment_policy(version):
    return {'timezone':'UTC', 'session':'24x7', 'calendar':'calendar-utc.1',
        'time_unit':'unix_milliseconds', 'time_close':'calendar_exclusive_end',
        'historical_last':'frozen_dataset_end_not_batch_end',
        'observed_history':'explicit_pinned_historical_end_boundary',
        'observed_realtime':'events_after_pinned_history_or_all_events_without_history_boundary',
        'metadata':'immutable_snapshot_provenance_or_explicit_replay_configuration',
        'missing_instrument':'error_if_required', 'timestamp':'strict_utc_1970_to_9999',
        'availability':'reviewed_per_version', 'external_execution_verified':False}


def environment_references(version):
    result = []
    def add(name, slot, base, qualifier='series'):
        result.append({'name':name,'slot':'environment.'+slot,'base':base,'qualifier':qualifier})
    add('time','time','int')
    if version >= 4:add('time_close','time_close','int')
    for name in CALENDAR:add(name,name,'int')
    flags = FLAGS if version >= 4 else FLAGS[:-1] if version == 3 else ('islast','isrealtime') if version == 2 else ()
    for flag in flags:add('barstate.'+flag,'barstate.'+flag,'bool')
    for name in TIMEFRAMES:
        if version < 4 and name not in ('period','isintraday','isdaily','isweekly','ismonthly'):continue
        add(('timeframe.' if version >= 4 else '')+name,'timeframe.'+name,
            'string' if name=='period' else 'int' if name=='multiplier' else 'bool','simple')
    for name in ('tickerid','ticker','prefix','root','session','timezone',*INSTRUMENT_FIELDS):
        if name=='timezone' and version < 3:continue
        if name in ('currency','basecurrency','type') and version < 4:continue
        public=name if name in ('ticker','tickerid') and version < 4 else 'syminfo.'+name
        add(public,'syminfo.'+name,'float' if name in ('mintick','pointvalue') else 'string','simple')
    if version==4:add('tickerid','syminfo.tickerid','string','simple')
    return result


def environment_apis(version):
    apis=[]
    def add(name,parameters,operation):
        apis.append({'id':f'openpine.environment.v{version}.{name}.{len(parameters)}', 'name':name,
            'parameters':parameters, 'operation':operation, 'named':version>=3})
    # Legacy overload metadata is incomplete: numeric UTC forms are reviewed
    # reconstructions, with named arguments only from v3.
    if version>=2:
        names=('year','month','day','hour','minute','second')
        for size in (5,6):add('timestamp',list(names[:size]),'environment.timestamp'+str(size))
    for name in CALENDAR:add(name,['time'],'environment.'+name)
    return apis


def uses_environment(value):
    from .model import ReferenceIR, CallIR
    if isinstance(value,ReferenceIR):return value.contract.slot.startswith('environment.')
    if isinstance(value,CallIR) and value.operation.startswith('environment.'):return True
    if is_dataclass(value):return any(uses_environment(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_environment,value))


@dataclass(frozen=True)
class InstrumentValues:
    mintick: str
    pointvalue: str
    currency: str
    basecurrency: str
    type: str

    def __post_init__(self):
        from .trading import decimal_text
        for name in ('mintick','pointvalue'):
            value=getattr(self,name)
            if type(value) is not str or len(value)>128 or not re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?',value) or decimal_text(value,positive=True)!=value or not 0<float(value)<float('inf'):
                raise ValueError('Instrument numbers require finite positive canonical decimal strings.')
        for name in ('currency','basecurrency','type'):
            value=getattr(self,name)
            if type(value) is not str or not 0<len(value)<=64 or any(ord(c)<32 for c in value):raise ValueError('Invalid instrument text.')

    def __getitem__(self,name):
        if name not in INSTRUMENT_FIELDS:raise KeyError(name)
        return getattr(self,name)

    @classmethod
    def from_payload(cls,value):
        if type(value) is not dict or value.keys()!=set(INSTRUMENT_FIELDS):raise ValueError('Invalid instrument metadata fields.')
        return cls(**value)


def spot_instrument(metadata):
    from .market import InstrumentMetadata
    if type(metadata) is dict:metadata=InstrumentMetadata.from_payload(metadata)
    if type(metadata) is not InstrumentMetadata or metadata.instrument.market!='spot':raise ValueError('Expected pinned spot metadata.')
    return asdict(InstrumentValues(metadata.tick_size,'1',metadata.quote_asset,metadata.base_asset,'crypto'))


ENVIRONMENT_NAMES=frozenset(r['name'] for v in range(1,7) for r in environment_references(v))


@dataclass(frozen=True)
class Environment:
    context_id: str
    symbol: str
    timeframe: str
    count: int | None = None
    instrument: InstrumentValues | dict | None = None
    mode: str = 'historical'
    history_last_ms: int | None = None
    schema_version: str = 'openpine.environment.1'

    def __post_init__(self):
        timeframe(self.timeframe)
        if (self.schema_version!='openpine.environment.1' or self.mode not in ('historical','observed')
                or any(type(v) is not str or not 0<len(v)<=256 for v in (self.context_id,self.symbol))
                or self.count is not None and (type(self.count) is not int or not 1<=self.count<=10_000_000)
                or self.mode=='historical' and self.count is None
                or self.history_last_ms is not None and (type(self.history_last_ms) is not int or not 0<=self.history_last_ms<253402214400000)):
            raise ValueError('Invalid frozen execution environment.')
        if self.mode=='observed' and self.count is not None or self.mode=='historical' and self.history_last_ms is not None or self.history_last_ms is not None and not aligned(self.history_last_ms,self.timeframe):raise ValueError('Environment mode/boundary differs from its calendar.')
        if self.instrument is not None:
            selected=InstrumentValues.from_payload(self.instrument) if type(self.instrument) is dict else self.instrument
            if type(selected) is not InstrumentValues:raise ValueError('Invalid instrument environment.')
            object.__setattr__(self,'instrument',selected)

    def payload(self):return asdict(self)

    @property
    def sha256(self):return digest(self.payload())

    @classmethod
    def from_payload(cls,value):
        if type(value) is not dict or value.keys()!={f.name for f in fields(cls)}:raise ValueError('Invalid environment fields.')
        return cls(**value)


def source_environment(source, *, instrument=None):
    identity=source.identity
    saved=getattr(source,'manifest',{}).get('provenance',{}).get('environment')
    if saved is not None:
        if type(saved) is not dict or saved.keys()!={'schema_version','instrument'} or saved['schema_version']!='openpine.instrument-environment.1':
            raise ValueError('Invalid snapshot instrument environment.')
        if instrument is not None and instrument!=saved['instrument']:raise ValueError('Instrument settings conflict with pinned snapshot metadata.')
        instrument=saved['instrument']
    return Environment(identity.context_id,identity.symbol,identity.timeframe,identity.count,instrument)


def validate_instrument_envelope(value):
    if type(value) is not dict or value.keys()!={'schema_version','instrument'} or value['schema_version']!='openpine.instrument-environment.1':raise ValueError('Invalid instrument environment envelope.')
    InstrumentValues.from_payload(value['instrument'])
