"""Bounded changing windows: exact prefix statistics and range extrema."""
import copy
import math
from packages.contracts.dynamic_windows import HORIZON
from packages.runtime.history import Ring
from packages.runtime.series import KernelError
from packages.runtime.series.technical import units,combine,CellOverlay

COUNT_LIMIT=2**63-1
KINDS=('window.sum','window.wma','window.moments','window.highest','window.lowest')
KIND_OPERATIONS={k:(k,) for k in KINDS if k!='window.moments'}
KIND_OPERATIONS['window.moments']=('window.variance','window.stdev','window.variance.population','window.stdev.population')

def length_value(n):
    if type(n) is not int or not 1<=n<=HORIZON:
        raise KernelError('WINDOW_LENGTH','Changing window length must be a defined int in 1..100000.','rule.dynamic-window.length')

def room(budget,n):
    if budget.cells+n>budget.limit:
        raise KernelError('HISTORY_STORAGE','Run history storage limit exceeded.','rule.history')

def finite(value):
    if not math.isfinite(value):raise KernelError('WINDOW_VALUE_RANGE','Changing window requires finite values/results.','rule.dynamic-window.numeric')
    return value

def ratio(n,d):
    try:return finite(n/d)
    except OverflowError as error:raise KernelError('WINDOW_VALUE_RANGE','Changing statistic exceeds finite range.','rule.dynamic-window.numeric') from error

