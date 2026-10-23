"""Coverage of confirmed candles is distinct from coverage of fetched intervals."""

from packages.contracts.calendar import floor_open, next_open


def first_open(timestamp,timeframe):
    start=floor_open(timestamp,timeframe)
    return start if start==timestamp else next_open(timestamp,timeframe)


def count_slots(start, stop, timeframe):
    if timeframe != 'M':
        duration=next_open(start,timeframe)-floor_open(start,timeframe)
        return max(0,(stop-start+duration-1)//duration)
    count=0
    while start<stop: count+=1;start=next_open(start,timeframe)
    return count


def coverage_report(query, rows, *, as_of, first_available=None, unfetched=(), max_intervals=200):
    timeframe=query.timeframe
    initial=first_open(query.start_ms,timeframe)
    final=min(query.end_ms,floor_open(as_of,timeframe))
    available=floor_open(first_available,timeframe) if first_available is not None else initial
    intervals=[];counts={'before_available':0,'not_closed':0,'not_fetched':0,'missing_candle':0}
    interval_count=0

    def add(start,stop,reason):
        nonlocal interval_count
        if start>=stop: return
        counts[reason]+=count_slots(start,stop,timeframe)
        if intervals and intervals[-1]['end_ms']==start and intervals[-1]['reason']==reason:
            intervals[-1]['end_ms']=stop
            return
        interval_count+=1
        if len(intervals)<max_intervals: intervals.append({'start_ms':start,'end_ms':stop,'reason':reason})

    def missing(start,stop):
        cursor=start
        for a,b in unfetched:
            if b<=cursor or a>=stop: continue
            add(cursor,min(a,stop),'missing_candle')
            add(max(cursor,a),min(b,stop),'not_fetched')
            cursor=max(cursor,min(b,stop))
        add(cursor,stop,'missing_candle')

    add(initial,min(available,final),'before_available')
    expected=max(initial,available)
    actual=0;first=last=None
    for bar in rows:
        if not query.start_ms<=bar.time<query.end_ms: raise ValueError('Coverage row lies outside its requested range.')
        if last is not None and bar.time<=last: raise ValueError('Coverage input is not chronological.')
        actual+=1;first=bar.time if first is None else first;last=bar.time
        if bar.time>=final: raise ValueError('Unconfirmed candle entered historical coverage.')
        if floor_open(bar.time,timeframe)!=bar.time: raise ValueError('Unaligned candle entered historical coverage.')
        missing(expected,min(bar.time,final))
        expected=max(expected,next_open(bar.time,timeframe))
    missing(expected,final)
    add(max(initial,final),query.end_ms,'not_closed')
    return {'schema_version':'openpine.coverage-report.1','start_ms':query.start_ms,'end_ms':query.end_ms,
        'as_of':as_of,'first_available_ms':first_available,'bars':actual,'first_ms':first,'last_ms':last,
        'counts':counts,'intervals':intervals,'interval_count':interval_count,'truncated':interval_count>len(intervals),
        'confirmed_complete':counts['missing_candle']==counts['not_fetched']==0,
        'requested_complete':not any(counts.values()),'gap_policy':'no_fabricated_candles'}
