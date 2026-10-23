"""Stream large-offset statistics against an independent Decimal window oracle."""
import argparse
from collections import deque
from decimal import Decimal,localcontext
import hashlib
import math
from pathlib import Path
import struct
import sys
import time
from packages.contracts import HISTORICAL_PROFILE_ID,BarFrame
from packages.contracts.wire import atomic_json
from packages.execution.session import Session
from tools.technical_growth import resident

SOURCE='''//@version=6
indicator("Composite numeric growth")
[m,u,l]=ta.bb(close,127,2)
plot(m,title="mean")
plot(u,title="upper")
plot(l,title="lower")
plot(ta.variance(close,127),title="variance")
plot(ta.stdev(close,127),title="stdev")
'''


class Sink:
    def __init__(self):self.expected={};self.rows=0;self.maximum_statistic_error=0.;self.sha=hashlib.sha256()
    def emit(self,site,title,typ,value,index,timestamp):
        expected=self.expected[title]
        if expected is None:
            if value is not None:raise AssertionError((index,title,value,expected))
        else:
            tolerance=1e-12 if title in ('variance','stdev') else math.ulp(expected)
            if value is None or abs(value-expected)>tolerance:raise AssertionError((index,title,value,expected,tolerance))
            if title in ('variance','stdev'):self.maximum_statistic_error=max(self.maximum_statistic_error,abs(value-expected))
        self.sha.update(title.encode()+ (b'N' if value is None else b'F'+struct.pack('!d',value)));self.rows+=1


def run(directory,bars=1000000):
    directory=Path(directory).resolve()
    if directory.exists():raise ValueError('Use a new composite growth directory.')
    if type(bars) is not int or not 100000<=bars<=10000000:raise ValueError('Use 100000..10000000 bars.')
    directory.mkdir(parents=True);session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(SOURCE,'composite-growth.pine')
    sink=Sink();evaluation=session.validate_program(program).prepare(program,output_sink=sink)
    initial=resident();samples=[];started=time.perf_counter();window=deque();total=Decimal(0);squares=Decimal(0)
    with localcontext() as context:
        context.prec=100
        for index in range(bars):
            value=None if index%113==0 else 1e15+(index%97)*.125
            if value is not None:
                current=Decimal.from_float(value);window.append(current);total+=current;squares+=current*current
                if len(window)>127:old=window.popleft();total-=old;squares-=old*old
            if len(window)<127:sink.expected=dict.fromkeys(('mean','upper','lower','variance','stdev'))
            else:
                mean=total/127;variance=squares/127-mean*mean;deviation=variance.sqrt()
                sink.expected={'mean':float(mean),'upper':float(mean+2*deviation),'lower':float(mean-2*deviation),
                    'variance':float(variance),'stdev':float(deviation)}
            evaluation.step(BarFrame('composite.growth',index,index*60000,value,value,value,value,1.))
            if (index+1)%100000==0:
                sample={'bars':index+1,'cells':evaluation.budget.cells,'seconds':round(time.perf_counter()-started,3),**resident()}
                samples.append(sample);print(sample,flush=True)
    if sink.rows!=bars*5 or evaluation.budget.cells!=381 or evaluation.times or any(v for _,_,v in evaluation.outputs.values()):raise AssertionError('Streaming output or memory bound differs.')
    if samples[-1]['peak_resident_bytes']-initial['peak_resident_bytes']>64*1024*1024:raise AssertionError('Resident growth exceeded 64 MiB.')
    receipt={'schema_version':'openpine.composite-growth.1','status':'passed','python':sys.version.split()[0],
        'implementation_sha256':program.identity.implementation_sha256,'bars':bars,'outputs_checked':sink.rows,
        'oracle':'independent_decimal_100_digits_rolling_moments_every_value','source_magnitude':1e15,'source_step':.125,
        'statistic_absolute_tolerance':1e-12,'bb_absolute_tolerance':'one_output_ulp',
        'maximum_statistic_error':sink.maximum_statistic_error,'output_sha256':sink.sha.hexdigest(),
        'budget_cells':evaluation.budget.cells,'streaming_outputs':True,'initial_memory':initial,'samples':samples,
        'seconds':round(time.perf_counter()-started,3)}
    atomic_json(directory/'evidence.json',receipt);return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--bars',type=int,default=1000000);args=parser.parse_args();print(run(args.directory,args.bars))


if __name__=='__main__':main()
