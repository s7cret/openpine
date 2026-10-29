"""Read-only bounded observations of the evaluator, requests and broker."""
from dataclasses import asdict,is_dataclass
from decimal import Decimal
import math,itertools
from packages.contracts.trace import options

def value(evaluation,item,limit=8,depth=0):
    if item is None:return {'type':'na'}
    if type(item) is float and not math.isfinite(item):return {'type':'nonfinite','value':repr(item)}
    if type(item) in (bool,int,float):return {'type':type(item).__name__,'value':item}
    if type(item) is str:return {'type':'string','value':item[:256],'length':len(item),'truncated':len(item)>256}
    if isinstance(item,Decimal):return {'type':'decimal','value':str(item)[:128]}
    from packages.runtime.arrays import ArrayRef
    if isinstance(item,ArrayRef):
        obj=evaluation.heap.objects.get(item.object_id) if evaluation.heap else None
        data=getattr(obj,'items',())
        return {'type':'array','object_id':item.object_id,'element':item.element,'length':len(data),
            'preview':[value(evaluation,x,limit,depth+1) for x in list(data[:limit])] if depth<2 else []}
    if type(item) in (list,tuple):return {'type':'tuple','length':len(item),'items':[value(evaluation,x,limit,depth+1) for x in item[:limit]] if depth<2 else []}
    return {'type':type(item).__name__}

class Observer:
    def __init__(self,writer,selected):
        self.writer=writer;self.options=options(selected);self.scope={'primary_bar':-1};self.stack=[]
        self.invocation=writer.invocations;self.branches=writer.branches;self.pass_phase=None;self.pass_ordinal=None
        self.site_nodes=None
        self.disabled=False
    def cursor(self,**scope):
        self.scope=scope;self.pass_phase=self.pass_ordinal=None
        if scope['primary_bar']>=self.options['end_bar'] or self.writer.stopped:self.disabled=True
    def seed_broker(self,simulation):
        if simulation is None:return
        broker=simulation.broker
        for name in ('pending','entries','protections'):
            collection=getattr(broker,name,())
            items=collection.values() if type(collection) is dict else collection
            for order in items:
                intent=getattr(order,'intent',None);site=getattr(intent,'call_site_id',None);key=getattr(order,'order_id',None)
                if site and key:self.writer.order_sites[key]=site
    def allowed(self,node=None):
        p=self.options;bar=self.scope['primary_bar']
        if self.disabled or self.writer.stopped or not p['start_bar']<=bar<p['end_bar']:return False
        if node is None:return True
        span=getattr(node,'span',None);site=getattr(node,'call_site_id',None)
        return (not p['lines'] or span and span.line in p['lines']) and (not p['sites'] or site in p['sites'])
    def begin(self,evaluation,node):
        if self.disabled or self.writer.stopped:evaluation.observe(None);return None
        if not self.allowed(node):return None
        self.invocation+=1;token=(self.invocation,self.stack[-1] if self.stack else None,len(evaluation.order_intents))
        self.writer.invocations=self.invocation
        self.stack.append(token[0]);return token
    def end(self,evaluation,node,token,result,error):
        if token is None:return
        if self.stack:self.stack.pop()
        self.record(evaluation,'error' if error else 'expression',node,value=result,invocation_id=token[0],parent_invocation_id=token[1],
            **({'error':{'type':type(error).__name__,'message':str(error)[:512]}} if error else {}))
        if not error and getattr(node,'operation','').startswith('simulation.'):
            for intent in evaluation.order_intents[token[2]:token[2]+16]:self.record(evaluation,'order_intent',node,intent=asdict(intent),invocation_id=token[0])
    def fork(self,evaluation):
        if not self.allowed():return 0
        self.branches+=1
        self.writer.branches=self.branches
        self.record(evaluation,'branch_fork',child_branch=self.branches)
        return self.branches
    def record(self,evaluation,kind,node=None,**fields):
        broker=fields.get('broker_event')
        if broker:
            if self.writer.stopped or self.scope['primary_bar']>=self.options['end_bar']:return
            raw=broker.get('value',{});raw=raw if type(raw) is dict else {};intent=raw.get('intent',{})
            site=raw.get('call_site_id') or intent.get('call_site_id');order=raw.get('order_id')
            if site and order:
                self.writer.order_sites[order]=site
                if len(self.writer.order_sites)>128:self.writer.order_sites.pop(next(iter(self.writer.order_sites)))
            site=site or self.writer.order_sites.get(order)
            if self.site_nodes is None:
                from packages.contracts.context_ir import ir_nodes
                self.site_nodes={n.call_site_id:n for n in ir_nodes(evaluation.program.statements) if getattr(n,'operation','').startswith('simulation.')}
            if site:node=self.site_nodes.get(site)
        if not self.allowed(node):return
        frame=evaluation.frame;span=getattr(node,'span',None)
        row={'kind':kind,**self.scope,'context_id':frame.context_id if frame else evaluation.context_id,
            'context_bar':frame.bar_index if frame else evaluation.count,'bar_time':frame.time if frame else None,
            'branch_id':evaluation.observation_branch,'phase':evaluation.environment_phase,'pass_phase':self.pass_phase,'pass_ordinal':self.pass_ordinal,
            'node_kind':getattr(node,'kind',None),'site':getattr(node,'call_site_id',None),
            'operation':getattr(node,'operation',None),'span':asdict(span) if span else None,**fields}
        row.update(operator=getattr(node,'operator',None),function_id=getattr(node,'function_id',None))
        if getattr(node,'binding_id',None):row['binding_id']=node.binding_id
        if 'value' in fields:row['value']=value(evaluation,fields['value'],self.options['array_preview'])
        if broker:row.update(context_bar=broker['bar_index'],bar_time=broker['time'])
        if node is not None and hasattr(node,'value_type'):
            row['ir_type']=asdict(node.value_type)
            if kind=='expression' and fields.get('value') is None and getattr(node.value_type,'base',None)=='void':row['value']={'type':'void'}
        if kind=='expression' and row['site'] and frame:
            state=next((s for key,s in itertools.islice(evaluation.states.items(),64) if key[:2]==(row['site'],frame.context_id)),None)
            if state is not None:row['kernel_state']={name:value(evaluation,getattr(state,name)) for name in ('count','length','period','seeded','total') if type(getattr(state,name,None)) in (bool,int,float)}
        self.writer.emit(row)
