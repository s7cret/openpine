"""Instrument real Parquet readers, object loads and the daily FX binary search."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, fx
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.execution.resources import resident_bytes
from packages.execution.session import Session
from tools.optional_symbol_scale import OutputDigest


class CountedList(list):
    def __init__(self, values):
        super().__init__(values)
        self.reads=0

    def __getitem__(self, index):
        self.reads+=1
        return super().__getitem__(index)


def run(session,manager,count,sites,bindings,fx_ref):
    expressions=("close","ta.ema(close,3)","close[1]")[:sites]
    lines=['//@version=6','indicator("FX scale")']+[
        f'plot(request.security("TEST:A","60",{expression},currency="EUR"))' for expression in expressions]
    program=session.compile(chr(10).join(lines))
    rows=Counter()
    starts=Counter()
    indexed=[]
    sink=OutputDigest()
    rss=resident_bytes(os.getpid())
    def sample(done,total):
        nonlocal rss
        rss=max(rss,resident_bytes(os.getpid()))
    def measured_reader(read,name):
        def measured(index):
            starts[name]+=1
            for bar in read(index):
                rows[name]+=1
                yield bar
        return measured
    class MeasuredRates(fx.Rates):
        def __init__(self,data):
            super().__init__(data)
            for pair,(start,end,times,values) in self.pairs.items():
                timestamps,quotes=CountedList(times),CountedList(values)
                self.pairs[pair]=(start,end,timestamps,quotes)
                indexed.append((timestamps,quotes))
    start=time.perf_counter()
    with ExitStack() as stack:
        opens=stack.enter_context(patch.object(manager.market,"source",wraps=manager.market.source))
        gets=stack.enter_context(patch.object(manager.objects,"get",wraps=manager.objects.get))
        stack.enter_context(patch.object(fx,"Rates",MeasuredRates))
        primary,secondary=(manager.market.source(b.snapshot_id) for b in bindings)
        for source,name in ((primary,"primary"),(secondary,"secondary")):
            stack.enter_context(patch.object(source,"read_from",measured_reader(source.read_from,name)))
        driver=session.stream_driver(program,primary,sink,primary_context=bindings[0].context,
            context_sources=[(bindings[1],secondary)],fx_rates=manager.objects.get(fx_ref))
        try:
            driver.run(progress=sample)
            sample(count,count)
            fx_opens=sum(call.args==(fx_ref,) for call in gets.call_args_list)
            assert (opens.call_count,fx_opens,len(indexed),len(driver.contexts.groups),driver.count)==(2,1,1,1,count)
            times,values=indexed[0]
            assert starts=={"primary":1,"secondary":1}
            assert rows=={"primary":count,"secondary":math.ceil(count/60)}
            closed=count//60
            assert values.reads==closed*sites-int(sites==3)
            assert times.reads<=values.reads*len(times).bit_length()
        finally:
            driver.contexts.close()
    return dict(primary_bars=count,sites=sites,reader_starts=dict(starts),rows_read=dict(rows),
        market_snapshot_opens=opens.call_count,fx_snapshot_opens=fx_opens,fx_rows_indexed=len(times),
        fx_date_probes=times.reads,fx_value_reads=values.reads,seconds=round(time.perf_counter()-start,3),
        sampled_peak_rss_bytes=rss,output_sha256=sink.digest.hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts",type=int,nargs="+",default=[100_000,1_000_000])
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    session=Session(profile_id=HISTORICAL_PROFILE_ID)
    reports=[]
    for count in args.counts:
        with TemporaryDirectory() as directory,local_manager(directory,autostart=False) as manager:
            bindings=[]
            for symbol,minutes,currency in (("TEST:B",1,"EUR"),("TEST:A",60,"USD")):
                ctx=MarketContext(symbol,symbol,str(minutes),"fixture")
                metadata=dict(symbol=symbol,context_id=symbol,timeframe=str(minutes),bar_schema="openpine.historical-bars.1")
                bars=(HistoricalBar(fx.DAY+i*60_000,100.+(i%30)) for i in range(0,count,minutes))
                provenance=dict(provider="fixture",environment=dict(schema_version="openpine.instrument-environment.1",
                    instrument=dict(basecurrency="TEST",currency=currency,mintick="0.01",pointvalue="1",type="custom")))
                bindings.append(ContextBinding(ctx,manager.market.ingest(metadata,bars,provenance)))
            rates=[dict(day_ms=i*fx.DAY,available_ms=(i+1)*fx.DAY,rate=.7+.01*(i%5)) for i in range(math.ceil(count/1440))]
            ref=manager.save_fx_rates(dict(schema_version="openpine.fx.1",source="scale",pairs=[
                dict(base="USD",quote="EUR",start_ms=fx.DAY,end_ms=fx.DAY+(count+60)*60_000,rates=rates)]))
            for sites in (1,3):
                row=run(session,manager,count,sites,bindings,ref)
                reports.append(row)
                print(json.dumps(row),flush=True)
                if args.output:args.output.write_text(json.dumps(reports,indent=2),encoding="utf-8")


if __name__=="__main__":
    main()
