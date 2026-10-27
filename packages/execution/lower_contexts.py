"""Bounded lower-candle windows inside the shared historical context graph."""
from packages.contracts import TupleType
from packages.contracts.calendar import next_open
from packages.contracts.arrays import MAX_LENGTH
from .contexts import Group,invalid

def closed_count(source,end_time):
    """Index by actual rows, including gaps; close every temporary iterator."""
    tf=source.identity.timeframe
    if next_open(source.identity.last_ms,tf)<=end_time:return source.identity.count
    lo,hi=0,source.identity.count
    while lo<hi:
        mid=(lo+hi)//2;iterator=iter(source.read_from(mid))
        try:bar=next(iterator)
        finally:
            if hasattr(iterator,'close'):iterator.close()
        if next_open(bar.time,tf)<=end_time:lo=mid+1
        else:hi=mid
    return lo

class LowerGroup(Group):
    def __init__(self,binding,source,count,empty,nodes,runtime,program,settings,end):
        super().__init__(binding,source,f'lower:{count}:{int(empty)}',nodes,runtime,program,settings,raw=True)
        self.end=0 if empty else end
        self.start=max(0,self.end-count);self.cursor=self.start;self.empty=empty
        self.parent_time=None;self.times=[];self.buffers={site:[] for site in self.records}

    def peek(self):return None if self.empty or self.cursor>=self.end else super().peek()

    def begin(self,time):
        if self.parent_time is not None and time<self.parent_time:invalid('Lower request primary time moved backwards.')
        if time!=self.parent_time:
            self.parent_time=time;self.times=[];self.buffers={site:[] for site in self.records}

    def collect(self):
        if self.last_time<self.parent_time:return
        if len(self.times)>=MAX_LENGTH:invalid('Lower request exceeds 100000 children per primary window.')
        self.times.append(self.last_time)
        for site,(_,evaluation,sink) in self.records.items():
            typ=self.records[site][0].arguments[2].expression.value_type
            if not evaluation.matches(sink.last,typ):invalid('Lower expression result differs from its scalar/tuple type.')
            self.buffers[site].append(sink.last)

    @property
    def pending_cells(self):
        return sum(len(rows)*(len(node.value_type.elements) if isinstance(node.value_type,TupleType) else 1)
            for site,(node,_,_) in self.records.items() for rows in (self.buffers[site],))

    def values(self,before):
        from packages.contracts.lower_tf import ClosedWindow
        result={}
        for site,(node,_,_) in self.records.items():
            rows=self.buffers[site]
            result[site]=ClosedWindow(rows,len(rows))
        return result

    def checkpoint(self):
        return {'schema_version':'openpine.lower-window.1','start':self.start,'end':self.end,'cursor':self.cursor,'last_time':self.last_time,
            'parent_time':self.parent_time,'times':list(self.times),'buffers':{k:list(v) for k,v in self.buffers.items()},
            'records':{site:{'runtime':evaluation.checkpoint() if self.cursor>self.start else None,'last':sink.last}
                for site,(_,evaluation,sink) in self.records.items()}}

    def restore(self,state):
        names={'schema_version','start','end','cursor','last_time','parent_time','times','buffers','records'}
        if (type(state) is not dict or state.keys()!=names or state['schema_version']!='openpine.lower-window.1'
                or type(state['start']) is not int or state['start']!=self.start
                or type(state['end']) is not int or state['end']!=self.end
                or type(state['cursor']) is not int or not self.start<=state['cursor']<=self.end
                or type(state['parent_time']) is not int or type(state['times']) is not list
                or len(state['times'])>MAX_LENGTH or any(type(t) is not int for t in state['times'])
                or state['times']!=sorted(set(state['times'])) or type(state['buffers']) is not dict
                or state['buffers'].keys()!=self.buffers.keys() or type(state['records']) is not dict
                or state['records'].keys()!=self.records.keys()):raise ValueError('Invalid lower request checkpoint shape.')
        cursor=state['cursor'];advanced=cursor>self.start
        if self.empty and (advanced or state['times']):raise ValueError('Invalid-timeframe request contains values.')
        if advanced:
            if type(state['last_time']) is not int:raise ValueError('Missing lower cursor timestamp.')
            self.source.verify_cursor(cursor,state['last_time']) if hasattr(self.source,'verify_cursor') else None
        elif state['last_time'] is not None:raise ValueError('Unstarted lower runtime has a timestamp.')
        buffers={}
        for site,(node,evaluation,sink) in self.records.items():
            row=state['records'][site];typ=node.arguments[2].expression.value_type
            if type(row) is not dict or row.keys()!={'runtime','last'}:raise ValueError('Invalid lower expression state.')
            values=state['buffers'][site]
            if type(values) is not list or len(values)!=len(state['times']):raise ValueError('Lower window length differs.')
            def normalize(v):return tuple(v) if isinstance(typ,TupleType) and type(v) in (tuple,list) else v
            values=[normalize(v) for v in values]
            if not all(evaluation.matches(v,typ) for v in values):raise ValueError('Invalid lower window element type.')
            if advanced:
                evaluation.restore(row['runtime']);last=normalize(row['last'])
                if (evaluation.count!=cursor-self.start or evaluation.context_id!=self.source.identity.context_id
                        or evaluation.last_time!=state['last_time'] or not evaluation.matches(last,typ)
                        or values and values[-1]!=last):raise ValueError('Lower runtime/window differs from cursor.')
                sink.last=last
            elif row['runtime'] is not None or row['last'] is not None:raise ValueError('Unstarted lower runtime contains state.')
            buffers[site]=values
        if len(state['times'])>cursor-self.start:raise ValueError('Lower window precedes evaluation start.')
        first=cursor-len(state['times']);offset=max(self.start,first-1);iterator=iter(self.source.read_from(offset))
        try:
            expected=[]
            for index in range(offset,cursor):
                bar=next(iterator)
                if bar.time>=state['parent_time']:expected.append(bar.time)
            if expected!=state['times']:raise ValueError('Lower window timestamps differ from pinned input.')
        finally:
            if hasattr(iterator,'close'):iterator.close()
        self.cursor,self.last_time,self.parent_time=cursor,state['last_time'],state['parent_time']
        self.times=list(state['times']);self.buffers=buffers

    def validate_window(self,primary_time,available_at):
        if self.parent_time!=primary_time:raise ValueError('Lower window belongs to another primary bar.')
        if any(not primary_time<=t or next_open(t,self.binding.context.timeframe)>available_at for t in self.times):
            raise ValueError('Lower window contains an unavailable child.')
