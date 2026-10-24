"""Bounded event queues and exact rolling sums with changed-cell previews."""
import copy
import math
from packages.runtime.series import KernelError
from packages.runtime.series.technical import units,CellOverlay
from packages.runtime.history import LIMIT


def require(value):
    if not value:raise ValueError('Invalid signal kernel checkpoint.')


def fixed(state,value,occurrence=False):
    low,high=(0,LIMIT-1) if occurrence else (1,LIMIT)
    if type(value) is not int or not low<=value<=high:
        raise KernelError('SIGNAL_PARAMETER','Signal length/occurrence is outside its bounded integer range.','rule.signals.parameter')
    if state.length is None:state.length=value
    elif state.length!=value:raise KernelError('SIGNAL_PARAMETER_CHANGED','Signal length/occurrence must remain fixed per call/context.','rule.signals.parameter')


def finite_ratio(numerator,denominator=1):
    try:value=numerator/denominator
    except (OverflowError,ZeroDivisionError) as e:raise KernelError('SIGNAL_VALUE_RANGE','Signal result exceeds finite numeric range.','rule.signals.numeric') from e
    if not math.isfinite(value):raise KernelError('SIGNAL_VALUE_RANGE','Signal result exceeds finite numeric range.','rule.signals.numeric')
    return value


class EventState:
    def __init__(self,budget,kind,base='float',version=6):
        self.budget,self.kind,self.base,self.version=budget,kind,base,version
        self.length=None;self.calls=0;self.count=0;self.elapsed=None;self.slots=[]

    def update(self,*args):
        if self.kind=='cum':
            self.calls+=1
            if args[0] is not None:self.count+=1;self.total=getattr(self,'total',0)+units(args[0])
            return finite_ratio(getattr(self,'total',0),1<<1074) if self.count else None
        if self.kind=='barssince':
            self.calls+=1
            if args[0] is True:self.elapsed=0
            elif self.elapsed is not None:self.elapsed+=1
            return self.elapsed
        condition,source,occurrence=args;fixed(self,occurrence,True)
        if not self.slots:
            for _ in range(occurrence+1):self.budget.reserve()
            self.slots=[None]*(occurrence+1)
        self.calls+=1
        if condition is True:self.slots[self.count%(occurrence+1)]=source;self.count+=1
        value=self.slots[(self.count-occurrence-1)%(occurrence+1)] if self.count>occurrence else None
        return False if value is None and self.base=='bool' and self.version==6 else value


