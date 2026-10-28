"""Bounded exact window moments and composite indicator state lifecycles."""
import copy
import math
from packages.runtime.series import EMAState,SMASeedEMAState,KernelError
from packages.runtime.series.technical import units,CellOverlay
from packages.runtime.series.basic import length_value


class MomentsState:
    def __init__(self,budget):
        self.budget=budget;self.length=None;self.count=0;self.leaves=[]
        self.total=0;self.squares=0

    def update(self,source,length):
        length_value(length)
        if self.length is None:
            for _ in range(length):self.budget.reserve()
            self.length=length;self.leaves=[None]*length
        if self.length!=length:raise KernelError('COMPOSITE_LENGTH_CHANGED','Window length must remain fixed.','rule.composite.length')
        if source is not None:
            if not math.isfinite(source):raise KernelError('COMPOSITE_VALUE_RANGE','Window source must be finite.','rule.composite.numeric')
            position=self.count%length;previous=self.leaves[position]
            if previous is not None:
                old=units(previous);self.total-=old;self.squares-=old*old
            current=units(source);self.total+=current;self.squares+=current*current
            self.leaves[position]=source;self.count+=1

    def mean(self):
        return None if self.count<self.length else self.total/(self.length<<1074)

    def variance(self,biased=True,*,root=False):
        n=self.length
        if self.count<n or biased is None or (not biased and n==1):return None
        numerator=n*self.squares-self.total*self.total
        denominator=n*(n if biased else n-1)
        if not numerator:return 0.0
        try:
            if not root:return numerator/(denominator<<2148)
            scale=2*((numerator.bit_length()-denominator.bit_length()-2148)//2)
            shift=2148+scale
            normalized=numerator/(denominator<<shift) if shift>=0 else (numerator<<-shift)/denominator
            return math.ldexp(math.sqrt(normalized),scale//2)
        except OverflowError as error:
            raise KernelError('COMPOSITE_VALUE_RANGE','Statistic exceeds finite float range.','rule.composite.numeric') from error


class MACDState:
    def __init__(self,budget,version):
        self.budget,self.version=budget,version;self.lengths=None
        cls=SMASeedEMAState if version<5 else EMAState
        self.parts=[cls(),cls(),cls()];self.seed_cells=[0,0,0]
        self.seed_labels=[budget.label+' MACD warmup '+str(i) for i in range(3)]

    def reconcile(self):
        if self.version<5 and self.budget.allocation:
            for state,label in zip(self.parts,self.seed_labels,strict=True):
                self.budget.allocation.resize(len(state.pending),label,'value')

    def ema(self,index,source,length):
        state=self.parts[index]
        if self.version<5 and state.previous is None and source is not None:
            self.budget.reserve(label=self.seed_labels[index]);self.seed_cells[index]+=1
        result=state.update(source,length)
        if self.version<5 and state.previous is not None and self.budget.allocation:
            self.budget.allocation.resize(0,self.seed_labels[index],'value')
        return result

    def update(self,source,fast,slow,signal):
        for length in (fast,slow,signal):length_value(length)
        lengths=(fast,slow,signal)
        if self.lengths is None:self.lengths=lengths
        if self.lengths!=lengths:raise KernelError('COMPOSITE_LENGTH_CHANGED','MACD lengths must remain fixed.','rule.composite.length')
        a,b=self.ema(0,source,fast),self.ema(1,source,slow)
        value=None if a is None or b is None else a-b
        average=self.ema(2,value,signal)
        return value,average,None if value is None or average is None else value-average


class BBState:
    def __init__(self,budget):self.moments=MomentsState(budget)

    def update(self,source,length,mult):
        self.moments.update(source,length)
        middle=self.moments.mean();deviation=self.moments.variance(root=True)
        width=None if deviation is None or mult is None else deviation*mult
        return middle,None if width is None else middle+width,None if width is None else middle-width


COMPOSITE_KINDS=('moments','macd','bb')
COMPOSITE_TYPES=(MomentsState,MACDState,BBState)
KIND_OPERATIONS={'moments':('series.variance','series.stdev','series.variance.population','series.stdev.population'),
    'macd':('series.macd',),'bb':('series.bb',)}


def export_composite(state):
    if type(state) is MomentsState:
        return 'moments',{'length':state.length,'count':state.count,'leaves':list(state.leaves),
            'total':state.total,'squares':state.squares}
    if type(state) is BBState:return 'bb',export_composite(state.moments)[1]
    if type(state) is MACDState:
        return 'macd',{'lengths':list(state.lengths),'seed_cells':list(state.seed_cells),
            'parts':[{'previous':s.previous,**({'pending':list(s.pending)} if state.version<5 else {})} for s in state.parts]}
    raise ValueError('Unregistered composite state.')


def require(value):
    if not value:raise ValueError('Invalid composite kernel checkpoint.')


def restore_composite(kind,value,budget,count,version):
    if kind in ('moments','bb'):
        require(type(value) is dict and value.keys()=={'length','count','leaves','total','squares'})
        length,n,leaves=value['length'],value['count'],value['leaves']
        require(type(length) is int and 1<=length<=100000 and type(n) is int and 0<=n<=count)
        require(type(leaves) is list and len(leaves)==length)
        require(type(value['total']) is int and value['total'].bit_length()<=2115
            and type(value['squares']) is int and 0<=value['squares'] and value['squares'].bit_length()<=4230)
        total=squares=0
        for index,leaf in enumerate(leaves):
            require(leaf is None or type(leaf) is float and math.isfinite(leaf))
            require((leaf is not None)==(n>=length or index<n))
            if leaf is not None:u=units(leaf);total+=u;squares+=u*u
        require(total==value['total'] and squares==value['squares'])
        result=MomentsState(budget);result.update(None,length)
        result.count=n;result.leaves=list(leaves);result.total=total;result.squares=squares
        if kind=='bb':wrapper=BBState(budget);wrapper.moments=result;return wrapper
        return result
    require(kind=='macd' and type(value) is dict and value.keys()=={'lengths','seed_cells','parts'})
    lengths,cells,parts=value['lengths'],value['seed_cells'],value['parts']
    require(type(lengths) is list and len(lengths)==3 and all(type(n) is int and 1<=n<=100000 for n in lengths))
    require(type(cells) is list and len(cells)==3 and type(parts) is list and len(parts)==3)
    result=MACDState(budget,version);result.lengths=tuple(lengths)
    for index,(row,length,allocated) in enumerate(zip(parts,lengths,cells,strict=True)):
        require(type(row) is dict and row.keys()==({'previous','pending'} if version<5 else {'previous'}))
        previous=row['previous'];require(previous is None or type(previous) is float and math.isfinite(previous))
        require(type(allocated) is int and 0<=allocated<=min(count,length))
        if version<5:
            pending=row['pending'];require(type(pending) is list and len(pending)<length
                and all(type(v) is float and math.isfinite(v) for v in pending))
            require((previous is None and len(pending)==allocated) or (previous is not None and not pending and allocated==length))
            budget.reserve_logical(allocated)
            budget.scratch(len(pending),'value',result.seed_labels[index])
            result.parts[index].pending=list(pending)
        else:require(allocated==0)
        result.parts[index].previous=previous
    result.seed_cells=list(cells)
    result.reconcile()
    return result


def fork_composite(state,budget):
    clone=copy.copy(state)
    if type(state) is BBState:clone.moments=fork_composite(state.moments,budget)
    elif type(state) is MomentsState:clone.budget=budget;clone.leaves=CellOverlay(state.leaves,budget)
    elif type(state) is MACDState:
        from packages.runtime.rollback import AppendOverlay
        clone.budget=budget;clone.parts=[copy.copy(s) for s in state.parts];clone.seed_cells=list(state.seed_cells)
        if state.version<5:
            for copied,original in zip(clone.parts,state.parts,strict=True):copied.pending=AppendOverlay(original.pending)
    else:raise ValueError('Unregistered composite state.')
    return clone
