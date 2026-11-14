"""Bounded price histories and weighted calculations for ALMA, HMA and COG."""

import copy
from decimal import Context
import math

from packages.contracts.loops import CANCELLATION_INTERVAL
from packages.contracts.weighted_indicators import ALMA, COG, HMA
from packages.runtime.history import Ring
from . import KernelError
from .dynamic_windows import COUNT_LIMIT, PrefixWindow, bound, export_window, fork_window, ratio, require, restore_window
from .technical import units
from .window_indicators import numeric


WMA = 'window.wma'


def alma(values, source, length, offset, sigma, floor, charge_work):
    if values.count == COUNT_LIMIT:
        raise KernelError('WINDOW_COUNT', 'ALMA call count exceeds int64.', 'rule.history')
    charge_work(1)
    source = numeric(source)
    if offset is not None and sigma is not None and sigma <= 0:
        raise KernelError('ALMA_SIGMA', 'ALMA sigma must be positive.', 'rule.history')
    result = None
    if None not in (source, offset, sigma, floor) and length <= values.count + 1:
        result = gaussian_mean(values, source, length, offset, sigma, floor, charge_work)
    values.append(source)
    return result


def gaussian_mean(values, source, length, offset, sigma, floor, charge_work):
    if length == 1:
        return source
    last = length - 1
    center = offset * last
    if math.isfinite(center):
        if floor:
            center = math.floor(center)
        anchor = round(min(last, max(0, center)))
    else:
        anchor = last if center > 0 else 0
    norm = weighted = 0
    # Avoid platform libm differences in checked portable results.
    decimal = Context(prec=34)
    for start in range(0, length, CANCELLATION_INTERVAL):
        end = min(length, start + CANCELLATION_INTERVAL)
        charge_work(end - start)
        for index in range(start, end):
            price = source if index == last else values.ago(last - index - 1)
            if price is None:
                return None
            # Relative to the nearest sample, so at least one weight remains 1
            # even when every unnormalized Gaussian weight would underflow.
            if index == anchor:
                weight = 1 << 1074
            else:
                distance = (index - anchor) / length
                midpoint = (index + anchor) / 2
                displacement = (midpoint - center) / length if math.isfinite(center) else (
                    midpoint / length - offset * (last / length))
                decay = 0. if displacement == 0 else (distance * sigma) * (displacement * sigma)
                weight = units(float(decimal.exp(decimal.create_decimal_from_float(-decay)))) if decay < 746 else 0
            norm += weight
            weighted += weight * units(price)
    return ratio(weighted, norm << 1074)


def center_of_gravity(window, source, length):
    window.push(numeric(source), length)
    if window.count < length:
        return None
    total, weighted = window.linear_totals(length)
    # The most recent defined sample has weight 1, the oldest has weight n.
    return ratio(weighted - (length + 1) * total, total) if total else None


class HullWindow:
    def __init__(self, budget, horizon):
        budget.preflight(4, 'exact')
        self.source = PrefixWindow(budget, WMA, horizon)
        self.smooth = PrefixWindow(budget, WMA, math.isqrt(horizon))

    def update(self, source, length):
        if self.source.length is not None and self.source.length != length:
            raise KernelError('TECHNICAL_LENGTH_CHANGED', 'HMA length must remain constant.', 'rule.history')
        source = numeric(source)
        smooth_length = math.isqrt(length)
        raw = None
        if source is not None:
            growth = 2 * (self.source.count < self.source.horizon)
            if self.source.count + 1 >= length:
                growth += 2 * (self.smooth.count < self.smooth.horizon)
            self.source.budget.preflight(growth, 'exact')
        self.source.push(source, length)
        if source is not None and self.source.count >= length:
            half = max(1, length // 2)
            _, short = self.source.linear_totals(half)
            _, long = self.source.linear_totals(length)
            short_norm = half * (half + 1) // 2
            long_norm = length * (length + 1) // 2
            raw = ratio(2 * short * long_norm - long * short_norm, (short_norm * long_norm) << 1074)
        return self.smooth.update(raw, smooth_length)


def kernel(operation):
    def run(arguments, context):
        source, length, *options = arguments
        horizon, budget = context.history_capacity, context.budget
        bound(length, horizon)
        if operation == ALMA:
            values = context.state(ALMA, lambda: Ring(budget, horizon))
            offset, sigma, *floor = options
            return alma(values, source, length, offset, sigma, floor[0] if floor else False, context.charge_work)
        if operation == COG:
            window = context.state(COG, lambda: PrefixWindow(budget, WMA, horizon))
            return center_of_gravity(window, source, length)
        return context.state(HMA, lambda: HullWindow(budget, horizon)).update(source, length)

    return run


def fork_hull(state, budget):
    clone = copy.copy(state)
    clone.source = fork_window(state.source, budget)
    clone.smooth = fork_window(state.smooth, budget)
    return clone


def export_hull(state):
    return dict(source=export_window(state.source)[1], smooth=export_window(state.smooth)[1])


def restore_hull(value, budget, updates, horizon):
    require(type(value) is dict and value.keys() == {'source', 'smooth'})
    state = object.__new__(HullWindow)
    state.source = source = restore_window(WMA, value['source'], budget, updates, horizon)
    state.smooth = smooth = restore_window(WMA, value['smooth'], budget, updates, math.isqrt(horizon))
    require(source.calls == smooth.calls and smooth.length == math.isqrt(source.length))
    require(smooth.count == max(0, source.count - source.length + 1))
    return state
