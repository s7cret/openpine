"""Run-local, bounded heap with reference roots and copy-on-write previews.

References contain a closed integer identity, never a Python container.
Arrays may point to flat scalar records; recursive edges are not admitted.
"""

import math
from collections import Counter
from dataclasses import dataclass

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.arrays import MAX_LENGTH, MAX_OBJECTS, MAX_CELLS, MAX_STRING_BYTES


@dataclass(frozen=True)
class ArrayRef:
    object_id: int
    element: str


@dataclass
class ArrayObject:
    element: str
    items: list
    string_bytes: int


@dataclass(frozen=True)
class ObjectRef:
    object_id: int
    type_id: str


@dataclass
class RecordObject(ArrayObject):
    initial_items: tuple = ()


def is_reference(value):return type(value) in (ArrayRef,ObjectRef)


def string_bytes(value):
    return len(value.encode('utf-8')) if type(value) is str else 0


class ArrayHeap:
    def __init__(self, version, account=None,object_types=()):
        self.version = version
        self.account=account;self.allocations={};self.borrowed={};self.owner='arrays';self.metadata=None
        self.objects, self.references, self.roots = {}, {}, Counter()
        self.garbage, self.owned = set(), set()
        self.next_id, self.cells, self.bytes = 1, 0, 0
        self.object_types={t.type_id:t for t in object_types}
        self.intrabar_dirty=set()
        self.initial_records=set()

    def error(self, code, message, node):
        raise OpenPineError(Diagnostic('RUNTIME_ERROR', code, message, getattr(node, 'span', None),
            'rule.arrays', self.version))

    def charge(self, count, evaluation, node):
        for _ in range(count):evaluation.work.charge(node, evaluation.cancelled)

    def capacity(self, objects, cells, size, byte_count, node):
        if size > MAX_LENGTH or objects > MAX_OBJECTS or cells > MAX_CELLS or byte_count > MAX_STRING_BYTES:
            self.error('ARRAY_STORAGE', 'Array object/element/string storage budget exceeded.', node)

    def sync(self,key,size,byte_count,initial=None,*,element=None):
        if self.account is None:return
        from packages.contracts.memory import charge
        from packages.runtime.history_memory import Allocation
        label=self.owner+' / object '+str(key)
        allocation=self.allocations.get(key)
        if initial is None:initial=getattr(self.objects.get(key),'initial_items',())
        extra_bytes=sum(map(string_bytes,initial))
        from packages.contracts.objects import is_object
        existing=self.objects.get(key)
        cell_kind='reference' if is_object(element) or type(existing) is ArrayObject and is_object(existing.element) else 'array_cell'
        total=charge('array_object')+charge(cell_kind,size)+charge('array_cell',len(initial))+4*(byte_count+extra_bytes)
        self.account.check(max(0,total-(allocation.bytes[0] if allocation else 0)),label,'array')
        if allocation is None:
            allocation=self.allocations[key]=Allocation(self.account);allocation.label=label
        label=allocation.label
        allocation.resize(1,label,'array_object')
        if cell_kind=='reference':allocation.resize(size,label,'reference');allocation.resize(len(initial),label,'array_cell')
        else:allocation.resize(size+len(initial),label,'array_cell')
        allocation.resize(4*(byte_count+extra_bytes),label,'byte')

    def close(self):
        for allocation in self.allocations.values():allocation.close()
        self.allocations.clear()
        self.borrowed.clear()
        if self.metadata:self.metadata.close()

    def create(self, element, items, byte_count, evaluation, node):
        self.capacity(len(self.objects) + 1, self.cells + len(items), len(items), self.bytes + byte_count, node)
        if self.next_id >= 2**63:self.error('ARRAY_STORAGE', 'Array identity space exhausted.', node)
        from packages.contracts.objects import is_object
        if is_object(element):
            for value in items:
                if value is not None and (type(value) is not ObjectRef or value.type_id!=element):self.error('ARRAY_REFERENCE','Array element nominal type differs.',node)
                if value is not None:self.record(value,node)
        self.sync(self.next_id,len(items),byte_count,element=element)
        key = self.next_id;self.next_id += 1
        self.objects[key] = ArrayObject(element, items, byte_count)
        if is_object(element):
            for value in items:
                self.retain(value)
        self.references[key] = 0;self.garbage.add(key);self.owned.add(key)
        self.cells += len(items);self.bytes += byte_count
        return ArrayRef(key, element)

    def object(self, reference, node):
        if reference is None:self.error('ARRAY_NA', 'Cannot use a missing array reference.', node)
        if type(reference) is not ArrayRef or reference.object_id not in self.objects:
            self.error('ARRAY_REFERENCE', 'Array reference does not belong to this evaluation.', node)
        result = self.objects[reference.object_id]
        if type(result) is not ArrayObject or result.element != reference.element:self.error('ARRAY_REFERENCE', 'Array element type differs.', node)
        return result

    def create_record(self,type_id,items,evaluation,node):
        byte_count=sum(map(string_bytes,items))
        self.capacity(len(self.objects)+1,self.cells+len(items),len(items),self.bytes+byte_count,node)
        if type_id not in self.object_types or len(items)!=len(self.object_types[type_id].fields):
            self.error('UDT_TYPE','Object layout differs from its declared type.',node)
        if self.next_id>=2**63:self.error('UDT_STORAGE','Object identity space exhausted.',node)
        initial=tuple(items)
        self.sync(self.next_id,len(items),byte_count,initial);key=self.next_id;self.next_id+=1
        self.objects[key]=RecordObject(type_id,items,byte_count,initial);self.references[key]=0
        self.initial_records.add(key)
        self.garbage.add(key);self.owned.add(key);self.cells+=len(items);self.bytes+=byte_count
        return ObjectRef(key,type_id)

    def record(self,reference,node):
        if reference is None:self.error('UDT_NA','Cannot access fields of a missing object.',node)
        obj=self.objects.get(getattr(reference,'object_id',None))
        if type(reference) is not ObjectRef or type(obj) is not RecordObject or obj.element!=reference.type_id:
            self.error('UDT_REFERENCE','Object reference does not belong to this evaluation/type.',node)
        return obj

    def writable(self, reference, evaluation, node):
        result = self.record(reference,node) if type(reference) is ObjectRef else self.object(reference, node)
        if reference.object_id not in self.owned:
            self.charge(len(result.items), evaluation, node)
            self.sync(reference.object_id,len(result.items),result.string_bytes)
            result = (RecordObject(result.element,result.items.copy(),result.string_bytes,result.initial_items)
                if type(result) is RecordObject else ArrayObject(result.element,result.items.copy(),result.string_bytes))
            self.objects[reference.object_id] = result;self.owned.add(reference.object_id)
            self.borrowed.pop(reference.object_id,None)
        return result

    def retain(self, value):
        if is_reference(value):
            if value.object_id not in self.objects:raise ValueError('Dangling array history/root.')
            self.references[value.object_id] += 1;self.garbage.discard(value.object_id)

    def release(self, value):
        if is_reference(value):
            key = value.object_id
            if self.references.get(key, 0) <= 0:raise ValueError('Invalid array reference count.')
            self.references[key] -= 1
            if self.references[key] == 0:self.garbage.add(key)

    def collect(self, persistent, evaluation, node):
        roots = Counter(v for v in persistent.values() if is_reference(v))
        for value, count in (roots - self.roots).items():
            for _ in range(count):self.retain(value)
        for value, count in (self.roots - roots).items():
            for _ in range(count):self.release(value)
        self.roots = roots
        pending=list(self.garbage);seen=set()
        while pending:
            key=pending.pop()
            if key not in self.garbage or key in seen:continue
            seen.add(key)
            obj = self.objects[key]
            if evaluation.is_preview and key in self.intrabar_dirty:continue
            self.charge(len(obj.items) + 1, evaluation, node)
            self.cells -= len(obj.items);self.bytes -= obj.string_bytes
            allocation=self.allocations.pop(key,None)
            if allocation:allocation.close()
            from .reference_graph import children
            for ref in children(obj):
                self.release(ref)
                if ref is not None and ref.object_id in self.garbage:pending.append(ref.object_id)
            del self.objects[key], self.references[key]
            self.owned.discard(key);self.garbage.remove(key)
            self.borrowed.pop(key,None)
            self.initial_records.discard(key)

    def confirm_records(self):
        for key in self.initial_records:
            obj=self.objects.get(key)
            if type(obj) is RecordObject and obj.initial_items:
                obj.initial_items=();self.sync(key,len(obj.items),obj.string_bytes)
        self.initial_records.clear()
        self.intrabar_dirty.clear()

    def fork(self):
        other = ArrayHeap(self.version,self.account,tuple(self.object_types.values()));other.owner=self.owner+' preview'
        other.borrowed={key:self.allocations.get(key,self.borrowed.get(key)) for key in self.objects}
        if self.account:
            from packages.runtime.history_memory import Allocation
            other.metadata=Allocation(self.account);other.metadata.reserve(len(self.objects),other.owner+' root maps','array_object')
        other.objects = self.objects.copy();other.references = self.references.copy()
        other.initial_records=self.initial_records.copy()
        other.roots = self.roots.copy();other.garbage = self.garbage.copy()
        other.next_id, other.cells, other.bytes = self.next_id, self.cells, self.bytes
        return other

    def index(self, value, obj, node):
        if type(value) is not int:self.error('ARRAY_INDEX', 'Array index must be a defined integer.', node)
        if value < 0 and self.version == 6:value += len(obj.items)
        if not 0 <= value < len(obj.items):self.error('ARRAY_INDEX', 'Array index is out of bounds.', node)
        return value

    def evaluate(self, evaluation, node):
        self.owner=evaluation.storage_owner+' / arrays / '+str(evaluation.context_id)
        args = [None] * len(node.arguments)
        for position in node.evaluation_order:args[position] = evaluation.evaluate(node.arguments[position].expression)
        op, element = node.operation, node.element
        if op == 'is_na':return args[0] is None
        if op == 'new':
            size, value = args
            if type(size) is not int or size < 0:self.error('ARRAY_SIZE', 'Array size must be a defined nonnegative integer.', node)
            byte_count = size * string_bytes(value)
            self.capacity(len(self.objects) + 1, self.cells + size, size, self.bytes + byte_count, node)
            self.charge(size, evaluation, node)
            return self.create(element, [value] * size, byte_count, evaluation, node)
        if op == 'from':
            return self.create(element, args, sum(map(string_bytes, args)), evaluation, node)
        reference = args[0];obj = self.object(reference, node)
        if op == 'size':return len(obj.items)
        if op == 'get':return obj.items[self.index(args[1], obj, node)]
        if op == 'copy':
            self.capacity(len(self.objects) + 1, self.cells + len(obj.items), len(obj.items), self.bytes + obj.string_bytes, node)
            self.charge(len(obj.items), evaluation, node)
            return self.create(element, obj.items.copy(), obj.string_bytes, evaluation, node)
        if op in ('sum', 'avg', 'min', 'max'):
            self.charge(len(obj.items), evaluation, node)
            values = [value for value in obj.items if value is not None]
            if not values:return None
            try:
                if op == 'min':return min(values)
                if op == 'max':return max(values)
                total = math.fsum(values) if element == 'float' else sum(values)
                return total / len(values) if op == 'avg' else total
            except (OverflowError, ValueError):self.error('ARRAY_NUMERIC', 'Array aggregate exceeds the numeric range.', node)
        if op == 'set':
            index = self.index(args[1], obj, node)
            delta = string_bytes(args[2]) - string_bytes(obj.items[index])
            self.capacity(len(self.objects), self.cells, len(obj.items), self.bytes + delta, node)
            obj = self.writable(reference, evaluation, node)
            self.sync(reference.object_id,len(obj.items),obj.string_bytes+delta)
            from packages.contracts.objects import is_object
            if is_object(element):self.retain(args[2]);self.release(obj.items[index])
            obj.items[index] = args[2];obj.string_bytes += delta;self.bytes += delta
        elif op in ('push', 'unshift'):
            delta = string_bytes(args[1])
            self.capacity(len(self.objects), self.cells + 1, len(obj.items) + 1, self.bytes + delta, node)
            if op == 'unshift':self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            self.sync(reference.object_id,len(obj.items)+1,obj.string_bytes+delta)
            from packages.contracts.objects import is_object
            if is_object(element):self.retain(args[1])
            if op == 'push':obj.items.append(args[1])
            else:obj.items.insert(0, args[1])
            obj.string_bytes += delta;self.bytes += delta;self.cells += 1
        elif op in ('pop', 'shift'):
            if not obj.items:self.error('ARRAY_EMPTY', 'Cannot remove an element from an empty array.', node)
            if op == 'shift':self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            value = obj.items.pop() if op == 'pop' else obj.items.pop(0)
            from packages.contracts.objects import is_object
            if is_object(element):self.release(value)
            delta = string_bytes(value);self.cells -= 1;self.bytes -= delta;obj.string_bytes -= delta
            self.sync(reference.object_id,len(obj.items),obj.string_bytes)
            return value
        elif op == 'clear':
            self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            self.cells -= len(obj.items);self.bytes -= obj.string_bytes
            from .reference_graph import children
            for ref in children(obj):self.release(ref)
            obj.items.clear();obj.string_bytes = 0
            self.sync(reference.object_id,0,0)
        return None
