"""Changing signals: exact paired prefixes, reached-call lags and indexed ranges."""
import copy,math
from packages.contracts.dynamic_signals import HORIZON,NAMES
from packages.runtime.history import Ring
from packages.runtime.series import KernelError
from packages.runtime.series.technical import units,CellOverlay
from .dynamic_windows import length_value,room,finite,ratio,COUNT_LIMIT,capacity,bound

KINDS=tuple('swindow.'+n for n in NAMES)
KIND_OPERATIONS={k:(k,) for k in KINDS}

def require(ok):
 if not ok:raise ValueError('Invalid changing-signal checkpoint.')

def parameters(kind,args):
 length_value(args[-1])
 for x in args[:-1]:
  if x is not None:finite(x)
 if kind=='swindow.vwma' and args[0] is not None and args[0]<0:raise KernelError('SIGNAL_VOLUME','VWMA volume must be nonnegative.','rule.signals.volume')

class SignalWindow:
 def __init__(self,budget,kind,version=6,horizon=HORIZON):
  self.budget,self.kind,self.version=budget,kind,version
  self.horizon=horizon
  self.calls=self.count=0;self.length=None;self.values=Ring(budget,horizon+1 if kind in ('swindow.mom','swindow.roc') else horizon,'pair' if kind=='swindow.vwma' else 'value')
  self.prefix=[];self.nodes=[]
  if kind=='swindow.vwma':
   budget.preflight(2,'exact');self.prefix=[Ring(budget,horizon+1,'exact'),Ring(budget,horizon+1,'exact')]
   for r in self.prefix:r.append(0)

 def high(self):return self.kind in ('swindow.highestbars','swindow.rising')
 def better(self,a,b):
  if a is None:return b
  if b is None:return a
  x,y=self.values.items[a],self.values.items[b]
  if x==y:return a if (self.count-1-a)%self.values.capacity<(self.count-1-b)%self.values.capacity else b
  return a if (x>y)==self.high() else b
 def winner(self,i):
  size=self.values.capacity
  return i-size if i>=size and self.values.items[i-size] is not None else None if i>=size else self.nodes[i]
 def initialize(self,count=1):
  size=capacity(count,self.horizon);old=len(self.nodes)
  if size<=old:return
  parts=(('value',size-old),('index',size-old));self.budget.preflight_many(parts)
  from packages.runtime.history_memory import Allocation
  scratch=Allocation(self.budget.allocation.account) if self.budget.allocation else None
  try:
   if scratch:
    scratch.reserve(old,self.budget.label+' resize','value');scratch.reserve(old,self.budget.label+' resize','index')
   self.budget.preflight_many(parts)
   items=[None]*size
   for i in range(min(self.count,old)):
    position=self.count-min(self.count,old)+i;items[position%size]=self.values.items[position%old]
   # Rebuild in a detached state; publish only after all resource checks.
   clone=copy.copy(self);clone.values=copy.copy(self.values);clone.values.items=items;clone.values.capacity=size;clone.nodes=[None]*size
   for i in range(size-1,0,-1):clone.nodes[i]=clone.better(clone.winner(i*2),clone.winner(i*2+1))
   self.budget.reserve_many(parts)
   if scratch and isinstance(self.nodes,CellOverlay):scratch.transfer(self.budget.allocation)
   self.values,self.nodes=clone.values,clone.nodes
  finally:
   if scratch:scratch.close()
 def interval(self,left,right):
  left+=self.values.capacity;right+=self.values.capacity;value=None
  while left<right:
   if left&1:value=self.better(value,self.winner(left));left+=1
   if right&1:right-=1;value=self.better(value,self.winner(right))
   left//=2;right//=2
  return value
 def query(self,n):
  if not self.count:return None
  n=min(n,self.count);a=(self.count-n)%self.values.capacity;b=self.count%self.values.capacity
  return self.interval(a,b) if a<b else self.better(self.interval(a,self.values.capacity),self.interval(0,b))
 def append_index(self,x):
  self.initialize(self.count+1)
  slot=self.count%self.values.capacity;self.values.items[slot]=x;self.count+=1;self.values.count=self.count
  i=(slot+self.values.capacity)//2
  while i:
   self.nodes[i]=self.better(self.winner(i*2),self.winner(i*2+1));i//=2

 def update(self,*args):
  parameters(self.kind,args)
  bound(args[-1],self.horizon)
  if self.calls>=COUNT_LIMIT:raise KernelError('WINDOW_COUNT','Changing signal count exceeds int64.','rule.dynamic-signals.count')
  n=args[-1];x=args[-2];volume=args[0] if self.kind=='swindow.vwma' else None
  if self.kind=='swindow.vwma':
   if x is not None and volume is not None:
    self.budget.preflight_many((('pair',2*(self.values.count<self.values.capacity)),('exact',sum(r.count<r.capacity for r in self.prefix))))
    if self.values.count<self.values.capacity:self.budget.reserve(kind='pair')
    self.values.append((x,volume));u,v=units(x),units(volume)
    self.prefix[0].append(self.prefix[0].ago(0)+u*v);self.prefix[1].append(self.prefix[1].ago(0)+v);self.count+=1
   self.calls+=1;self.length=n
   if self.count<n:return None
   a,b=[r.ago(0)-r.ago(n) for r in self.prefix]
   return ratio(a,b<<1074) if b else None
  if self.kind in ('swindow.mom','swindow.roc'):
   room(self.budget,self.values.count<self.values.capacity)
   previous=self.values.ago(n-1);self.values.append(x);self.count+=1;self.calls+=1;self.length=n
   if x is None or previous is None or self.kind=='swindow.roc' and previous==0:return None
   delta=units(x)-units(previous)
   return ratio(delta,1<<1074) if self.kind=='swindow.mom' else ratio(delta*100,units(previous))
  self.initialize();trend=self.kind in ('swindow.rising','swindow.falling')
  if trend:
   prior=self.query(n)
   result=(x>self.values.items[prior] if self.high() else x<self.values.items[prior]) if x is not None and self.count>=n and prior is not None else (False if self.version==6 else None)
   if x is not None:self.append_index(x)
  else:
   self.append_index(x);best=self.query(n)
   result=-((self.count-1-best)%self.values.capacity) if best is not None else None
  self.calls+=1;self.length=n;return result

