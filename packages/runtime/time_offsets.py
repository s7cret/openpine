"""Observed candle timestamps and deterministic UTC projections for time calls."""

from dataclasses import replace
from datetime import UTC

from packages.contracts.calendar import (
    canonical_timeframe, floor_open, next_open, requestable, shift_open, utc_datetime,
)
from packages.contracts.computed_targets import evaluate as frozen_target
from packages.contracts.context_ir import ir_nodes
from packages.contracts.model import CallIR
from packages.contracts.trading_sessions import (
    CALENDAR_ABI, FUTURE_LIMIT, OFFSET_OPERATIONS, PAST_LIMIT, period_target,
)
from packages.runtime.history import Ring
from packages.runtime.time_format import TimeFormatError, load_zone
from packages.runtime.trading_sessions import error, evaluate as evaluate_session


CHART = '@chart'


def plan(program, settings, environment):
    """Resolve the periods before observing the first candle or restoring a run."""
    primary = {'timeframe': environment.timeframe, 'symbol': environment.symbol}
    capacities = {CHART: PAST_LIMIT}
    for node in ir_nodes(program.statements):
        if not isinstance(node, CallIR):
            continue
        target = period_target(node)
        if target is not None:
            requested, _ = frozen_target(program, target, settings, allow_primary=True, primary=primary)
            period = interval(primary['timeframe'], requested)
            # Both offsets may independently traverse 5000 observed entries.
            key = CHART if period == primary['timeframe'] else period
            capacities[key] = 2 * PAST_LIMIT
    return capacities


def interval(chart, requested):
    try:
        period = canonical_timeframe(requested) if requested else chart
        if requestable(chart, period):
            return period
    except ValueError as cause:
        raise error('SESSION_TIMEFRAME', 'Use an equal or compatible higher timeframe in minutes or D/W/M.') from cause
    raise error('SESSION_TIMEFRAME', 'Use an equal or compatible higher timeframe in minutes or D/W/M.')


def new_history(program, settings, environment, budget, owner):
    return {key: Ring(budget, capacity, 'value', f'{owner} / time offsets / {key}')
            for key, capacity in plan(program, settings, environment).items()}


def commit(rings, stamp):
    rings[CHART].append(stamp)
    for period, ring in rings.items():
        if period != CHART:
            opening = floor_open(stamp, period)
            if opening != ring.ago(0):
                ring.append(opening)


def past(ring, anchor, distance):
    """Find the nth observed opening strictly before the anchor."""
    lo, hi = 0, min(ring.count, ring.capacity)
    while lo < hi:
        middle = (lo + hi) // 2
        if ring.ago(middle) < anchor:
            hi = middle
        else:
            lo = middle + 1
    return ring.ago(lo + distance - 1)


def calendar_distance(start, end, period):
    if period == 'M':
        left, right = utc_datetime(start), utc_datetime(end)
        return (right.year - left.year) * 12 + right.month - left.month
    return (end - start) // (next_open(max(0, start), period) - start)


def evaluate(arguments, context, *, closing):
    stamp, chart, requested, session, zone, chart_offset, *target_offsets = arguments
    target_offset = target_offsets[0] if target_offsets else 0
    if chart_offset is None or target_offset is None:
        return None
    if any(type(offset) is not int or not -FUTURE_LIMIT <= offset <= PAST_LIMIT
           for offset in (chart_offset, target_offset)):
        raise error('TIME_OFFSET_RANGE', 'Time offsets must be integers in -500..5000.')

    chart = canonical_timeframe(chart)
    timestamp = stamp
    if chart_offset > 0:
        timestamp = context.time_offsets[CHART].ago(chart_offset - 1)
        if timestamp is None:
            return None
    if chart_offset < 0 or target_offset:
        if session not in ('', '24x7'):
            raise error('TIME_OFFSET_SESSION', 'Projections and timeframe offsets require a continuous 24x7 session.')
        try:
            utc = load_zone(zone) == UTC
        except TimeFormatError as cause:
            raise error('SESSION_ZONE', str(cause)) from cause
        if not utc:
            raise error('SESSION_ZONE', 'Projections and timeframe offsets use the UTC market calendar.')
    if chart_offset < 0:
        try:
            timestamp = shift_open(stamp, chart, -chart_offset)
        except ValueError:
            return None
    if not target_offset:
        return evaluate_session((timestamp, chart, requested, session, zone),
                                replace(context, overload_id=context.overload_id + CALENDAR_ABI), closing=closing)

    period = interval(chart, requested)
    anchor = floor_open(timestamp, period)
    if target_offset < 0:
        try:
            anchor = shift_open(timestamp, period, -target_offset)
        except ValueError:
            return None
    else:
        current = floor_open(stamp, period)
        if anchor > current:
            projected = calendar_distance(current, anchor, period)
            if target_offset <= projected:
                anchor = current if target_offset == projected else shift_open(timestamp, period, -target_offset)
                return next_open(max(0, anchor), period) if closing else anchor
            target_offset -= projected
            anchor = current
        key = CHART if period == chart else period
        anchor = past(context.time_offsets[key], anchor, target_offset)
        if anchor is None:
            return None
    return next_open(max(0, anchor), period) if closing else anchor