class WindowState:
    def __init__(self,budget,kind):
        self.budget,self.kind=budget,kind;self.length=None;self.calls=0;self.count=0;self.slots=[]
        self.total=0;self.weighted=0;self.denominator=0

    def initialize(self,length):
        fixed(self,length)
        if not self.slots:
            size=length+1 if self.kind in ('mom','roc') else length
            for _ in range(size*(2 if self.kind=='vwma' else 1)):self.budget.reserve()
            self.slots=[None]*size

    def update(self,*args):
        if self.kind=='vwma':volume,source,length=args
        else:source,length=args;volume=None
        self.initialize(length);self.calls+=1
        if self.kind in ('mom','roc'):
            previous=self.slots[(self.count-length)%len(self.slots)] if self.count>=length else None
            self.slots[self.count%len(self.slots)]=source;self.count+=1
            if source is None or previous is None or self.kind=='roc' and previous==0:return None
            delta=units(source)-units(previous)
            return finite_ratio(delta,1<<1074) if self.kind=='mom' else finite_ratio(delta*100,units(previous))
        if self.kind=='vwma' and volume is not None and volume<0:raise KernelError('SIGNAL_VOLUME','VWMA volume must be nonnegative.','rule.signals.volume')
        if source is not None and (self.kind!='vwma' or volume is not None):
            i=self.count%length;old=self.slots[i]
            if self.kind=='vwma':
                u,v=units(source),units(volume)
                self.total+=u*v-(units(old[0])*units(old[1]) if old is not None else 0)
                self.denominator+=v-(units(old[1]) if old is not None else 0)
                self.slots[i]=(source,volume)
            else:
                u=units(source)
                if self.count>=length:self.weighted-=self.total
                self.weighted+=min(self.count+1,length)*u
                self.total+=u-(units(old) if old is not None else 0);self.slots[i]=source
            self.count+=1
        if self.count<length:return None
        if self.kind=='vwma':return finite_ratio(self.total,self.denominator<<1074) if self.denominator else None
        return finite_ratio(self.weighted,((length*(length+1))//2)<<1074) if self.kind=='wma' else finite_ratio(self.total,1<<1074)


def better(a,b,high):
    if a is None:return b
    if b is None:return a
    if a[0]==b[0]:return a if a[1]>b[1] else b
    return a if (a[0]>b[0])==high else b


class IndexState:
    def __init__(self,budget,kind,version=6):
        self.budget,self.kind,self.version=budget,kind,version;self.length=None;self.calls=0;self.count=0;self.nodes=[]

    def initialize(self,length):
        fixed(self,length)
        if not self.nodes:
            for _ in range(4*length):self.budget.reserve()
            self.nodes=[None]*(2*length)

    def update(self,source,length):
        self.initialize(length);self.calls+=1;high=self.kind in ('highestbars','rising');trend=self.kind in ('rising','falling')
        if trend:
            prior=self.nodes[1]
            result=(source>prior[0] if high else source<prior[0]) if source is not None and self.count>=length and prior is not None else (False if self.version==6 else None)
            if source is None:return result
        i=length+self.count%length;self.nodes[i]=(source,self.count) if source is not None else None
        while i>1:
            i//=2;self.nodes[i]=better(self.nodes[2*i],self.nodes[2*i+1],high)
        self.count+=1
        if trend:return result
        top=self.nodes[1];return top[1]-(self.count-1) if top is not None else None


SIGNAL_KINDS=('barssince','valuewhen','cum','sum','wma','vwma','mom','roc','rising','falling','highestbars','lowestbars')
SIGNAL_TYPES=(EventState,WindowState,IndexState)


def export_signal(state):
    common={'calls':state.calls,'count':state.count}
    if state.kind=='cum':common['total']=getattr(state,'total',0)
    elif state.kind=='barssince':common['elapsed']=state.elapsed
    elif type(state) is IndexState:common.update(length=state.length,slots=[state.nodes[i] for i in range(state.length,2*state.length)])
    else:common.update(length=state.length,slots=[state.slots[i] for i in range(len(state.slots))])
    return state.kind,common


def restore_signal(kind,value,budget,updates,base,version):
    require(kind in SIGNAL_KINDS and type(value) is dict)
    special={'total'} if kind=='cum' else {'elapsed'} if kind=='barssince' else {'length','slots'}
    require(value.keys()=={'calls','count'}|special)
    calls,count=value['calls'],value['count'];require(type(calls) is int and 1<=calls<=updates and type(count) is int and 0<=count<=calls)
    def number(x):require(type(x) is float and math.isfinite(x))
    if kind in ('cum','barssince','valuewhen'):
        state=EventState(budget,kind,base,version)
        if kind=='cum':
            total=value['total'];require(type(total) is int and total.bit_length()<=2115 and abs(total)<=count*units(float.fromhex('0x1.fffffffffffffp+1023')))
            state.total=total
        elif kind=='barssince':
            elapsed=value['elapsed'];require(count==0 and (elapsed is None or type(elapsed) is int and 0<=elapsed<calls));state.elapsed=elapsed
        else:
            length=value['length'];require(type(length) is int and 0<=length<LIMIT);fixed(state,length,True)
            slots=value['slots'];require(type(slots) is list and len(slots)==length+1)
            for i,x in enumerate(slots):
                require(x is None or type(x) is {'int':int,'float':float,'bool':bool}[base])
                if type(x) is float:number(x)
                if type(x) is int:require(-(2**63)<=x<2**63)
                require(count>=len(slots) or i<count or x is None)
                require(base!='bool' or version!=6 or x is not None or count<len(slots) and i>=count)
                budget.reserve()
            state.slots=list(slots)
    else:
        length=value['length'];require(type(length) is int and 1<=length<=LIMIT)
        indexed=kind in ('rising','falling','highestbars','lowestbars')
        state=IndexState(budget,kind,version) if indexed else WindowState(budget,kind);state.initialize(length)
        slots=value['slots'];size=length+1 if kind in ('mom','roc') else length
        require(type(slots) is list and len(slots)==size)
        if kind in ('mom','roc','highestbars','lowestbars'):require(count==calls)
        for i,x in enumerate(slots):
            require(count>=size or i<count or x is None)
            if x is None:
                require(kind in ('mom','roc','highestbars','lowestbars') or count<size and i>=count)
            elif indexed:
                require(type(x) in (list,tuple) and len(x)==2);number(x[0]);n=x[1]
                require(type(n) is int and max(0,count-length)<=n<count and n%length==i)
                require(n==count-1-((count-1-i)%length))
            elif kind=='vwma':require(type(x) in (list,tuple) and len(x)==2);number(x[0]);number(x[1]);require(x[1]>=0)
            else:number(x)
        if indexed:
            state.nodes[length:]=[tuple(x) if x is not None else None for x in slots]
            for i in range(length-1,0,-1):state.nodes[i]=better(state.nodes[2*i],state.nodes[2*i+1],kind in ('rising','highestbars'))
        else:
            state.slots=[tuple(x) if kind=='vwma' and x is not None else x for x in slots]
            ordered=[state.slots[(max(0,count-size)+i)%size] for i in range(min(count,size))]
            if kind=='vwma':state.total=sum(units(x[0])*units(x[1]) for x in ordered);state.denominator=sum(units(x[1]) for x in ordered)
            elif kind in ('sum','wma'):
                state.total=sum(units(x) for x in ordered);state.weighted=sum((i+1)*units(x) for i,x in enumerate(ordered))
    state.calls,state.count=calls,count
    return state


def fork_signal(state,budget):
    result=copy.copy(state);result.budget=budget
    if type(state) is IndexState:result.nodes=CellOverlay(state.nodes)
    elif state.slots:result.slots=CellOverlay(state.slots)
    return result


def kernel(kind):
    def run(arguments,context):
        def factory():
            if kind in ('barssince','valuewhen','cum'):return EventState(context.budget,kind,context.result_base,context.language_version)
            if kind in ('highestbars','lowestbars','rising','falling'):return IndexState(context.budget,kind,context.language_version)
            return WindowState(context.budget,kind)
        return context.state(kind,factory).update(*arguments)
    return run
