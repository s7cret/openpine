"""Million-bar TA streaming qualification against an independent Decimal oracle."""

import argparse
from collections import deque
import ctypes
from decimal import Decimal, localcontext
import hashlib
import math
import os
from pathlib import Path
import struct
import sys
import time

from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID
from packages.contracts.wire import atomic_json
from packages.execution.session import Session


SOURCE = '''//@version=6
indicator("Technical growth")
plot(ta.rma(close,17),"rma")
plot(ta.atr(17),"atr")
plot(ta.rsi(close,17),"rsi")
plot(ta.highest(high,17),"highest")
plot(ta.lowest(low,17),"lowest")
plot(ta.tr(true),"tr")
'''


def resident():
    if os.name == 'nt':
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
        value = Memory(); value.cb = ctypes.sizeof(value)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        if not query(process(), ctypes.byref(value), value.cb):
            raise ctypes.WinError()
        return {'resident_bytes': value.WorkingSetSize, 'peak_resident_bytes': value.PeakWorkingSetSize}
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'resident_bytes': None, 'peak_resident_bytes': peak if sys.platform == 'darwin' else peak * 1024}


class Mean:
    def __init__(self):
        self.seed, self.value = [], None

    def feed(self, value):
        if value is not None:
            value = Decimal(value)
            if self.value is None:
                self.seed.append(value)
                if len(self.seed) == 17:
                    self.value = sum(self.seed) / 17
                    self.seed.clear()
            else:
                self.value = (16 * self.value + value) / 17
        return self.value


class Oracle:
    def __init__(self):
        self.rma, self.atr, self.gain, self.loss = Mean(), Mean(), Mean(), Mean()
        self.highs, self.lows = deque(maxlen=17), deque(maxlen=17)
        self.physical_close, self.defined_close = None, None

    def step(self, close, high, low):
        previous = self.physical_close
        tr = high-low if previous is None else max(high-low, abs(high-previous), abs(low-previous))
        rma, atr = self.rma.feed(close), self.atr.feed(tr)
        if close is not None:
            if self.defined_close is not None:
                delta = close-self.defined_close
                self.gain.feed(max(delta, 0.)); self.loss.feed(max(-delta, 0.))
            self.defined_close = close
        gain, loss = self.gain.value, self.loss.value
        rsi = None if gain is None or loss is None or gain == loss == 0 else (
            Decimal(100) if loss == 0 else Decimal(100)-Decimal(100)/(1+gain/loss))
        self.highs.append(high); self.lows.append(low); self.physical_close = close
        return dict(rma=rma, atr=atr, rsi=rsi, highest=max(self.highs), lowest=min(self.lows), tr=tr)


class Sink:
    def __init__(self):
        self.expected, self.last = {}, {}
        self.rows, self.maximum_error = 0, 0.
        self.sha = hashlib.sha256()

    def emit(self, site, title, value_type, value, index, timestamp):
        expected = self.expected[title]
        if expected is None:
            if value is not None: raise AssertionError((index, title, value, expected))
        elif value is None or not math.isclose(value, float(expected), rel_tol=2e-12, abs_tol=2e-12):
            raise AssertionError((index, title, value, str(expected)))
        if value is not None:
            self.maximum_error = max(self.maximum_error, abs(value-float(expected)))
        self.sha.update(title.encode('ascii') + (b'N' if value is None else b'F'+struct.pack('!d', value)))
        self.rows += 1; self.last[title] = value


def run(directory, bars=1000000):
    root = Path(directory).resolve()
    if root.exists(): raise ValueError('Use a new technical growth directory.')
    if type(bars) is not int or not 100000 <= bars <= 10000000:
        raise ValueError('Growth qualification requires 100000..10000000 bars.')
    root.mkdir(parents=True)
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    program = session.compile(SOURCE, 'technical-growth.pine')
    sink, oracle = Sink(), Oracle()
    evaluation = session.validate_program(program).prepare(program, output_sink=sink)
    initial = resident(); samples = []
    started = time.perf_counter()
    with localcontext() as context:
        context.prec = 40
        for index in range(bars):
            price = float(100 + index % 97)
            close = None if index % 113 == 0 else price
            sink.expected = oracle.step(close, price+2., price-2.)
            evaluation.step(BarFrame('technical.growth', index, index*60000, close, price, price+2., price-2., 1.))
            if (index+1) % 100000 == 0 or index == bars-1:
                sample = {'bars': index+1, 'cells': evaluation.budget.cells,
                    'seconds': round(time.perf_counter()-started, 3), **resident()}
                samples.append(sample)
                print(sample, flush=True)
    if sink.rows != bars * 6 or evaluation.budget.cells != 100069:
        raise AssertionError('Unexpected output cardinality or storage growth.')
    if evaluation.times or any(values for _, _, values in evaluation.outputs.values()):
        raise AssertionError('Streaming evaluation retained the complete output series.')
    if samples[-1]['peak_resident_bytes']-initial['peak_resident_bytes'] > 64*1024*1024:
        raise AssertionError('Technical streaming resident growth exceeded 64 MiB.')
    receipt = {'schema_version': 'openpine.technical-growth.1', 'status': 'passed',
        'python': '.'.join(map(str, sys.version_info[:3])), 'bars': bars,
        'outputs_checked': sink.rows, 'oracle': 'independent_decimal_40_digits_every_value',
        'maximum_absolute_error': sink.maximum_error, 'output_sha256': sink.sha.hexdigest(),
        'final_outputs': sink.last, 'initial_memory': initial, 'samples': samples,
        'streaming_outputs': True, 'budget_cells': evaluation.budget.cells,
        'seconds': round(time.perf_counter()-started, 3), 'implementation_sha256': program.identity.implementation_sha256}
    atomic_json(root/'evidence.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--bars', type=int, default=1000000)
    args = parser.parse_args()
    print(run(args.directory, args.bars))


if __name__ == '__main__': main()
