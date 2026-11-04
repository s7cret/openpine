"""Resolve drawing timestamps using bounded reads of immutable candle parts."""
from bisect import bisect_left
from functools import lru_cache

from packages.contracts.drawing_clock import position, stream_positions
from .columnar import connection


def positions(source, timestamps):
    if not timestamps:
        return {}
    ranges = sorted(source.manifest['parts'], key=lambda part: (part['select_start'], part['select_end']))
    if any(a['select_end'] > b['select_start'] for a, b in zip(ranges, ranges[1:])):
        # Revisions can share a range. The canonical iterator owns deduplication.
        return stream_positions(timestamps, ((bar.time, index) for index, bar in enumerate(source.rows())))

    with connection(threads=source.threads, memory_mb=source.memory_mb) as db:
        blocks, ends, offset = [], [], 0
        for part in ranges:
            lo, hi = part['select_start'], part['select_end']
            if lo == part['first_ms'] and hi == part['last_ms'] + 1:
                count, last = part['count'], part['last_ms']
            else:
                path = str(source.objects.verify(part['object_id']))
                count, last = db.execute(
                    'SELECT count(*),max(time) FROM read_parquet(?) WHERE time>=? AND time<?',
                    [path, lo, hi]).fetchone()
            if count:
                blocks.append((part['object_id'], lo, hi, offset, count))
                ends.append(last)
                offset += count
        if offset != source.identity.count:
            raise ValueError('Candle clock count differs from the snapshot.')

        @lru_cache(maxsize=3)
        def times(block):
            sha, lo, hi, _, count = blocks[block]
            result = [t for t, in db.execute(
                'SELECT time FROM read_parquet(?) WHERE time>=? AND time<? ORDER BY time',
                [str(source.objects.verify(sha)), lo, hi]).fetchall()]
            if len(result) != count:
                raise ValueError('Candle clock block count differs from its manifest.')
            return result

        result = {}
        for t in sorted(timestamps):
            block = min(bisect_left(ends, t), len(blocks) - 1)
            dates = times(block)
            index = bisect_left(dates, t)
            base = blocks[block][3]
            neighbors = [(dates[i], base + i) for i in range(max(0, index - 2), min(len(dates), index + 2))]
            if block and (index == 0 or len(dates) == 1):
                neighbors.insert(0, (times(block - 1)[-1], base - 1))
            if index >= len(dates) - 1 and block + 1 < len(blocks):
                neighbors.append((times(block + 1)[0], base + len(dates)))
            result[t] = position(t, neighbors)
        return result
