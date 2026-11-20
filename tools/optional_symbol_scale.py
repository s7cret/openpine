"""Measure frozen-market resolution, streaming, and checkpoint costs."""
import hashlib
import json
import struct
import os
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.contracts import optional_symbols
from packages.contracts.wire import canonical
from packages.execution.resources import resident_bytes
from packages.execution.session import Session
from tools.corporate_scale import Sink


class OutputDigest:
    def __init__(self):
        self.digest = hashlib.sha256()

    def emit(self, site, title, value_type, value, index, time):
        self.digest.update(b"N" if value is None else b"V" + struct.pack(">d", value))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lower',action='store_true',help='measure lower-timeframe arrays')
    parser.add_argument('--compare-prepared',action='store_true',help='compare all output values for literal and input targets')
    args = parser.parse_args()
    lower = args.lower
    primary_tf,secondary_tf = (2,1) if lower else (1,60)
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    catalog = {"schema_version":"openpine.symbol-catalog.1","source":"benchmark","unknown_symbols":["TEST:ABSENT"]}
    with TemporaryDirectory() as directory, local_manager(directory,autostart=False) as manager:
        for count in (100_000,1_000_000):
            bindings = []
            for symbol, tf in (("TEST:MAIN",primary_tf),("TEST:SECOND",secondary_tf)):
                context = MarketContext(symbol,symbol,str(tf),"fixture")
                provenance = {"provider":"fixture"}
                if symbol == "TEST:MAIN":
                    provenance["symbol_catalog"] = catalog
                metadata = {"symbol":symbol,"context_id":symbol,"timeframe":str(tf),"bar_schema":"openpine.historical-bars.1"}
                bars = (HistoricalBar(i*60000,10.) for i in range(0,count*primary_tf,tf))
                bindings.append(ContextBinding(context,manager.market.ingest(metadata,bars,provenance)))
            scenarios = [(["TEST:SECOND"],False),(["TEST:SECOND","TEST:ABSENT"],False),(["TEST:SECOND","TEST:SECOND"],False)]
            if args.compare_prepared:
                scenarios = [(["TEST:SECOND","TEST:ABSENT"], mode) for mode in (False,True)]
            baseline = None
            for symbols, prepared in scenarios:
                source = ['//@version=6','indicator("Resolution scale")']
                if prepared:
                    source.append(f'tf=input.timeframe("{secondary_tf}")')
                for i,symbol in enumerate(symbols):
                    target,tf = f'"{symbol}"', f'"{secondary_tf}"'
                    if prepared:
                        source.append(f's{i}=input.symbol({target})')
                        target,tf = f's{i}', 'tf'
                    if lower:
                        source.append(f'a{i}=request.security_lower_tf({target},{tf},close,ignore_invalid_symbol=true,calc_bars_count={count*2})')
                        source.append(f'plot(na(a{i}) ? -1 : array.size(a{i}))')
                    else:
                        source.append(f'plot(request.security({target},{tf},close,ignore_invalid_symbol=true))')
                program = session.compile(chr(10).join(source))
                peak = resident_bytes(os.getpid())
                def sample(done,total):
                    nonlocal peak
                    peak = max(peak,resident_bytes(os.getpid()))
                start = time.perf_counter()
                with patch.object(manager.market,"source",wraps=manager.market.source) as loads, patch.object(
                        optional_symbols,"catalog_symbols",wraps=optional_symbols.catalog_symbols) as catalogs:
                    primary,secondary = [manager.market.source(b.snapshot_id) for b in bindings]
                    sink = OutputDigest() if args.compare_prepared else Sink()
                    driver = session.stream_driver(program,primary,sink,primary_context=bindings[0].context,
                        context_sources=[(bindings[1],secondary)])
                    driver.run(progress=sample)
                    ck_start = time.perf_counter()
                    checkpoint = canonical(driver.checkpoint())
                    checkpoint_seconds = time.perf_counter()-ck_start
                    assert (loads.call_count,catalogs.call_count,driver.count) == (2,1,count)
                    comparison = {}
                    if args.compare_prepared:
                        observed = (sink.digest.hexdigest(), loads.call_count, catalogs.call_count, len(driver.contexts.groups))
                        if prepared:
                            assert observed == baseline
                        else:
                            baseline = observed
                        comparison = {"prepared":prepared,"lower":lower,"outputs_sha256":observed[0]}
                    print(json.dumps(dict(**comparison,bars=count,symbols=symbols,snapshot_loads=loads.call_count,
                        catalog_resolutions=catalogs.call_count,groups=len(driver.contexts.groups),
                        seconds=time.perf_counter()-start,checkpoint_seconds=checkpoint_seconds,
                        checkpoint_bytes=len(checkpoint),sampled_peak_rss_bytes=peak)),flush=True)
                    driver.contexts.close()


if __name__ == "__main__":
    main()
