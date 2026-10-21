"""Pure projection from recorded arrivals into the received-event profile."""

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import digest
import hashlib
from pathlib import Path

MAPPING='openpine.arrival-projection.1'


def implementation_identity():
    root=Path(__file__).resolve().parents[2]
    files=('packages/marketdata/live_mapping.py','packages/marketdata/stream.py','packages/marketdata/timeframes.py',
        'packages/contracts/market.py','packages/contracts/live.py')
    return digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files})


def initial():
    return {'mapping':MAPPING,'sequence':0,'availability':0,'active':None,'last_closed':None,
        'gaps':0,'recovered_bars':0,'clock_clamps':0}


def project(state,context_id,*,time,available_at,closed,open,high,low,close,volume,recovered=False,boundary=None):
    if state['mapping']!=MAPPING:raise ValueError('Unsupported live projection profile.')
    if state['last_closed'] is not None and time<=state['last_closed']:return None,state
    active=state['active']
    if active is not None:
        if time!=active['time']:raise ValueError('Missing confirmed close in the recorded stream; REST repair is required.')
        if (open!=active['open'] or high<active['high'] or low>active['low'] or volume<active['volume']):
            raise ValueError('Reconciliation conflicts with an already observed candle prefix.')
    availability=max(available_at,state['availability'],time if boundary is None else boundary)
    event=MarketEvent(state['sequence'],context_id,'close' if closed else 'open' if active is None else 'update',
        time,availability,0 if active is None else active['revision']+1,open,high,low,close,volume)
    return event, {**state,'sequence':state['sequence']+1,'availability':availability,
        'active':None if closed else event.payload(),'last_closed':time if closed else state['last_closed'],
        'clock_clamps':state['clock_clamps']+int(availability!=available_at),
        'recovered_bars':state['recovered_bars']+int(recovered and closed)}
