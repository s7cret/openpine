"""Measure rows delivered by the Parquet secondary reader for two EMA windows."""
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.execution.session import Session
from tools.optional_symbol_scale import OutputDigest


def main():
    root=Path(__file__).resolve().parents[1]
    session=Session(profile_id=HISTORICAL_PROFILE_ID)
    program=session.compile((root/"examples/security-count/indicator.pine").read_text())
    for count in (100_000,1_000_000):
        with TemporaryDirectory() as location, local_manager(location,autostart=False) as manager:
            bindings=[]
            for symbol,minutes in (("TEST:MAIN",1),("TEST:SECOND",2)):
                ctx=MarketContext(symbol,symbol,str(minutes),"fixture")
                meta={"symbol":symbol,"context_id":symbol,"timeframe":str(minutes),"bar_schema":"openpine.historical-bars.1"}
                bars=(HistoricalBar(i*60000,float(i+1)) for i in range(0,count,minutes))
                bindings.append(ContextBinding(ctx,manager.market.ingest(meta,bars,{"provider":"fixture"})))
            parent,child=(manager.market.source(b.snapshot_id) for b in bindings)
            rows_read=0
            offsets=[]
            read_from=child.read_from
            def measured(index):
                nonlocal rows_read
                offsets.append(index)
                for bar in read_from(index):
                    rows_read+=1
                    yield bar
            sink=OutputDigest()
            started=time.perf_counter()
            with patch.object(child,"read_from",measured):
                driver=session.stream_driver(program,parent,sink,primary_context=bindings[0].context,
                                             context_sources=[(bindings[1],child)])
                try:
                    driver.run()
                    groups=len(driver.contexts.groups)
                    assert driver.count==count and groups==1
                    assert offsets==[child.identity.count-4] and rows_read==4
                finally:
                    driver.contexts.close()
            print(json.dumps(dict(primary_bars=count,secondary_bars=child.identity.count,secondary_rows_read=rows_read,
                secondary_read_offsets=offsets,groups=groups,outputs_sha256=sink.digest.hexdigest(),
                seconds=time.perf_counter()-started)),flush=True)


if __name__=="__main__":
    main()
