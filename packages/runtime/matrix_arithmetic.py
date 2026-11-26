"""Numeric matrix arithmetic with transactional result buffers."""
from fractions import Fraction
from operator import add, mul, sub

from .matrices import array, object as matrix_object, publish


def exact(value):
    return Fraction(value) if type(value) is float else value


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

    operation = {'sum':add, 'diff':sub, 'mult':mul}[node.operation]
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

    if node.operation != 'mult':
        if (left.rows,left.columns) != (rows,columns):
            e.heap.error('MATRIX_SHAPE', 'Matrix addition and subtraction require equal shapes.', node)
        return publish(e,node,None,rows,columns,0,
                       lambda:[pair(x,y) for x,y in zip(left.items,right_items,strict=True)],
                       result_element=result_element)
    if left.columns != rows:
        e.heap.error('MATRIX_SHAPE', 'Inner product dimensions must match.', node)

    def product():
        if columns == 0:
            return []
        values = []
        for i in range(left.rows):
            start = i * rows
            for j in range(columns):
                total = 0
                for k in range(rows):
                    e.work.charge(node,e.cancelled)
                    x, y = left.items[start+k], right_items[k*columns+j]
                    if x is None or y is None:
                        total = None
                        break
                    total += exact(x) * exact(y)
                values.append(None if total is None else finish(total))
        return values

    if vector:return array(e,node,result_element,left.rows,0,product)
    return publish(e,node,None,left.rows,columns,0,product,result_element=result_element)
