"""Bounded varip roots. Ordinary history and kernels remain on rollback branches."""
import copy
from collections import Counter
from packages.contracts.arrays import ARRAY_BASES
from packages.runtime.arrays import ArrayRef
from packages.runtime.array_checkpoints import restore_heap,decode_reference
from packages.runtime.checkpoints import layout

class IntrabarMemory:
    def __init__(self):self.value=None;self._bindings=None

    def bindings(self,base):
        if self._bindings is None:self._bindings=layout(base.program)[1]
        return self._bindings

    def capture(self,current,base):
        bindings=self.bindings(base)
        values={k:v for k,v in current.persistent.items() if k in bindings and bindings[k].mode=='varip'}
        objects={};mapping={};next_id=base.heap.next_id if base.heap else 1
        for ref in sorted({v for v in values.values() if type(v) is ArrayRef},key=lambda r:r.object_id):
            if ref.object_id in base.heap.objects:key=ref.object_id
            else:key=next_id;next_id+=1
            mapping[ref.object_id]=key;obj=current.heap.objects[ref.object_id]
            objects[key]={'id':key,'element':obj.element,'items':list(obj.items)}
        encoded={k:{'array_id':mapping[v.object_id]} if type(v) is ArrayRef else v for k,v in values.items()}
        rows=[objects[k] for k in sorted(objects)]
        self.value={'values':encoded,'heap':{'next_id':next_id,'cells':sum(len(o['items']) for o in rows),
            'string_bytes':sum(len(v.encode('utf-8')) for o in rows for v in o['items'] if type(v) is str),'objects':rows} if base.heap else None}

    def decode(self,base):
        from packages.runtime.engine import value_matches
        value=self.value
        if type(value) is not dict or value.keys()!={'values','heap'} or type(value['values']) is not dict:
            raise ValueError('Invalid intrabar variable state.')
        bindings=self.bindings(base);values={}
        heap=restore_heap(value['heap'],base.program.identity.language_version) if base.heap else None
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
                heap.objects[key]=obj;heap.owned.add(key)
                if key not in heap.references:heap.references[key]=0;heap.garbage.add(key)
            roots=Counter(v for v in persistent.values() if type(v) is ArrayRef)
            for v,n in (heap.roots-roots).items():
                for _ in range(n):heap.release(v)
            for v,n in (roots-heap.roots).items():
                for _ in range(n):heap.retain(v)
            heap.roots=roots;heap.next_id=partial.next_id
            for key in tuple(heap.garbage):
                del heap.objects[key],heap.references[key];heap.owned.discard(key);heap.garbage.remove(key)
            heap.cells=sum(len(o.items) for o in heap.objects.values());heap.bytes=sum(o.string_bytes for o in heap.objects.values())
            heap.capacity(len(heap.objects),heap.cells,0,heap.bytes,base.program.statements[0])
        base.persistent=persistent

    def restore(self,value,base):
        self.value=copy.deepcopy(value)
        if value is not None:self.decode(base)

    def clear(self):self.value=None
