"""Page streaming. Successful pages have explicit, confirmed coverage boundaries."""

from dataclasses import asdict, dataclass

from packages.contracts.market import HistoryRequest
from packages.contracts.model import HistoricalBar, HistoricalData
from packages.contracts.wire import digest
from packages.marketdata import validate_data
from .timeframes import INTERVALS, check, floor_open, next_open
from .transport import DEFAULT_TRANSPORT, RequestControl


@dataclass(frozen=True)
class HistoryPage:
    start_ms: int
    stop_ms: int
    bars: tuple
    sha256: str


def normalize_rows(rows, request, start, stop, as_of, limit):
    if type(rows) is not list or len(rows)>limit: raise ValueError('Invalid bounded kline page.')
    result, previous = [], start-1
    for row in rows:
        if (type(row) is not list or len(row)!=12 or type(row[0]) is not int or type(row[6]) is not int
                or not start<=row[0]<stop or row[0]<=previous
                or floor_open(row[0],request.timeframe)!=row[0]
                or next_open(row[0],request.timeframe)-1 != row[6]
                or any(type(row[i]) is not str or len(row[i])>64 for i in range(1,6))):
            raise ValueError('Malformed, unaligned, duplicate or out-of-range kline page.')
        previous=row[0]
        if row[6]>=as_of: continue
        values=[float(row[i]) for i in range(1,6)]
        result.append(HistoricalBar(row[0],values[3],values[0],values[1],values[2],values[4]))
    if result:
        if any(b.low<=0 for b in result):raise ValueError('Spot candles require positive prices.')
        validate_data(HistoricalData('binance.spot.'+request.instrument.symbol+'.'+request.timeframe,
            request.instrument.pine_symbol,request.timeframe,tuple(result),schema_version='openpine.historical-bars.2'))
    return tuple(result)


class BinanceHistory:
    def __init__(self, transport=None, *, page_limit=1000):
        if type(page_limit) is not int or not 1<=page_limit<=1000: raise ValueError('Invalid page limit.')
        self.transport,self.page_limit=transport or DEFAULT_TRANSPORT,page_limit

    def server_time(self, control):
        raw=self.transport('/api/v3/time',{},control=control)
        value=raw.get('serverTime') if type(raw) is dict else None
        if type(value) is not int or not 0<=value<253402214400000: raise ValueError('Invalid exchange server time.')
        return value

    def first_available(self, instrument, as_of, control):
        request=HistoryRequest(instrument,'1',0,max(1,as_of))
        raw=self.transport('/api/v3/klines',{'symbol':instrument.symbol,'interval':'1m','startTime':0,
            'endTime':as_of-1,'timeZone':'0','limit':1},control=control)
        rows=normalize_rows(raw,request,0,as_of,as_of,1)
        return rows[0].time if rows else None

    def pages(self, request, *, start=None, stop=None, as_of=None, control=None):
        if type(request) is not HistoryRequest: raise ValueError('Invalid history request.')
        check(request.timeframe)
        control=control or RequestControl()
        as_of=self.server_time(control) if as_of is None else as_of
        if request.end_ms>as_of: raise ValueError('Historical range extends beyond exchange server time.')
        cursor=request.start_ms if start is None else start
        stop=min(request.end_ms if stop is None else stop,floor_open(as_of,request.timeframe))
        while cursor<stop:
            control.check()
            raw=self.transport('/api/v3/klines',{'symbol':request.instrument.symbol,'interval':INTERVALS[request.timeframe],
                'startTime':cursor,'endTime':stop-1,'timeZone':'0','limit':self.page_limit},control=control)
            bars=normalize_rows(raw,request,cursor,stop,as_of,self.page_limit)
            end=raw[-1][0]+1 if len(raw)==self.page_limit else stop
            if not cursor<end<=stop: raise ValueError('Pagination did not advance.')
            yield HistoryPage(cursor,end,bars,digest(raw))
            cursor=end


def metadata(request):
    return {'context_id':'binance.spot.'+request.instrument.symbol+'.'+request.timeframe,
        'symbol':request.instrument.pine_symbol,'timeframe':request.timeframe,'bar_schema':'openpine.historical-bars.2'}