class PrefixWindow:
    def __init__(self,budget,kind):
        self.budget,self.kind=budget,kind;self.calls=self.count=0;self.length=None
        self.rings=[Ring(budget) for _ in range(1 if kind=='window.sum' else 2)]
        room(budget,len(self.rings))
        for r in self.rings:r.append(0)

    def update(self,source,length,biased=True,*,root=False):
        length_value(length)
        if self.calls>=COUNT_LIMIT:raise KernelError('WINDOW_COUNT','Changing window call count exceeds int64.','rule.dynamic-window.count')
        if source is not None:finite(source)
        if source is not None:
            room(self.budget,sum(r.count<r.capacity for r in self.rings))
            u=units(source);count=self.count+1
            self.rings[0].append(self.rings[0].ago(0)+u)
            if len(self.rings)==2:self.rings[1].append(self.rings[1].ago(0)+(count*u if self.kind=='window.wma' else u*u))
            self.count=count
        self.calls+=1;self.length=length
        if self.count<length:return None
        total=self.rings[0].ago(0)-self.rings[0].ago(length)
        if self.kind=='window.sum':return ratio(total,1<<1074)
        second=self.rings[1].ago(0)-self.rings[1].ago(length)
        if self.kind=='window.wma':return ratio(second-(self.count-length)*total,(length*(length+1)//2)<<1074)
        if biased is None or biased is False and length==1:return None
        numerator=length*second-total*total;denominator=length*(length if biased else length-1)
        if not numerator:return 0.0
        if not root:return ratio(numerator,denominator<<2148)
        scale=2*((numerator.bit_length()-denominator.bit_length()-2148)//2);shift=2148+scale
        normalized=numerator/(denominator<<shift) if shift>=0 else (numerator<<-shift)/denominator
        try:return finite(math.ldexp(math.sqrt(normalized),scale//2))
        except OverflowError as error:raise KernelError('WINDOW_VALUE_RANGE','Changing deviation exceeds finite range.','rule.dynamic-window.numeric') from error

class RangeWindow:
    def __init__(self,budget,kind):
        self.budget,self.kind=budget,kind;self.calls=0;self.length=None;self.nodes=[]

    def initialize(self):
        if not self.nodes:
            room(self.budget,2*HORIZON)
            for _ in range(2*HORIZON):self.budget.reserve()
            self.nodes=[None]*(2*HORIZON)

    def interval(self,left,right):
        left+=HORIZON;right+=HORIZON;value=None;high=self.kind=='window.highest'
        while left<right:
            if left&1:value=combine(value,self.nodes[left],high);left+=1
            if right&1:right-=1;value=combine(value,self.nodes[right],high)
            left//=2;right//=2
        return value

    def update(self,source,length):
        length_value(length)
        if self.calls>=COUNT_LIMIT:raise KernelError('WINDOW_COUNT','Changing window call count exceeds int64.','rule.dynamic-window.count')
        if source is not None:finite(source)
        self.initialize();i=HORIZON+self.calls%HORIZON;self.nodes[i]=source
        while i>1:
            i//=2;self.nodes[i]=combine(self.nodes[2*i],self.nodes[2*i+1],self.kind=='window.highest')
        self.calls+=1;self.length=length;n=min(length,self.calls);start=(self.calls-n)%HORIZON;end=self.calls%HORIZON
        return self.interval(start,end) if start<end else combine(self.interval(start,HORIZON),self.interval(0,end),self.kind=='window.highest')

TYPES=(PrefixWindow,RangeWindow)

def kernel(operation):
    def run(arguments,context):
        length_value(arguments[1])
        if operation in ('window.highest','window.lowest'):
            return context.state(operation,lambda:RangeWindow(context.budget,operation)).update(*arguments)
        kind='window.moments' if operation in KIND_OPERATIONS['window.moments'] else operation
        state=context.state(kind,lambda:PrefixWindow(context.budget,kind))
        return state.update(*arguments,root=operation in ('window.stdev','window.stdev.population'))
    return run

def export_window(state):
    common={'calls':state.calls,'length':state.length}
    if type(state) is RangeWindow:common['leaves']=list(state.nodes[HORIZON:HORIZON+min(state.calls,HORIZON)])
    else:
        n=min(state.count,HORIZON)
        # Store float samples plus exact anchors, not hundreds of thousands of
        # wide cumulative integers. Prefix arrays are rebuilt on restore.
        samples=[(state.rings[0].ago(i)-state.rings[0].ago(i+1))/(1<<1074) for i in range(n-1,-1,-1)]
        common.update(count=state.count,samples=samples,anchors=[r.ago(n) for r in state.rings],totals=[r.ago(0) for r in state.rings])
    return state.kind,common

def require(value):
    if not value:raise ValueError('Invalid changing-window checkpoint.')

def restore_window(kind,value,budget,updates):
    require(kind in KINDS and type(value) is dict)
    ranged=kind in ('window.highest','window.lowest')
    require(value.keys()==({'calls','length','leaves'} if ranged else {'calls','length','count','samples','anchors','totals'}))
    calls,length=value['calls'],value['length']
    require(type(calls) is int and 1<=calls<=min(updates,COUNT_LIMIT) and type(length) is int and 1<=length<=HORIZON)
    if ranged:
        leaves=value['leaves'];require(type(leaves) is list and len(leaves)==min(calls,HORIZON))
        require(all(x is None or type(x) is float and math.isfinite(x) for x in leaves))
        state=RangeWindow(budget,kind);state.initialize();state.nodes[HORIZON:HORIZON+len(leaves)]=leaves
        for i in range(HORIZON-1,0,-1):state.nodes[i]=combine(state.nodes[2*i],state.nodes[2*i+1],kind=='window.highest')
    else:
        count=value['count'];size=1 if kind=='window.sum' else 2
        require(type(count) is int and 0<=count<=calls)
        samples,anchors,totals=value['samples'],value['anchors'],value['totals']
        require(type(samples) is list and len(samples)==min(count,HORIZON) and all(type(x) is float and math.isfinite(x) for x in samples))
        require(type(anchors) is list and len(anchors)==size and type(totals) is list and len(totals)==size)
        state=object.__new__(PrefixWindow);state.kind=kind;state.budget=budget;state.count=count;state.rings=[]
        max_unit=units(float.fromhex('0x1.fffffffffffffp+1023'))
        before=count-len(samples);retained=len(samples)+1
        for index in range(size):
            for n,x in ((before,anchors[index]),(count,totals[index])):
                bound=n*max_unit if index==0 else n*(n+1)//2*max_unit if kind=='window.wma' else n*max_unit*max_unit
                require(type(x) is int and abs(x)<=bound and (index==0 or kind=='window.wma' or x>=0))
        room(budget,size*retained)
        for _ in range(size):
            for _ in range(retained):budget.reserve()
            ring=Ring(budget);ring.items=[None]*retained;ring.count=count+1;state.rings.append(ring)
        current=list(anchors)
        for i in range(retained):
            if i:
                u=units(samples[i-1]);current[0]+=u
                if size==2:current[1]+=(before+i)*u if kind=='window.wma' else u*u
            for index,r in enumerate(state.rings):r.items[(before+i)%r.capacity]=current[index]
        require(current==totals)
    state.calls=calls;state.length=length
    return state

def fork_window(state,budget):
    from packages.runtime.rollback import RingOverlay
    clone=copy.copy(state);clone.budget=budget
    if type(state) is RangeWindow:clone.nodes=CellOverlay(state.nodes)
    else:clone.rings=[RingOverlay(r,budget) for r in state.rings]
    return clone
