"""Strict cumulative Spot observations and a bounded, non-dropping WS boundary."""

import time
import threading
from urllib.parse import quote

from packages.contracts.market import CandleObservation, InstrumentKey
from packages.contracts.wire import loads
from .timeframes import EXECUTION, INTERVALS, floor_open, next_open


def normalize(payload, instrument, timeframe, received_at):
    if type(instrument) is not InstrumentKey or timeframe not in EXECUTION: raise ValueError('Unsupported live instrument/timeframe.')
    instrument.pine_symbol
    if type(payload) is not dict or payload.get('e')!='kline' or payload.get('s')!=instrument.symbol:
        raise ValueError('WebSocket message has a different instrument/event type.')
    candle=payload.get('k')
    if (type(candle) is not dict or candle.get('s')!=instrument.symbol or candle.get('i')!=INTERVALS[timeframe]
            or type(candle.get('t')) is not int or type(candle.get('T')) is not int
            or type(candle.get('x')) is not bool
            or floor_open(candle['t'],timeframe)!=candle['t']
            or next_open(candle['t'],timeframe)-1!=candle['T']
            or any(type(candle.get(k)) is not str or len(candle[k])>64 for k in ('o','h','l','c','v'))):
        raise ValueError('Malformed or unaligned cumulative WebSocket candle.')
    return CandleObservation(instrument,timeframe,candle['t'],candle['T'],payload['E'],received_at,candle['x'],
        *(float(candle[k]) for k in ('o','h','l','c','v')))


class StreamDisconnected(OSError): pass


class BinanceSocket:
    """One physical stream, bounded library buffers, no application coalescing.

    A slow consumer applies TCP backpressure. A disconnect is exposed so the
    application records a gap and reconciles closed candles before continuing.
    """
    def __init__(self,instrument,timeframe,*,connector=None):
        instrument.pine_symbol
        if timeframe not in EXECUTION: raise ValueError('Unsupported live timeframe.')
        if connector is None:
            from websockets.sync.client import connect
            connector=connect
        url=f'wss://stream.binance.com:9443/ws/{quote(instrument.symbol.lower(),safe="")}@kline_{INTERVALS[timeframe]}'
        self.context=connector(url,open_timeout=10,close_timeout=2,ping_interval=20,ping_timeout=20,
            max_size=65536,max_queue=16,compression=None)
        self.socket=self.context.__enter__()
        self.lock,self.closed=threading.Lock(),False

    def recv(self,timeout=.5):
        try:raw=self.socket.recv(timeout=timeout)
        except TimeoutError:return None
        except Exception as error:
            from websockets.exceptions import ConnectionClosed
            if isinstance(error,(ConnectionClosed,OSError)): raise StreamDisconnected(str(error)) from error
            raise
        return loads(raw,max_bytes=65536)

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed=True;self.context.__exit__(None,None,None)
