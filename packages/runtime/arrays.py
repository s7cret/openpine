"""Run-local, bounded heap with reference roots and copy-on-write previews.

References contain a closed integer identity, never a Python container. Only
primitive elements are admitted, so roots are flat and collection is exact.
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


def string_bytes(value):
    return len(value.encode('utf-8')) if type(value) is str else 0


class ArrayHeap:
    def __init__(self, version):
        self.version = version
        self.objects, self.references, self.roots = {}, {}, Counter()
        self.garbage, self.owned = set(), set()
        self.next_id, self.cells, self.bytes = 1, 0, 0

    def error(self, code, message, node):
        raise OpenPineError(Diagnostic('RUNTIME_ERROR', code, message, getattr(node, 'span', None),
            'rule.arrays', self.version))

    def charge(self, count, evaluation, node):
        for _ in range(count):evaluation.work.charge(node, evaluation.cancelled)

    def capacity(self, objects, cells, size, byte_count, node):
        if size > MAX_LENGTH or objects > MAX_OBJECTS or cells > MAX_CELLS or byte_count > MAX_STRING_BYTES:
            self.error('ARRAY_STORAGE', 'Array object/element/string storage budget exceeded.', node)

    def create(self, element, items, byte_count, evaluation, node):
        self.capacity(len(self.objects) + 1, self.cells + len(items), len(items), self.bytes + byte_count, node)
        if self.next_id >= 2**63:self.error('ARRAY_STORAGE', 'Array identity space exhausted.', node)
        key = self.next_id;self.next_id += 1
        self.objects[key] = ArrayObject(element, items, byte_count)
        self.references[key] = 0;self.garbage.add(key);self.owned.add(key)
        self.cells += len(items);self.bytes += byte_count
        return ArrayRef(key, element)

    def object(self, reference, node):
        if reference is None:self.error('ARRAY_NA', 'Cannot use a missing array reference.', node)
        if type(reference) is not ArrayRef or reference.object_id not in self.objects:
            self.error('ARRAY_REFERENCE', 'Array reference does not belong to this evaluation.', node)
        result = self.objects[reference.object_id]
        if result.element != reference.element:self.error('ARRAY_REFERENCE', 'Array element type differs.', node)
        return result

    def writable(self, reference, evaluation, node):
        result = self.object(reference, node)
        if reference.object_id not in self.owned:
            self.charge(len(result.items), evaluation, node)
            result = ArrayObject(result.element, result.items.copy(), result.string_bytes)
            self.objects[reference.object_id] = result;self.owned.add(reference.object_id)
        return result

    def retain(self, value):
        if type(value) is ArrayRef:
            if value.object_id not in self.objects:raise ValueError('Dangling array history/root.')
            self.references[value.object_id] += 1;self.garbage.discard(value.object_id)

    def release(self, value):
        if type(value) is ArrayRef:
            key = value.object_id
            if self.references.get(key, 0) <= 0:raise ValueError('Invalid array reference count.')
            self.references[key] -= 1
            if self.references[key] == 0:self.garbage.add(key)

    def collect(self, persistent, evaluation, node):
        roots = Counter(v for v in persistent.values() if type(v) is ArrayRef)
        for value, count in (roots - self.roots).items():
            for _ in range(count):self.retain(value)
        for value, count in (self.roots - roots).items():
            for _ in range(count):self.release(value)
        self.roots = roots
        for key in tuple(self.garbage):
            obj = self.objects[key]
            self.charge(len(obj.items) + 1, evaluation, node)
            self.cells -= len(obj.items);self.bytes -= obj.string_bytes
            del self.objects[key], self.references[key]
            self.owned.discard(key);self.garbage.remove(key)

    def fork(self):
        other = ArrayHeap(self.version)
        other.objects = self.objects.copy();other.references = self.references.copy()
        other.roots = self.roots.copy();other.garbage = self.garbage.copy()
        other.next_id, other.cells, other.bytes = self.next_id, self.cells, self.bytes
        return other

    def index(self, value, obj, node):
        if type(value) is not int:self.error('ARRAY_INDEX', 'Array index must be a defined integer.', node)
        if value < 0 and self.version == 6:value += len(obj.items)
        if not 0 <= value < len(obj.items):self.error('ARRAY_INDEX', 'Array index is out of bounds.', node)
        return value

    def evaluate(self, evaluation, node):
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
            obj.items[index] = args[2];obj.string_bytes += delta;self.bytes += delta
        elif op in ('push', 'unshift'):
            delta = string_bytes(args[1])
            self.capacity(len(self.objects), self.cells + 1, len(obj.items) + 1, self.bytes + delta, node)
            if op == 'unshift':self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            if op == 'push':obj.items.append(args[1])
            else:obj.items.insert(0, args[1])
            obj.string_bytes += delta;self.bytes += delta;self.cells += 1
        elif op in ('pop', 'shift'):
            if not obj.items:self.error('ARRAY_EMPTY', 'Cannot remove an element from an empty array.', node)
            if op == 'shift':self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            value = obj.items.pop() if op == 'pop' else obj.items.pop(0)
            delta = string_bytes(value);self.cells -= 1;self.bytes -= delta;obj.string_bytes -= delta
            return value
        elif op == 'clear':
            self.charge(len(obj.items), evaluation, node)
            obj = self.writable(reference, evaluation, node)
            self.cells -= len(obj.items);self.bytes -= obj.string_bytes
            obj.items.clear();obj.string_bytes = 0
        return None
