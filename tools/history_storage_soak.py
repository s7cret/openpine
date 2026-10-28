"""All seven signals in three actual scheduled markets; restart and growth proof."""
import argparse,gc,json,math,time
from pathlib import Path
from types import SimpleNamespace
from fractions import Fraction
from packages.contracts import HistoricalBar,HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.storage import DatasetIdentity
from packages.contracts.wire import atomic_json,canonical,loads,digest
from packages.execution.session import Session
from packages.execution.identity import implementation_hash,build_identity

def source(bars,*,growth=True):
    length=f'bar_index<{bars//2} ? bar_index%4+1 : 4096' if growth else 'bar_index%4+1'
    text='//@version=6\nindicator("History storage")\nlength() => '+length+'\nsignals() =>\n    [ta.vwma(close,length()),ta.mom(close,length()),ta.roc(close,length()),ta.rising(close,length()) ? 1 : 0,ta.falling(close,length()) ? 1 : 0,ta.highestbars(close,length()),ta.lowestbars(close,length())]\n'
    for prefix,expression in [('p','signals()'),('a','request.security("TEST:A","1",signals())'),('b','request.security("TEST:B","1",signals())')]:
        text+='['+','.join(prefix+str(i) for i in range(7))+']='+expression+'\n'
        text+=''.join(f'plot({prefix}{i})\n' for i in range(7))
    return text

class Source:
    def __init__(self,context,bars,base):
        self.context,self.base=context,base
        self.identity=DatasetIdentity(digest([context.symbol,bars,base]),context.context_id,context.symbol,'1',bars,0,(bars-1)*60000)
    def read_from(self,index):
        for i in range(index,self.identity.count):
            x=float(self.base+i%32+1);yield HistoricalBar(i*60000,x,x,x,x,10.)
    def verify_cursor(self,index,last_time):
        if not 0<index<=self.identity.count or last_time!=(index-1)*60000:raise ValueError('Invalid generated cursor.')

def expected(i,base,n):
    def x(k):return base+k%32+1
    def prefix(k):
        q,r=divmod(k,32);return base*k+q*528+r*(r+1)//2
    current=x(i);previous=x(i-n) if i>=n else None
    mean=float(Fraction(prefix(i+1)-prefix(i+1-n),n)) if i+1>=n else None
    prior=[x(j) for j in range(max(0,i-min(n,32)),i)]
    rising=int(i>=n and current>max(prior)) if prior else 0
    falling=int(i>=n and current<min(prior)) if prior else 0
    # 32 is a full cycle: the latest extremum always belongs to this suffix.
    reached=[(x(j),j-i) for j in range(max(0,i+1-min(n,32)),i+1)]
    highest=max(reached,key=lambda v:(v[0],v[1]))[1]
    lowest=min(reached,key=lambda v:(v[0],-v[1]))[1]
    return (mean,float(current-previous) if previous is not None else None,
        float(Fraction((current-previous)*100,previous)) if previous is not None and previous else None,rising,falling,highest,lowest)

class Sink:
    def __init__(self,bars,growth):self.bars,self.growth=bars,growth;self.sites={};self.points=0
    def emit(self,site,title,typ,value,index,timestamp):
        column=self.sites.setdefault(site,len(self.sites));base=(0,100,200)[column//7]
        n=4096 if self.growth and index>=self.bars//2 else index%4+1
        wanted=expected(index,base,n)[column%7]
        if not (value is None if wanted is None else value is not None and math.isclose(value,wanted,rel_tol=1e-12,abs_tol=1e-12)):
            raise AssertionError((index,column,value,wanted))
        if timestamp!=index*60000:raise AssertionError('Timestamp differs.')
        self.points+=1

def peak_rss():
    import sys
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),*[(n,ctypes.c_size_t) for n in ('peak','working','pp','p','npp','np','page','peakpage')]]
        c=Counters();c.cb=ctypes.sizeof(c)
        ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
        query=ctypes.windll.psapi.GetProcessMemoryInfo;query.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        handle=ctypes.windll.kernel32.GetCurrentProcess()
        if not query(handle,ctypes.byref(c),c.cb):raise ctypes.WinError()
        return c.peak
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024

def run(directory,bars,*,growth=True):
    if type(bars) is not int or bars<8:raise ValueError('Use at least eight bars.')
    root=Path(directory);root.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(source(bars,growth=growth))
    contexts=[MarketContext(k,s,'1','generated.1') for k,s in [('primary','TEST:P'),('a','TEST:A'),('b','TEST:B')]]
    sources=[Source(c,bars,base) for c,base in zip(contexts,(0,100,200))];sink=Sink(bars,growth)
    def driver():return session.stream_driver(program,sources[0],sink,primary_context=contexts[0],context_sources=tuple((ContextBinding(c,s.identity.sha256),s) for c,s in zip(contexts[1:],sources[1:])))
    d=driver();cut=bars//2-1;d.run(pause_after=cut,checkpoint=lambda e:None);saved=canonical(d.checkpoint());points=sink.points;allocation=getattr(d.evaluation.budget,'allocation',None);account=allocation.account if allocation else None
    before_cells=d.evaluation.budget.cells;before_bytes=account.used_bytes if account else None
    del d;gc.collect()
    d=driver();d.restore(loads(saved));d.run()
    assert sink.points==bars*21 and sink.points>points
    final=canonical(d.checkpoint());allocation=getattr(d.evaluation.budget,'allocation',None);account=allocation.account if allocation else None
    value=dict(status='passed',schema_version='openpine.history-storage-soak.1',python=__import__('platform').python_version(),
        events=bars*3,primary_bars=bars,private_contexts=2,functions=7,verified_output_points=sink.points,
        resumed_from_bar=cut,checkpoint_bytes_before_growth=len(saved),checkpoint_bytes_final=len(final),
        primary_cells_before_growth=before_cells,primary_cells_final=d.evaluation.budget.cells,
        charged_bytes_before_growth=before_bytes,charged_bytes_final=account.used_bytes if account else None,
        peak_charged_bytes=account.peak_bytes if account else None,peak_resident_bytes=peak_rss(),seconds=round(time.monotonic()-start,3),
        abrupt_growth_to=4096 if growth else None,implementation_sha256=implementation_hash(),build=build_identity(),
        oracle='Independent cyclic integer sums, rational means/ROC and latest-tie lists; every output checked.')
    atomic_json(root/'report.json',value);return value

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=333334);p.add_argument('--no-growth',action='store_true');a=p.parse_args(argv)
    print(json.dumps(run(a.directory,a.bars,growth=not a.no_growth)));return 0
if __name__=='__main__':raise SystemExit(main())
