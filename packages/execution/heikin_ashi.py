"""Transform a stream of closed, pinned market candles in snapshot order."""
from dataclasses import replace
from math import isfinite

from packages.contracts import Diagnostic, OpenPineError


def finite_price(value):
    return type(value) in (int, float) and isfinite(value)


def candle(frame, previous):
    prices = frame.open, frame.high, frame.low, frame.close
    if not all(map(finite_price, prices)):
        raise OpenPineError(Diagnostic('INPUT_INVALID', 'HEIKIN_ASHI_OHLC',
                                      'Heikin Ashi requires complete finite OHLC in the requested snapshot.'))
    raw_open, raw_high, raw_low, raw_close = prices
    left, right = (raw_open, raw_close) if previous is None else previous
    ha_open = left * 0.5 + right * 0.5
    ha_close = sum(price * 0.25 for price in prices)
    return replace(frame, open=ha_open, close=ha_close, high=max(raw_high, ha_open, ha_close),
                   low=min(raw_low, ha_open, ha_close)), (ha_open, ha_close)


def restore_pair(value, cursor):
    if not cursor and value is None:
        return None
    if cursor and type(value) in (tuple, list) and len(value) == 2 and all(map(finite_price, value)):
        return tuple(value)
    raise ValueError('Invalid Heikin Ashi checkpoint state.')
