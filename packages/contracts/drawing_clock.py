"""Drawing timestamps projected onto the global candle-index axis."""
from bisect import bisect_left

AXES = {'labels': ('x',), 'lines': ('x1', 'x2'), 'boxes': ('left', 'right')}


def timestamps(state):
    anchors = {record[key] for kind, keys in AXES.items() for record in state.get(kind, ())
               if record.get('xloc') == 'bar_time' for key in keys if record[key] is not None}
    anchors.update(time for row in state.get('polylines', ()) if row['xloc'] == 'bar_time' for time, _ in row['vertices'])
    return anchors


def projected(state, positions):
    result = {kind: [
        {**record, **{f'chart_{key}': positions.get(record[key]) for key in keys}}
        if record.get('xloc') == 'bar_time' else record for record in state[kind]]
        for kind, keys in AXES.items() if kind in state}
    if 'polylines' in state:
        result['polylines'] = [
            {**row, 'chart_vertices': [(positions.get(time), price) for time, price in row['vertices']]}
            if row['xloc'] == 'bar_time' else row for row in state['polylines']]
    return result


def chart_x(record, key):
    return record.get('chart_' + key) if record.get('xloc') == 'bar_time' else record[key]


def position(timestamp, neighbors):
    """Neighbors are time-ordered (timestamp, global bar index) pairs."""
    offset = bisect_left(neighbors, (timestamp, -1))
    if offset < len(neighbors) and neighbors[offset][0] == timestamp:
        return neighbors[offset][1]
    if len(neighbors) < 2:
        return None
    left = max(0, min(offset - 1, len(neighbors) - 2))
    (a, index), (b, _) = neighbors[left:left + 2]
    return index + (timestamp - a) / (b - a)


def stream_positions(timestamps, clock):
    """Resolve ordered anchors against a streaming clock in constant space."""
    points = iter(clock)
    left = next(points, None)
    right = next(points, None)
    if right is None:
        return {t: position(t, [left] if left else []) for t in timestamps}
    result = {}
    for t in sorted(timestamps):
        while t > right[0] and (upcoming := next(points, None)) is not None:
            left, right = right, upcoming
        result[t] = position(t, [left, right])
    return result
