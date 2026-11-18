"""Measure sparse dividend requests on streaming historical bars on Python 3.14.8."""
import argparse
import json
import os
import time
from types import SimpleNamespace

from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar
from packages.contracts.wire import canonical, read_json
from packages.execution.driver import HistoricalDriver
from packages.execution.resources import resident_bytes
from packages.execution.session import Session
from tools.specification import ROOT


class Bars:
    def __init__(self,count):
        self.identity = SimpleNamespace(count=count,context_id='bench',symbol='TEST:B',timeframe='1')
        self.reads = 0

    def read_from(self,start):
        self.reads += 1
        for i in range(start,self.identity.count):
            yield HistoricalBar(i*60000,100.,100.,100.,100.,1.)


class Sink:
    def emit(self,site,title,value_type,value,index,time):
        self.last = value


def benchmark_earnings(session):
    program = session.compile('//@version=6\nindicator("EPS scale")\nplot(request.earnings("TEST:B"))')
    runtime = session.validate_program(program)
    for count, publications in ((100_000, 8), (100_000, 8_000), (1_000_000, 8_000)):
        bars, sink = Bars(count), Sink()
        started = time.perf_counter()
        data = read_json(ROOT/'conformance/earnings/events.json')
        stream = next(item for item in data['streams'] if item['family'] == 'earnings')
        data['streams'] = [stream]
        stream['end_ms'] = count * 60000
        stream['events'] = [dict(period_end_ms=i*60000, available_ms=i*(count//publications)*60000,
                                 field='actual', value=float(i)) for i in range(publications)]
        driver = HistoricalDriver(runtime.prepare(program, output_sink=sink), bars, corporate_actions=data)
        peak = resident_bytes(os.getpid())
        def sample(done, total):
            nonlocal peak
            peak = max(peak, resident_bytes(os.getpid()))
        driver.run(progress=sample)
        assert sink.last == publications-1 and driver.count == count and bars.reads == 1
        print(json.dumps(dict(bars=count, publications=publications, seconds=round(time.perf_counter()-started,3),
                              sampled_peak_rss_bytes=peak, checkpoint_json_bytes=len(canonical(driver.checkpoint())),
                              candle_passes=bars.reads, corporate_file_reads=1)), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--earnings', action='store_true')
    args = parser.parse_args()
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    if args.earnings:
        return benchmark_earnings(session)
    program = session.compile('//@version=6\nindicator("Scale")\nplot(request.dividends("TEST:B"))')
    runtime = session.validate_program(program)
    for count in (100_000,1_000_000):
        bars, sink = Bars(count), Sink()
        data = read_json(ROOT/'conformance/corporate-actions/events.json')
        for stream in data['streams']:
            stream['end_ms'] = count*60000
        started = time.perf_counter()
        driver = HistoricalDriver(runtime.prepare(program,output_sink=sink),bars,corporate_actions=data)
        peak = resident_bytes(os.getpid())
        def sample(done,total):
            nonlocal peak
            peak = max(peak,resident_bytes(os.getpid()))
        driver.run(progress=sample)
        assert sink.last == 3. and driver.count == count and bars.reads == 1
        print(json.dumps(dict(bars=count,seconds=round(time.perf_counter()-started,3),
                              sampled_peak_rss_bytes=peak,checkpoint_json_bytes=len(canonical(driver.checkpoint())),
                              candle_passes=bars.reads,corporate_file_reads=1)),flush=True)


if __name__ == '__main__':
    main()
