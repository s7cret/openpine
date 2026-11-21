"""Measure Parquet and FX access during historical market rotation."""
import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, fx
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.execution.resources import resident_bytes
from packages.execution.session import Session
from packages.storage import candles
from tools.optional_symbol_scale import OutputDigest
from tools.security_currency_scale import CountedList


def selector(names, start=0):
    if len(names) == 1:
        return f'"{names[0]}"'
    mid = len(names) // 2
    return (f'(slot < {start + mid} ? {selector(names[:mid], start)}'
            f' : {selector(names[mid:], start + mid)})')


def measure(session, manager, count, bindings, rate_id, mode):
    size = len(bindings) - 1
    names = [b.context.symbol for b in bindings[1:]]
    step = 1024 if mode == 'rare' else 1
    symbol = selector(names) if size > 1 else f'(slot == 0 ? "{names[0]}" : "{names[0]}")'
    if mode == 'static':
        symbol = f'"{names[0]}"'
    nested = mode == 'nested'
    expr = 'request.security("","",close[1])' if nested else 'close[1]'
    currency = '' if nested else ', currency="EUR"'
    source = (f'//@version=6\nindicator("Rotation")\nslot=int(bar_index/{step})%{size}\n'
              f'plot(request.security({symbol},"60",{expr}{currency}))')
    program = session.compile(source, 'scale.pine')
    sink, expected = OutputDigest(), OutputDigest()
    for i in range(count):
        last = (i + 1) // 60 - 2
        market = 0 if mode == 'static' else (i // step) % size
        value = None if last < 0 else (100 * (market + 1) + last % 31) * (1 if nested else .5)
        expected.emit(None, None, None, value, i, None)
    starts, rows, indexed = Counter(), Counter(), []
    parquet_queries = []
    connect = candles.connection
    @contextmanager
    def measured_connection(**options):
        with connect(**options) as db:
            class Reader:
                def execute(self, sql, *args):
                    if 'read_parquet' in sql:
                        parquet_queries.append(sql)
                    return db.execute(sql, *args)
            yield Reader()
    class MeasuredRates(fx.Rates):
        def __init__(self, data):
            super().__init__(data)
            for pair, (begin, end, timestamps, quotes) in self.pairs.items():
                times, values = CountedList(timestamps), CountedList(quotes)
                self.pairs[pair] = begin, end, times, values
                indexed.append((times, values))
    def reader(read, name):
        def run(index):
            starts[name] += 1
            for bar in read(index):
                rows[name] += 1
                yield bar
        return run
    rss = resident_bytes(os.getpid())
    def sample(*args):
        nonlocal rss
        rss = max(rss, resident_bytes(os.getpid()))
    begin = time.perf_counter()
    with ExitStack() as stack:
        opens = stack.enter_context(patch.object(manager.market, 'source', wraps=manager.market.source))
        gets = stack.enter_context(patch.object(manager.objects, 'get', wraps=manager.objects.get))
        stack.enter_context(patch.object(fx, 'Rates', MeasuredRates))
        stack.enter_context(patch.object(candles, 'connection', measured_connection))
        sources = [manager.market.source(b.snapshot_id, threads=1, memory_mb=16) for b in bindings]
        for b, src in zip(bindings, sources, strict=True):
            stack.enter_context(patch.object(src, 'read_from', reader(src.read_from, b.context.symbol)))
        driver = session.stream_driver(program, sources[0], sink, primary_context=bindings[0].context,
            context_sources=list(zip(bindings[1:], sources[1:], strict=True)),
            fx_rates=None if nested else manager.objects.get(rate_id))
        try:
            driver.run(progress=sample)
            sample()
            assert driver.count == count and sink.digest.digest() == expected.digest.digest()
        finally:
            driver.contexts.close()
        fx_opens = sum(c.args == (rate_id,) for c in gets.call_args_list)
        assert opens.call_count == len(bindings) and fx_opens == len(indexed) == int(not nested)
        assert starts == {b.context.symbol: 1 for b in bindings}
        assert len(parquet_queries) == sum(len(s.manifest['parts']) for s in sources)
        assert rows == {bindings[0].context.symbol: count, **{name: (count+59)//60 for name in names}}
        reads = sum(v.reads for _, v in indexed)
        assert reads == (0 if nested else size * max(0, count//60-1))
    return dict(mode=mode, primary_bars=count, markets=size, switch_every=step if mode!='static' else None,
                snapshot_opens=opens.call_count, parquet_queries=len(parquet_queries),
                reader_starts=dict(starts), rows_delivered=dict(rows),
                fx_snapshot_opens=fx_opens, fx_quote_reads=reads, fx_date_probes=sum(t.reads for t,_ in indexed),
                sampled_peak_rss_bytes=rss, seconds=round(time.perf_counter()-begin, 3),
                outputs_sha256=sink.digest.hexdigest(), python=platform.python_version(), platform=platform.platform())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--counts', nargs='+', type=int, default=[100_000, 1_000_000])
    parser.add_argument('--markets', nargs='+', type=int, choices=(1,8,32), default=[1,8,32])
    parser.add_argument('--modes', nargs='+', choices=('static','rare','frequent','nested'),
                        default=['static','rare','frequent','nested'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if any(n < 120 for n in args.counts):
        parser.error('At least two complete hourly candles are required.')
    reports = []
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    for count in args.counts:
        with TemporaryDirectory() as folder, local_manager(folder, autostart=False) as manager:
            bindings = []
            for i in range(max(args.markets)+1):
                minutes = 60 if i else 1
                symbol = f'TEST:M{i:02}' if i else 'TEST:CHART'
                ctx = MarketContext(symbol, symbol, str(minutes), 'fixture')
                meta = dict(context_id=symbol, symbol=symbol, timeframe=str(minutes), bar_schema='openpine.historical-bars.1')
                bars = (HistoricalBar(fx.DAY + j * minutes * 60000, float(100*i + j%31))
                        for j in range((count+minutes-1)//minutes))
                env = dict(schema_version='openpine.instrument-environment.1', instrument=dict(
                    basecurrency='TEST',currency='USD' if i else 'EUR',mintick='0.01',pointvalue='1',type='custom'))
                bindings.append(ContextBinding(ctx, manager.market.ingest(meta, bars, dict(provider='fixture',environment=env))))
            rates = [dict(day_ms=i*fx.DAY,available_ms=(i+1)*fx.DAY,rate=.5) for i in range((count+1439)//1440)]
            rate_id = manager.save_fx_rates(dict(schema_version='openpine.fx.1',source='scale',pairs=[
                dict(base='USD',quote='EUR',start_ms=fx.DAY,end_ms=fx.DAY+(count+60)*60000,rates=rates)]))
            for size in args.markets:
                for mode in args.modes:
                    if mode == 'static' and size != 1:
                        continue
                    row = measure(session,manager,count,bindings[:size+1],rate_id,mode)
                    reports.append(row)
                    print(json.dumps(row), flush=True)
                    if args.output:
                        args.output.write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
