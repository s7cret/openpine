"""Numeric matrix arithmetic with transactional result buffers."""
from fractions import Fraction
from operator import add, mul, sub

from .matrices import array, object as matrix_object, publish


def exact(value):
    return Fraction(value) if type(value) is float else value


def product(e, node, left_items, right_items, height, rows, columns, finish):
    if columns == 0:
        return []
    values = []
    for i in range(height):
        start = i * rows
        for j in range(columns):
            total = 0
            for k in range(rows):
                e.work.charge(node,e.cancelled)
                x, y = left_items[start+k], right_items[k*columns+j]
                if x is None or y is None:
                    total = None
                    break
                total += exact(x) * exact(y)
            values.append(None if total is None else finish(total))
    return values


def power(e, node, left, exponent, finish):
    from .history_memory import Allocation
    from .matrices import reserve

    size = left.rows
    if size != left.columns:
        e.heap.error('MATRIX_SHAPE', 'Matrix power requires a square matrix.', node)
    if type(exponent) is not int or exponent < 0:
        e.heap.error('MATRIX_POWER', 'Exponent must be a defined nonnegative integer.', node)

    def build():
        if exponent == 0:
            one, zero = (1, 0) if left.element == 'int' else (1.0, 0.0)
            return [one if i == j else zero for i in range(size) for j in range(size)]
        if exponent == 1:
            return left.items.copy()
        # One product buffer is reserved by publish; later multiplications also
        # keep the preceding result alive until its replacement is complete.
        scratch = Allocation(e.heap.account) if exponent > 2 and e.heap.account is not None else None
        try:
            if scratch:
                reserve(scratch,left.element,size*size,0,e.heap.owner+' / matrix power')
            values = left.items
            for bit in bin(exponent)[3:]:
                values = product(e,node,values,values,size,size,size,finish)
                if bit == '1':
                    values = product(e,node,values,left.items,size,size,size,finish)
            return values
        finally:
            if scratch:
                scratch.close()

    return publish(e,node,None,size,size,0,build)


def calculate(e, node, left, right):
    operand = node.arguments[1].expression.value_type.base
    result_element = node.value_type.base.split('.')[1]

    def finish(value):
        if result_element == 'int':
            if not -(2**63) <= value < 2**63:
                e.heap.error('MATRIX_NUMERIC', 'Matrix arithmetic exceeds signed int64.', node)
            return value
        try:
            return float(value)
        except OverflowError:
            e.heap.error('MATRIX_NUMERIC', 'Matrix arithmetic exceeds the numeric range.', node)

    def pair(x, y):
        if x is None or y is None:return None
        return finish(operation(exact(x), exact(y)))

    if node.operation == 'pow':
        return power(e,node,left,right,finish)
    operation = {'sum':add, 'diff':sub, 'mult':mul, 'kron':mul}[node.operation]
    if operand in ('int','float'):
        return publish(e,node,None,left.rows,left.columns,0,
                       lambda:[pair(x,right) for x in left.items],result_element=result_element)

    vector = operand.startswith('array.')
    if vector:
        right_items = e.heap.object(right,node).items
        rows, columns = len(right_items), 1
    else:
        matrix = matrix_object(e.heap,right,node)
        right_items, rows, columns = matrix.items, matrix.rows, matrix.columns

    if node.operation == 'kron':
        height, width = left.rows * rows, left.columns * columns
        def blocks():
            if not height or not width:
                return []
            return [pair(left.items[i*left.columns+j],right_items[k*columns+l])
                    for i in range(left.rows) for k in range(rows)
                    for j in range(left.columns) for l in range(columns)]
        return publish(e,node,None,height,width,0,blocks,result_element=result_element)

    if node.operation != 'mult':
        if (left.rows,left.columns) != (rows,columns):
            e.heap.error('MATRIX_SHAPE', 'Matrix addition and subtraction require equal shapes.', node)
        return publish(e,node,None,rows,columns,0,
                       lambda:[pair(x,y) for x,y in zip(left.items,right_items,strict=True)],
                       result_element=result_element)
    if left.columns != rows:
        e.heap.error('MATRIX_SHAPE', 'Inner product dimensions must match.', node)

    def build():
        return product(e,node,left.items,right_items,left.rows,rows,columns,finish)

    if vector:return array(e,node,result_element,left.rows,0,build)
    return publish(e,node,None,left.rows,columns,0,build,result_element=result_element)
