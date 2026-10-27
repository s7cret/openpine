"""Event adapter over the same typed private runtime and array prefix protocol."""
import copy
from collections import deque
from packages.contracts import TupleType
from packages.contracts.calendar import floor_open,next_open,aligned
from packages.contracts.lower_tf import OPERATION,ClosedWindow,options,requestable
from packages.contracts.lower_events import PROFILE
from packages.contracts.arrays import MAX_LENGTH,MAX_CELLS

class LowerEvents:
    def __init__(self,engine):
        self.engine=engine;self.sites={};self.late_count=0;self.incomplete_count=0;self.last_late=None
        self.last_parent=None;self.last_counts={}
        for site,r in engine.requests.items():
            if r['node'].operation!=OPERATION:continue
            cid=r['context_id'];ignore,count=options(r['node']);primary=engine.contexts[engine.primary_id]
            valid=requestable(primary.timeframe,engine.contexts[cid].timeframe)
            if not valid and not ignore:raise ValueError('Lower replay requires a smaller divisible UTC minute interval.')
            seed=engine.lower_config['warmup'].get(cid,{'count':0,'end_ms':0})
            self.sites[site]={'empty':not valid,'skip':max(0,seed['count']-count),'seed':seed,
                'rows':deque(),'windows':{},'width':len(r['node'].value_type.elements) if isinstance(r['node'].value_type,TupleType) else 1}

    def is_lower(self,site):return site in self.sites

    def committed_end(self):
        e=self.engine;last=e.members[e.primary_id]['last_closed']
        return next_open(last,e.contexts[e.primary_id].timeframe) if last is not None else None

    def validate_event(self,event):
        e=self.engine;end=self.committed_end()
        if event.kind=='close':
            cells=self.pending_cells()
            for site,v in self.sites.items():
                if v['empty'] or e.requests[site]['context_id']!=event.context_id:continue
                count=e.members[event.context_id]['count']+1;boundary=next_open(event.time,e.contexts[event.context_id].timeframe)
                if count<=v['seed']['count'] and boundary>v['seed']['end_ms']:raise ValueError('Lower warmup prefix ended before its pinned count.')
                if count<=v['skip'] or end is not None and boundary<=end:continue
                parent=floor_open(event.time,e.contexts[e.primary_id].timeframe)
                if len(v['windows'].get(parent,[]))>=MAX_LENGTH:raise ValueError('Lower event window exceeds its array budget.')
                cells+=v['width']
            if cells>MAX_CELLS:raise ValueError('Lower event pending windows exceed the shared cell budget.')
        if (event.kind=='close' and any(e.requests[s]['context_id']==event.context_id and not v['empty'] for s,v in self.sites.items())
                and end is not None and next_open(event.time,e.contexts[event.context_id].timeframe)<=end
                and e.lower_config['late']=='reject'):
            raise ValueError('Late lower close belongs to an already committed primary bar.')
        if event.context_id==e.primary_id and event.kind=='close' and e.lower_config['late']=='reject':
            for cid in {e.requests[s]['context_id'] for s,v in self.sites.items() if not v['empty']}:
                active=e.members[cid]['active']
                if active is not None and next_open(active['time'],e.contexts[cid].timeframe)<=next_open(event.time,e.contexts[e.primary_id].timeframe):
                    raise ValueError('Primary close precedes an observed lower candle close.')

    def close(self,site,event,count):
        from .realtime import frame,captured
        e=self.engine;r=e.requests[site];v=self.sites[site]
        if v['empty']:return
        seed=v['seed'];boundary=next_open(event.time,e.contexts[event.context_id].timeframe)
        if count<=seed['count'] and boundary>seed['end_ms']:raise ValueError('Lower warmup prefix ended before its pinned count.')
        if count<=v['skip']:return
        selected=r['evaluation'];selected.environment_phase=e.phase(event);selected.step(frame(event,selected.count));r['last']=captured(r['sink'])
        end=self.committed_end()
        if end is not None and boundary<=end:return
        parent=floor_open(event.time,e.contexts[e.primary_id].timeframe)
        rows=v['rows'];bucket=v['windows'].setdefault(parent,[])
        if len(bucket)>=MAX_LENGTH:raise ValueError('Lower event window exceeds its array budget.')
        typ=r['node'].arguments[2].expression.value_type
        if not selected.matches(r['last'],typ):raise ValueError('Lower event result differs from its expression type.')
        row={'time':event.time,'end':boundary,'count':count,'value':r['last'],'received_at':event.event_time,'sequence':event.sequence}
        rows.append(row);bucket.append(r['last'])
        if self.pending_cells()>MAX_CELLS:raise ValueError('Lower event pending windows exceed the shared cell budget.')

    def note_event(self,event):
        e=self.engine
        if event.kind!='close' or not any(e.requests[s]['context_id']==event.context_id and not v['empty'] for s,v in self.sites.items()):return
        end=self.committed_end()
        if end is not None and next_open(event.time,e.contexts[event.context_id].timeframe)<=end:
            self.late_count+=1;self.last_late={'context_id':event.context_id,'time':event.time,'sequence':event.sequence,'received_at':event.event_time,'committed_end':end}

    def value(self,site,event):
        rows=self.sites[site]['windows'].get(event.time,[])
        return ClosedWindow(rows,len(rows))

    def primary_closed(self,event):
        e=self.engine;tf=e.contexts[e.primary_id].timeframe;end=next_open(event.time,tf)
        self.last_parent=event.time;self.last_counts={s:len(v['windows'].get(event.time,[])) for s,v in self.sites.items()}
        for cid in {e.requests[s]['context_id'] for s,v in self.sites.items() if not v['empty']}:
            active=e.members[cid]['active']
            if active and next_open(active['time'],e.contexts[cid].timeframe)<=end:self.incomplete_count+=1
        for s,v in self.sites.items():
            while v['rows'] and v['rows'][0]['end']<=end:
                row=v['rows'].popleft();v['windows'].pop(floor_open(row['time'],tf),None)
            e.requests[s]['primary_cursor']=e.members[e.requests[s]['context_id']]['count']

    def pending_cells(self):return sum(len(v['rows'])*v['width'] for v in self.sites.values())

    def summary(self):
        e=self.engine;active=e.members[e.primary_id]['active']
        time=active['time'] if active else self.last_parent
        counts={s:len(v['windows'].get(time,[])) for s,v in self.sites.items()} if active else self.last_counts
        return {'profile_id':PROFILE,'late_policy':e.lower_config['late'],'warmup':copy.deepcopy(e.lower_config['warmup']),
            'parent_time':time,'closed_children':dict(counts),'pending_cells':self.pending_cells(),
            'late_closes':self.late_count,'incomplete_primary_closes':self.incomplete_count,'last_late':copy.deepcopy(self.last_late)}

    def record_state(self,site):
        r=self.engine.requests[site]
        return {'runtime':r['evaluation'].checkpoint() if r['evaluation'].count else None,'last':r['last'],
            'primary_cursor':r['primary_cursor'],'history':copy.deepcopy(list(self.sites[site]['rows']))}

    def checkpoint(self):
        return {'schema_version':'openpine.lower-events-state.1','late_closes':self.late_count,'incomplete_primary_closes':self.incomplete_count,
            'last_late':copy.deepcopy(self.last_late),'last_parent':self.last_parent,'last_counts':dict(self.last_counts)}

    def restore_record(self,site,row,state):
        from .realtime import decoded_request
        e=self.engine;r=e.requests[site];v=self.sites[site];member=e.members[r['context_id']];typ=r['node'].arguments[2].expression.value_type
        actual=0 if v['empty'] else max(0,member['count']-v['skip'])
        if (type(row) is not dict or row.keys()!={'runtime','last','primary_cursor','history'}
                or type(row['primary_cursor']) is not int or not 0<=row['primary_cursor']<=member['count']
                or (row['runtime'] is None)!=(actual==0) or type(row['history']) is not list or len(row['history'])*v['width']>MAX_CELLS):raise ValueError('Invalid lower event request state.')
        if actual:
            r['evaluation'].restore(row['runtime']);last=decoded_request(row['last'],typ)
            if (r['evaluation'].count!=actual or r['evaluation'].context_id!=r['context_id'] or r['evaluation'].last_time!=member['last_closed']
                    or not r['evaluation'].matches(last,typ)):raise ValueError('Lower event runtime differs from its closed cursor.')
            r['last']=last
        elif row['last'] is not None:raise ValueError('Unstarted lower runtime contains a value.')
        prior=None;count=v['skip'];seq=-1;end=self.committed_end();tf=e.contexts[r['context_id']].timeframe
        for raw in row['history']:
            if (type(raw) is not dict or raw.keys()!={'time','end','count','value','received_at','sequence'}
                    or type(raw['count']) is not int or not count<raw['count']<=member['count']
                    or type(raw['sequence']) is not int or not seq<raw['sequence']<=state['sequence']
                    or not aligned(raw['time'],tf) or raw['end']!=next_open(raw['time'],tf)
                    or type(raw['received_at']) is not int or not raw['end']<=raw['received_at']<=state['event_time']
                    or prior is not None and raw['time']<prior or end is not None and raw['end']<=end):raise ValueError('Invalid lower event window cursor.')
            value=decoded_request(raw['value'],typ)
            if not r['evaluation'].matches(value,typ):raise ValueError('Invalid lower event window value.')
            parent=floor_open(raw['time'],e.contexts[e.primary_id].timeframe);bucket=v['windows'].setdefault(parent,[])
            if len(bucket)>=MAX_LENGTH:raise ValueError('Lower event restored window exceeds budget.')
            bucket.append(value);v['rows'].append({**raw,'value':value});prior=raw['end'];count=raw['count'];seq=raw['sequence']
        if v['rows'] and (v['rows'][-1]['count']!=member['count'] or v['rows'][-1]['value']!=r['last']):raise ValueError('Lower event tail differs from runtime.')
        if v['empty'] and (row['history'] or actual):raise ValueError('Invalid timeframe has lower event values.')
        r['primary_cursor']=row['primary_cursor']

    def restore(self,value,state):
        e=self.engine;names={'schema_version','late_closes','incomplete_primary_closes','last_late','last_parent','last_counts'}
        if (type(value) is not dict or value.keys()!=names or value['schema_version']!='openpine.lower-events-state.1'
                or type(value['late_closes']) is not int or not 0<=value['late_closes']<=state['sequence']+1
                or type(value['incomplete_primary_closes']) is not int or not 0<=value['incomplete_primary_closes']<=8*(state['sequence']+1)
                or type(value['last_counts']) is not dict or not value['last_counts'].keys()<=self.sites.keys()
                or any(type(n) is not int or not 0<=n<=MAX_LENGTH for n in value['last_counts'].values())
                or value['last_parent']!=e.members[e.primary_id]['last_closed'] or self.pending_cells()>MAX_CELLS):raise ValueError('Invalid lower event summary.')
        if value['last_counts'].keys()!=(self.sites.keys() if value['last_parent'] is not None else set()):raise ValueError('Missing lower window counts.')
        late=value['last_late']
        if (late is None)!=(value['late_closes']==0):raise ValueError('Missing late lower event provenance.')
        if late is not None:
            if (type(late) is not dict or late.keys()!={'context_id','time','sequence','received_at','committed_end'}
                    or late['context_id'] not in {e.requests[s]['context_id'] for s in self.sites}
                    or type(late['sequence']) is not int or not 0<=late['sequence']<=state['sequence']
                    or type(late['received_at']) is not int or late['received_at']>state['event_time']
                    or type(late['committed_end']) is not int or self.committed_end() is None or late['committed_end']>self.committed_end()
                    or not aligned(late['time'],e.contexts[late['context_id']].timeframe)
                    or next_open(late['time'],e.contexts[late['context_id']].timeframe)>late['committed_end']):raise ValueError('Invalid late lower provenance.')
        if e.lower_config['late']=='reject' and (value['late_closes'] or value['incomplete_primary_closes']):raise ValueError('Strict lower replay contains skipped closes.')
        self.late_count=value['late_closes'];self.incomplete_count=value['incomplete_primary_closes'];self.last_late=copy.deepcopy(late)
        self.last_parent=value['last_parent'];self.last_counts=dict(value['last_counts'])
