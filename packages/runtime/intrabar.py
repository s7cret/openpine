"""Bounded varip roots. Ordinary history and kernels remain on rollback branches."""
import copy
from collections import Counter
from packages.contracts.arrays import ARRAY_BASES
from packages.runtime.arrays import ArrayRef,ObjectRef,RecordObject,is_reference,string_bytes
from packages.contracts.objects import is_reference as reference_type
from packages.runtime.array_checkpoints import restore_heap,decode_reference
from packages.runtime.checkpoints import layout

class IntrabarMemory:
    def __init__(self):self.value=None;self._bindings=None;self.allocation=None

    def charge(self,base,objects,values):
        from packages.runtime.history_memory import Allocation
        account=base.budget.allocation.account if base.budget.allocation else None
        if account is None:return None
        allocation=Allocation(account);label='varip snapshot / '+str(base.context_id)
        try:
            allocation.reserve(len(objects),label,'array_object')
            allocation.reserve(sum(len(obj.items)+len(getattr(obj,'initial_items',())) for obj in objects),label,'array_cell')
            allocation.reserve(4*sum(obj.string_bytes+sum(map(string_bytes,getattr(obj,'initial_items',()))) for obj in objects),label,'byte')
            allocation.reserve(sum(type(v) is str for v in values.values()),label,'string')
            allocation.reserve(len(values),label,'value')
            return allocation
        except BaseException:allocation.close();raise

    def bindings(self,base):
        if self._bindings is None:self._bindings=layout(base.program)[1]
        return self._bindings

    def capture(self,current,base):
        if base.heap is not None and base.heap.object_types:
            from .object_intrabar import capture
            return capture(self,current,base)
        bindings=self.bindings(base)
        values={k:v for k,v in current.persistent.items() if k in bindings and bindings[k].mode=='varip'}
        refs=sorted({v for v in values.values() if type(v) is ArrayRef},key=lambda r:r.object_id)
        allocation=self.charge(base,[current.heap.objects[r.object_id] for r in refs],values)
        objects={};mapping={};next_id=base.heap.next_id if base.heap else 1
        for ref in refs:
            if ref.object_id in base.heap.objects:key=ref.object_id
            else:key=next_id;next_id+=1
            mapping[ref.object_id]=key;obj=current.heap.objects[ref.object_id]
            objects[key]={'id':key,'element':obj.element,'items':list(obj.items)}
        encoded={k:{'array_id':mapping[v.object_id]} if type(v) is ArrayRef else v for k,v in values.items()}
        rows=[objects[k] for k in sorted(objects)]
        self.value={'values':encoded,'heap':{'next_id':next_id,'cells':sum(len(o['items']) for o in rows),
            'string_bytes':sum(len(v.encode('utf-8')) for o in rows for v in o['items'] if type(v) is str),'objects':rows} if base.heap else None}
        if self.allocation:self.allocation.close()
        self.allocation=allocation
        if current.observer:
            import itertools
            for key,item in itertools.islice(values.items(),16):current.observer.record(current,'varip_carry',value=item,binding_id=key)

    def decode(self,base):
        from packages.runtime.arrays import ArrayHeap
        heap=ArrayHeap(base.program.identity.language_version,base.heap.account,getattr(base.program,'object_types',())) if base.heap else None
        try:return self._decode(base,heap)
        except BaseException:
            if heap:heap.close()
            raise

    def _decode(self,base,heap):
        if base.heap is not None and base.heap.object_types:
            from .object_intrabar import decode
            return decode(self,base,heap)
        from packages.runtime.engine import value_matches
        value=self.value
        if type(value) is not dict or value.keys()!={'values','heap'} or type(value['values']) is not dict:
            raise ValueError('Invalid intrabar variable state.')
        bindings=self.bindings(base);values={}
        heap=restore_heap(value['heap'],base.program.identity.language_version,heap=heap) if base.heap else None
        if not base.heap and value['heap'] is not None:raise ValueError('Unexpected intrabar array heap.')
        for key,raw in value['values'].items():
            binding=bindings.get(key)
            if binding is None or binding.mode!='varip':raise ValueError('Intrabar state contains an ordinary or foreign binding.')
            typ=binding.value_type
            decoded=decode_reference(raw,typ.base,heap) if typ.base in ARRAY_BASES else raw
            if not value_matches(decoded,typ,base.program.identity.language_version,missing_string=base.heap is not None):raise ValueError('Invalid intrabar value type.')
            values[key]=decoded
        if not {k for k in base.persistent if bindings[k].mode=='varip'}<=values.keys():raise ValueError('Intrabar state loses confirmed varip roots.')
        if heap:
            used={v.object_id for v in values.values() if type(v) is ArrayRef}
            new=sorted(used-base.heap.objects.keys())
            if (used!=heap.objects.keys() or new!=list(range(base.heap.next_id,base.heap.next_id+len(new)))
                    or heap.next_id!=base.heap.next_id+len(new)):
                raise ValueError('Intrabar heap identities differ from their confirmed allocation boundary.')
            for key in used&base.heap.objects.keys():
                if heap.objects[key].element!=base.heap.objects[key].element:raise ValueError('Intrabar heap changes an existing object type.')
            from packages.contracts.arrays import MAX_OBJECTS,MAX_CELLS,MAX_STRING_BYTES
            objects={**base.heap.objects,**heap.objects};references=base.heap.references.copy()
            roots=Counter(v for v in {**base.persistent,**values}.values() if type(v) is ArrayRef)
            for ref,n in (base.heap.roots-roots).items():references[ref.object_id]-=n
            for ref,n in (roots-base.heap.roots).items():references[ref.object_id]=references.get(ref.object_id,0)+n
            retained=[obj for key,obj in objects.items() if references.get(key,0)>0]
            if len(retained)>MAX_OBJECTS or sum(len(o.items) for o in retained)>MAX_CELLS or sum(o.string_bytes for o in retained)>MAX_STRING_BYTES:
                raise ValueError('Combined confirmed and intrabar heap exceeds its budget.')
        return values,heap

    def apply(self,base):
        if self.value is None:return
        values,partial=self.decode(base);persistent={**base.persistent,**values}
        if partial is not None:
            heap=base.heap
            for key,obj in partial.objects.items():
                old=heap.allocations.pop(key,None)
                if old:old.close()
                heap.borrowed.pop(key,None)
                if key in partial.allocations:heap.allocations[key]=partial.allocations.pop(key)
                heap.objects[key]=obj;heap.owned.add(key)
                if type(obj) is RecordObject:heap.intrabar_dirty.add(key)
                if type(obj) is RecordObject and obj.initial_items:heap.initial_records.add(key)
                if key not in heap.references:heap.references[key]=0;heap.garbage.add(key)
            roots=Counter(v for v in persistent.values() if is_reference(v))
            for v,n in (heap.roots-roots).items():
                for _ in range(n):heap.release(v)
            for v,n in (roots-heap.roots).items():
                for _ in range(n):heap.retain(v)
            heap.roots=roots;heap.next_id=partial.next_id
            for key in tuple(heap.garbage):
                old=heap.allocations.pop(key,None)
                if old:old.close()
                heap.borrowed.pop(key,None)
                del heap.objects[key],heap.references[key];heap.owned.discard(key);heap.garbage.remove(key)
                heap.initial_records.discard(key)
            heap.cells=sum(len(o.items) for o in heap.objects.values());heap.bytes=sum(o.string_bytes for o in heap.objects.values())
            heap.capacity(len(heap.objects),heap.cells,0,heap.bytes,base.program.statements[0])
        base.persistent=persistent

    def restore(self,value,base):
        candidate=IntrabarMemory();candidate.value=copy.deepcopy(value)
        if value is not None:
            values,heap=candidate.decode(base)
            try:candidate.allocation=candidate.charge(base,list(heap.objects.values()) if heap else [],values)
            finally:
                if heap:heap.close()
        if self.allocation:self.allocation.close()
        self.value,self.allocation=candidate.value,candidate.allocation

    def clear(self):
        self.value=None
        if self.allocation:self.allocation.close();self.allocation=None
