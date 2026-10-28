"""All-version long rollback/varip/private-context memory plateau proof."""
import argparse,gc,json,math,sys,time
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.intrabar import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import atomic_json,canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from packages.execution.identity import implementation_hash,build_identity
from packages.execution.resources import resident_bytes
from tools.history_storage_soak import peak_rss

def source(version):
    prefix='ta.' if version>=5 else '';api='request.security' if version>=5 else 'security';decl='indicator' if version>=5 else 'study'
    text=f'//@version={version}\n{decl}("Rollback soak")\nplot({prefix}sma(close,4))\nplot(close[1])\nplot({prefix}change(close,3))\nx=0.0\nif bar_index%2==0\n    x:={prefix}ema(close,7)\nplot(x)\n'
    text+=f'plot({api}("TEST:A","1",{prefix}sma(close,4)))\nplot({api}("TEST:B","1",{prefix}sma(close,4)))\n'
    if version>=4:
        text+='varip ticks=0\nvarip a=array.new_float(0)\nif barstate.isnew\n    ticks:=0\n    array.clear(a)\nticks:=ticks+1\narray.push(a,close)\nif array.size(a)>8\n    array.shift(a)\nplot(ticks)\nplot(array.size(a))\n'
    return text

class Sink:
    def __init__(self,version,updates):self.version,self.updates=version,updates;self.rows=0;self.sites={};self.last={};self.seed=[];self.ema=None
    def emit(self,site,title,typ,value,index,timestamp):
        column=self.sites.setdefault(site,len(self.sites));wanted=None
        if column==0:wanted=float(sum(j%32+1 for j in range(index-3,index+1))/4) if index>=3 else None
        elif column==1:wanted=float((index-1)%32+1) if index else None
        elif column==2:wanted=float(index%32-(index-3)%32) if index>=3 else None
        elif column==3:
            wanted=0.
            if index%2==0:
                x=float(index%32+1)
                if self.ema is None:
                    if self.version<5:
                        self.seed.append(x)
                        if len(self.seed)==7:self.ema=math.fsum(v/7 for v in self.seed)
                    else:self.ema=x
                else:self.ema=.25*x+.75*self.ema
                wanted=self.ema
        elif column in (4,5):wanted=float((100 if column==4 else 200)+sum(j%32+1 for j in range(index-3,index+1))/4) if index>=3 else None
        elif column==6:wanted=self.updates+2
        elif column==7:wanted=min(8,self.updates+2)
        if value!=wanted:raise AssertionError((self.version,index,column,value,wanted))
        if timestamp!=index*60000:raise AssertionError('Realtime commit timestamp differs.')
        self.rows+=1;self.last[site]=value

def run(directory,bars=256,updates=64):
    if sys.version_info[:3]!=(3,14,8) or bars<32 or not 1<=updates<=10000:raise ValueError('Use Python 3.14.8, >=32 bars, 1..10000 updates.')
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False);start=time.monotonic();results=[]
    contexts=tuple(MarketContext(k,s,'1','soak.1') for k,s in [('primary','TEST:P'),('a','TEST:A'),('b','TEST:B')])
    for version in range(1,7):
        session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(source(version),'rollback-soak.pine');sink=Sink(version,updates)
        def engine():return ReplayEngine(session,program,contexts,'primary',sink,calculation_profile=PROFILE)
        replay=engine();sequence=0;samples=[];counts=[];rss_samples=[]
        for index in range(bars):
            for revision in range(updates+2):
                kind='open' if revision==0 else 'close' if revision==updates+1 else 'update'
                received=index*60000+(60000 if kind=='close' else revision)
                for context,base in [(contexts[1],100),(contexts[2],200),(contexts[0],0)]:
                    op=float(base+index%32+1);price=op if kind=='close' else op+revision%3*.25
                    before=replay.evaluation.count
                    high=op+(0.5 if revision>=2 else 0.25 if revision==1 else 0.)
                    event=MarketEvent(sequence,context.context_id,kind,index*60000,received,revision,op,high,op,price,float(revision))
                    replay.apply(event);sequence+=1
                    if replay.evaluation.count!=before+(context.context_id=='primary' and kind=='close'):raise AssertionError('Preview committed history.')
                    if index==bars//2 and context.context_id=='primary' and revision==updates//2:
                        saved=loads(canonical(replay.checkpoint()),max_bytes=16*1024**2)
                        del replay;gc.collect();replay=engine();replay.restore(saved);del saved
            if index>=bars//4:
                gc.collect();memory=replay.evaluation.memory_status()['ledger'];samples.append(memory['charged_bytes']);counts.append(memory['peak_charged_bytes'])
                if index>=bars*3//4:rss_samples.append(resident_bytes(__import__('os').getpid()))
        # All retained windows and bounded arrays have reached capacity before
        # sampling. A small fixed metadata allowance covers counter encodings.
        drift=max(samples)-min(samples)
        if drift>65536:raise AssertionError(('Retained memory grew after warmup.',version,drift))
        rss_drift=max(rss_samples)-min(rss_samples)
        if rss_drift>16*1024**2 or peak_rss()>512*1024**2:raise AssertionError(('Realtime RSS did not plateau inside its budget.',version,rss_drift))
        if sink.rows!=bars*(8 if version>=4 else 6):raise AssertionError('Realtime output count differs.')
        row=dict(version=version,events=sequence,confirmed_bars=replay.evaluation.count,output_values=sink.rows,
            retained_min=min(samples),retained_max=max(samples),retained_drift=drift,peak_charged_bytes=max(counts),rss_plateau_drift=rss_drift,
            varip_and_arrays=version>=4,restore_inside_open_bar=True,sampled_resident_bytes=resident_bytes(__import__('os').getpid()))
        results.append(row);atomic_json(root/f'v{version}.json',row)
        del replay,session,program;gc.collect()
    report=dict(status='passed',python=sys.version.split()[0],bars=bars,updates_per_bar=updates,versions=results,
        events=sum(r['events'] for r in results),peak_resident_bytes=peak_rss(),seconds=round(time.monotonic()-start,3),
        implementation_sha256=implementation_hash(),build=build_identity())
    atomic_json(root/'report.json',report);return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=256);p.add_argument('--updates',type=int,default=64);a=p.parse_args();print(json.dumps(run(a.directory,a.bars,a.updates)))
if __name__=='__main__':main()