TYPES=(SignalWindow,)

def kernel(operation):
 def run(args,context):
  parameters(operation,args)
  bound(args[-1],context.history_capacity)
  return context.state(operation,lambda:SignalWindow(context.budget,operation,context.language_version,context.history_capacity)).update(*args)
 return run

def export_window(s):
 indexed=s.kind not in ('swindow.vwma','swindow.mom','swindow.roc')
 value={'retention':s.horizon,'calls':s.calls,'count':s.count,'length':s.length,'values':[s.values.items[i] for i in range(min(s.count,s.horizon))] if indexed else [s.values.ago(i) for i in range(min(s.count,s.values.capacity)-1,-1,-1)]}
 if s.prefix:
  retained=min(s.count,s.horizon);value.update(anchors=[r.ago(retained) for r in s.prefix],totals=[r.ago(0) for r in s.prefix])
 return s.kind,value

def restore_window(kind,v,budget,updates,version,horizon=HORIZON):
 require(kind in KINDS and type(v) is dict)
 paired=kind=='swindow.vwma';lag=kind in ('swindow.mom','swindow.roc')
 require(v.keys()==({'calls','count','length','values','anchors','totals','retention'} if paired else {'calls','count','length','values','retention'}))
 require(type(v['retention']) is int and v['retention']==horizon)
 calls,count,n=v['calls'],v['count'],v['length']
 require(type(calls) is int and 1<=calls<=min(updates,COUNT_LIMIT) and type(count) is int and 0<=count<=calls and type(n) is int and 1<=n<=horizon)
 if lag or kind in ('swindow.highestbars','swindow.lowestbars'):require(count==calls)
 values=v['values'];cap=horizon+1 if lag else horizon
 require(type(values) is list and len(values)==min(count,cap))
 def number(x):return type(x) is float and math.isfinite(x)
 require(all(type(x) in (list,tuple) and len(x)==2 and all(number(y) for y in x) and x[1]>=0 for x in values) if paired else all(x is None and lag or x is None and kind in ('swindow.highestbars','swindow.lowestbars') or number(x) for x in values))
 s=SignalWindow(budget,kind,version,horizon);s.calls=calls;s.length=n
 if paired:
  anchors,totals=v['anchors'],v['totals'];require(type(anchors) is list and type(totals) is list and len(anchors)==len(totals)==2)
  before=count-len(values);maxu=units(float.fromhex('0x1.fffffffffffffp+1023'))
  for i in range(2):
   for k,x in ((before,anchors[i]),(count,totals[i])):require(type(x) is int and abs(x)<=k*(maxu if i else maxu*maxu) and (i==0 or x>=0))
  budget.reserve_many((('pair',len(values)*2),('exact',len(values)*2)))
  s.values.items=[None]*len(values);s.values.count=count
  for r in s.prefix:r.items=[None]*(len(values)+1);r.count=count+1
  current=list(anchors)
  for i in range(len(values)+1):
   if i:
    x,volume=values[i-1];current[0]+=units(x)*units(volume);current[1]+=units(volume);s.values.items[(before+i-1)%horizon]=(x,volume)
   for j,r in enumerate(s.prefix):r.items[(before+i)%r.capacity]=current[j]
  require(current==totals)
 elif lag:
  room(budget,len(values))
  for x in values:s.values.append(x)
  # append used chronological order; rotate to the original global counter.
  ordered=list(s.values.items);s.values.items=[None]*len(ordered)
  for i,x in enumerate(ordered):s.values.items[(count-len(ordered)+i)%cap]=x
  s.values.count=count
 else:
  s.initialize(count);s.count=count;s.values.items[:len(values)]=values;s.values.count=count
  for i in range(s.values.capacity-1,0,-1):s.nodes[i]=s.better(s.winner(i*2),s.winner(i*2+1))
 s.count=count
 return s

def fork_window(s,budget):
 from packages.runtime.rollback import RingOverlay
 clone=copy.copy(s);clone.budget=budget;clone.values=RingOverlay(s.values,budget)
 clone.prefix=[RingOverlay(r,budget) for r in s.prefix]
 if s.kind not in ('swindow.vwma','swindow.mom','swindow.roc'):
  clone.values=copy.copy(s.values);clone.values.items=CellOverlay(s.values.items,budget)
  clone.nodes=CellOverlay(s.nodes,budget) if s.nodes else []
 return clone
