"""Historical execution at explicit model points with recoverable parent cursors."""
import copy
from dataclasses import asdict
from packages.contracts import BarFrame,Diagnostic,OpenPineError
from packages.contracts.magnifier import PROFILE,policy
from packages.contracts.realtime import MarketEvent
from packages.contracts.calendar import next_open
from packages.contracts.wire import digest,read_json
from .identity import ROOT
from .magnifier_feed import Feed,path
from .observed_trading import ObservedTrading
from .trading import TradingExecution

class MagnifierTrading(ObservedTrading):
    def provenance(self):
        return {**self.model_point,'model':'lower_candle_four_points','simulation_only':True}

    def summary(self):
        return {**TradingExecution.summary(self),'bar_magnifier':self.magnifier.summary()}

def events(parent,rows,fallback,context_id,start_sequence,child_tf,parent_tf):
    high=low=opening=None;volume=0.;revision=0
    for child in rows:
        duration=next_open(child.time,parent_tf if fallback else child_tf)-child.time
        for point,price in enumerate(path(child)):
            if opening is None:opening=high=low=price
            high=max(high,price);low=min(low,price)
            if point==3:volume+=child.volume
            at=child.time+(0,duration//3,2*duration//3,duration)[point]
            final=child is rows[-1] and point==3
            event=MarketEvent(start_sequence+revision,context_id,'close' if final else 'open' if revision==0 else 'update',
                parent.time,at,revision,opening,high,low,price,parent.volume if final else volume)
            yield event,{'parent_time':parent.time,'child_time':child.time,'child_timeframe':parent_tf if fallback else child_tf,
                'point':point,'price':price,'available_at':at,'fallback':fallback,'sequence':event.sequence}
            revision+=1

class Magnifier:
    def __init__(self,driver,lower,configuration):
        self.driver,self.lower,self.configuration=driver,lower,copy.deepcopy(configuration)
        pinned=read_json(ROOT/'spec/bar-magnifier-profile.json')
        if (pinned['source_lock_sha256']!=digest(read_json(ROOT/'spec/lock.json'))
                or pinned['strategy_cycle_sha256']!=digest(read_json(ROOT/'spec/strategy-cycle-profile.json'))
                or pinned['versions'][str(driver.evaluation.program.identity.language_version)]['policy']!=policy(driver.evaluation.program.identity.language_version)):
            raise ValueError('Magnifier profile differs from pinned evidence.')
        if getattr(lower,'manifest',{}).get('snapshot_id',configuration['snapshot_id'])!=configuration['snapshot_id']:raise ValueError('Lower source differs from pinned snapshot.')
        from .magnifier_feed import identities
        self.ratio,self.inputs_sha=identities(driver.source,lower)
        self.child_cursor=0;self.group_start=0;self.within=0;self.observations=0;self.active=None
        self.request_start=None
        if driver.contexts and any(g.mode=='on' for g in driver.contexts.groups.values()):raise ValueError('Magnifier cannot expose full requested OHLC with lookahead_on before close.')
        self.covered=0;self.fallback=0;self.last_point=None;self.phase='ready'
        driver.simulation.magnifier=self

    def summary(self):
        return {'profile_id':PROFILE,'configuration':copy.deepcopy(self.configuration),'inputs_sha256':self.inputs_sha,
            'covered_bars':self.covered,'fallback_bars':self.fallback,'lower_bars':self.covered*self.ratio,
            'model_points':self.observations,'bar_confirmed':self.active is None,'last_point':copy.deepcopy(self.last_point),
            'effective_mode':'mixed_parent_fallback' if self.fallback else 'lower_candles',
            'external_equivalence':False,'price_path':policy(6)['path']}

    def checkpoint(self,*,streaming=False):
        d=self.driver;e=d.evaluation
        return {'schema_version':'openpine.magnifier-state.1','count':e.count,'context_id':d.source.identity.context_id,'last_time':e.last_time,
            'inputs_sha256':self.inputs_sha,'configuration':copy.deepcopy(self.configuration),
            'runtime':e.checkpoint(streaming=streaming) if e.count else None,'contexts':d.contexts.checkpoint(streaming=streaming) if d.contexts else None,
            'simulation':d.simulation.checkpoint(),'cycle':d.cycle.checkpoint(streaming=streaming),
            'cursor':{'child_cursor':self.child_cursor,'group_start':self.group_start,'within':self.within,'observations':self.observations,
                'active':copy.deepcopy(self.active),'request_start':copy.deepcopy(self.request_start),'covered':self.covered,'fallback':self.fallback,'last_point':copy.deepcopy(self.last_point)}}

    def restore(self,state):
        d=self.driver;e=d.evaluation
        names={'schema_version','count','context_id','last_time','inputs_sha256','configuration','runtime','contexts','simulation','cycle','cursor'}
        if (type(state) is not dict or state.keys()!=names or state['schema_version']!='openpine.magnifier-state.1'
                or state['inputs_sha256']!=self.inputs_sha or state['configuration']!=self.configuration
                or state['context_id']!=d.source.identity.context_id or type(state['count']) is not int or not 0<=state['count']<=d.source.identity.count
                or (state['runtime'] is None)!=(state['count']==0) or (state['last_time'] is None)!=(state['count']==0)):
            raise ValueError('Invalid magnifier checkpoint identity.')
        cursor=state['cursor'];keys={'child_cursor','group_start','within','observations','active','covered','fallback','last_point','request_start'}
        if (type(cursor) is not dict or cursor.keys()!=keys or any(type(cursor[k]) is not int or cursor[k]<0 for k in keys-{'active','last_point','request_start'})
                or cursor['covered']+cursor['fallback']!=state['count'] or cursor['child_cursor']>self.lower.identity.count
                or cursor['group_start']>cursor['child_cursor'] or cursor['observations']<1
                or cursor['observations']!=4*(cursor['covered']*self.ratio+cursor['fallback'])+cursor['within']
                or self.configuration['missing']=='reject' and cursor['fallback']!=0
                or (cursor['active'] is None)!=(cursor['within']==0)):
            raise ValueError('Invalid lower-candle checkpoint cursor.')
        if state['count']:d.source.verify_cursor(state['count'],state['last_time'])
        if state['runtime'] is not None and any(state['runtime'].get(k)!=state[k] for k in ('count','context_id','last_time')):
            raise ValueError('Runtime differs from the completed primary boundary.')
        if state['simulation'].get('observations')!=cursor['observations']:raise ValueError('Broker differs from the model point cursor.')
        last_event=state['simulation']['last_observation']
        event=MarketEvent.from_payload(last_event)
        if event.sequence!=cursor['observations']-1:raise ValueError('Model event sequence differs from cursor.')
        if cursor['active'] is not None:
            if cursor['active']!=last_event or state['count']==d.source.identity.count:raise ValueError('Invalid active parent boundary.')
            iterator=iter(d.source.read_from(state['count']))
            try:parent=next(iterator)
            finally:
                if hasattr(iterator,'close'):iterator.close()
            feed=Feed(d.source,self.lower,self.configuration['missing'],offset=cursor['group_start'])
            try:rows,fallback,start,end=feed.group(parent)
            finally:feed.close()
            if start!=cursor['group_start'] or end!=cursor['child_cursor'] or not 0<cursor['within']<len(rows)*4:raise ValueError('Invalid inside-parent path cursor.')
            expected=None
            for index,pair in enumerate(events(parent,rows,fallback,state['context_id'],cursor['observations']-cursor['within'],self.lower.identity.timeframe,d.source.identity.timeframe),1):
                if index==cursor['within']:expected=pair;break
            if expected[0].payload()!=last_event or expected[1]!=cursor['last_point']:raise ValueError('Model cursor differs from pinned input path.')
        elif event.kind!='close' or event.time!=state['last_time'] or cursor['group_start']!=cursor['child_cursor']:
            raise ValueError('Invalid committed magnifier boundary.')
        else:
            iterator=iter(d.source.read_from(state['count']-1))
            try:parent=next(iterator)
            finally:
                if hasattr(iterator,'close'):iterator.close()
            metadata=cursor['last_point']
            if type(metadata) is not dict or type(metadata.get('fallback')) is not bool:raise ValueError('Missing model point provenance.')
            fallback=metadata['fallback'];tf=d.source.identity.timeframe if fallback else self.lower.identity.timeframe
            expected_event=MarketEvent(cursor['observations']-1,state['context_id'],'close',parent.time,next_open(parent.time,d.source.identity.timeframe),
                3 if fallback else self.ratio*4-1,parent.open,parent.high,parent.low,parent.close,parent.volume)
            expected_point={'parent_time':parent.time,'child_time':parent.time if fallback else next_open(parent.time,d.source.identity.timeframe)-int(tf)*60000,
                'child_timeframe':tf,'point':3,'price':parent.close,'available_at':expected_event.event_time,'fallback':fallback,'sequence':expected_event.sequence}
            if event.payload()!=expected_event.payload() or metadata!=expected_point:raise ValueError('Committed model point differs from pinned input.')
        anchors=cursor['request_start']
        if cursor['active'] is None:
            if anchors is not None:raise ValueError('Closed parent retains request anchors.')
        elif d.contexts:
            if type(anchors) is not dict or anchors.keys()!=d.contexts.groups.keys() or any(type(n) is not int or not 0<=n<=state['contexts'][k]['cursor'] for k,n in anchors.items()):raise ValueError('Invalid requested parent anchors.')
        elif anchors is not None:raise ValueError('Unexpected request anchors.')
        if state['runtime'] is not None:e.restore(state['runtime'])
        if d.contexts is not None:
            d.contexts.restore(state['contexts']);d.contexts.validate_available(event.time,event.event_time)
        elif state['contexts'] is not None:raise ValueError('Unexpected requested-context state.')
        d.simulation.model_point=copy.deepcopy(cursor['last_point'])
        d.simulation.restore_observation(state['simulation'],e.count,e.last_time,cursor['active'],sequence=event.sequence,
            context_id=d.source.identity.context_id,available_at=event.event_time)
        d.cycle.restore(state['cycle'],count=e.count,sequence=event.sequence,active=cursor['active'],observations=cursor['observations'],fill_count=d.simulation.broker.fill_count)
        for name in ('child_cursor','group_start','within','observations','active','covered','fallback','last_point','request_start'):setattr(self,name,copy.deepcopy(cursor[name]))
        d.phase='committed'

    def run(self,*,progress=None,cancelled=None,checkpoint=None,interval=0,pause=None,pause_after=None):
        d=self.driver;e=d.evaluation;e.cancelled=cancelled
        iterator=iter(d.source.read_from(e.count));feed=Feed(d.source,self.lower,self.configuration['missing'],offset=self.group_start if self.active else self.child_cursor)
        try:
            for parent in iterator:
                rows,fallback,start,end=feed.group(parent);skip=self.within;start_sequence=self.observations-skip
                if not skip:self.request_start={k:g.cursor for k,g in d.contexts.groups.items()} if d.contexts else None
                for index,(event,metadata) in enumerate(events(parent,rows,fallback,d.source.identity.context_id,start_sequence,self.lower.identity.timeframe,d.source.identity.timeframe),1):
                    if index<=skip:continue
                    if cancelled and cancelled():raise OpenPineError(Diagnostic('EXECUTION_CANCELLED','CANCELLED','Execution cancelled.'))
                    final=event.kind=='close';d.phase='executing';d.simulation.model_point=metadata
                    if e.observer:e.observer.cursor(primary_bar=e.count,mode='magnifier',event=event.payload(),model_point=metadata)
                    before=d.simulation.broker.fill_count
                    d.simulation.observe(event,first=index==1)
                    requests=d.contexts.advance_available(parent.time,event.event_time,cancelled,before=self.request_start) if d.contexts else {}
                    bar=BarFrame(event.context_id,e.count,event.time,event.close,event.open,event.high,event.low,event.volume)
                    d.cycle.run(bar,event.payload(),confirmed=final,filled=d.simulation.broker.fill_count>before,environment_phase='historical',requests=requests)
                    self.observations+=1;self.last_point=metadata;self.child_cursor=end
                    self.active=None if final else event.payload();self.within=0 if final else index;self.group_start=end if final else start
                    if final:self.covered+=not fallback;self.fallback+=fallback;self.request_start=None
                    d.phase='committed'
                    stopping=(pause and pause()) or (pause_after is not None and e.count>=pause_after)
                    period=self.configuration['checkpoint_every_children']
                    periodic=(final and interval and e.count%interval==0) or (index%4==0 and period and d.simulation.observations//4%period==0)
                    if checkpoint and (stopping or periodic):checkpoint(d)
                    if progress and (final and (e.count%1000==1 or e.count==d.source.identity.count)):progress(e.count,d.source.identity.count)
                    if stopping:
                        if checkpoint is None:raise ValueError('Pause requires durable checkpoint support.')
                        d.phase='paused';return False
            if e.count!=d.source.identity.count:raise ValueError('Primary input ended at another cursor.')
            d.phase='completed';return True
        except BaseException:d.phase='failed';e.closed=True;raise
        finally:
            e.cancelled=None;feed.close()
            if hasattr(iterator,'close'):iterator.close()
            if d.contexts:d.contexts.close()
