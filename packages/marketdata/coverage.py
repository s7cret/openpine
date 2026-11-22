"""Coverage of confirmed candles is distinct from coverage of fetched intervals."""

from packages.contracts.calendar import floor_open, next_open
from packages.contracts.market_schedule import MarketSchedule
from packages.contracts.wire import digest


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


def coverage_report(query, rows, *, as_of, first_available=None, unfetched=(), max_intervals=200, market_schedule=None):
    timeframe=query.timeframe
    initial=first_open(query.start_ms,timeframe)
    final=min(query.end_ms,floor_open(as_of,timeframe))
    available=floor_open(first_available,timeframe) if first_available is not None else initial
    intervals=[];counts={'before_available':0,'not_closed':0,'not_fetched':0,'missing_candle':0}
    interval_count=0
    last_reason=last_end=None
    schedule=None
    if market_schedule is not None:
        schedule=MarketSchedule.from_payload(market_schedule,timeframe)
        if not schedule.start_ms<=query.start_ms<query.end_ms<=schedule.end_ms:
            raise ValueError('Requested coverage is outside the pinned market schedule.')
        counts['market_closed']=0

    def record(start,stop,reason):
        nonlocal interval_count,last_reason,last_end
        if start>=stop: return
        counts[reason]+=count_slots(start,stop,timeframe)
        if last_end==start and last_reason==reason:
            last_end=stop
            if interval_count==len(intervals):
                intervals[-1]['end_ms']=stop
            return
        last_reason,last_end=reason,stop
        interval_count+=1
        if len(intervals)<max_intervals: intervals.append({'start_ms':start,'end_ms':stop,'reason':reason})

    def add(start,stop,reason):
        if start>=stop:return
        if schedule is None:
            record(start,stop,reason)
            return
        for a,b,is_open in schedule.segments(start,stop):
            record(a,b,reason if is_open else 'market_closed')

    def missing(start,stop):
        cursor=start
        for a,b in unfetched:
            if b<=cursor or a>=stop: continue
            # Fetch ranges are half-open timestamps. Classify each candle by
            # its opening time, even when a fetch stops inside that candle.
            first=first_open(a,timeframe) if a>cursor else cursor
            end=min(stop,first_open(b,timeframe)) if b<stop else stop
            add(cursor,min(first,stop),'missing_candle')
            add(first,end,'not_fetched')
            cursor=end
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
        if schedule is not None:schedule.require_open(bar.time)
        missing(expected,min(bar.time,final))
        expected=max(expected,next_open(bar.time,timeframe))
    missing(expected,final)
    add(max(initial,final),query.end_ms,'not_closed')
    return {'schema_version':'openpine.coverage-report.2' if schedule else 'openpine.coverage-report.1',
        **({'market_schedule_sha256':digest(market_schedule)} if schedule else {}),
        'start_ms':query.start_ms,'end_ms':query.end_ms,
        'as_of':as_of,'first_available_ms':first_available,'bars':actual,'first_ms':first,'last_ms':last,
        'counts':counts,'intervals':intervals,'interval_count':interval_count,'truncated':interval_count>len(intervals),
        'confirmed_complete':counts['missing_candle']==counts['not_fetched']==0,
        'requested_complete':not any(v for k,v in counts.items() if k!='market_closed'),'gap_policy':'no_fabricated_candles'}
