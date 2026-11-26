"""Transactional row operations on dense rectangular matrices."""
from packages.contracts.array_order import field_layout, valid_order
from .array_order import sorted_indices
from .history_memory import Allocation
from .matrices import index, object as matrix_object, publish, string_bytes


def transform(e, node, ref, matrix, args, defaults):
    rows, columns = matrix.rows, matrix.columns
    items = matrix.items
    if node.operation == 'concat':
        other = matrix_object(e.heap, args[1], node)
        if other.columns != columns:
            e.heap.error('MATRIX_SHAPE', 'Concatenation requires equal column counts.', node)
        return publish(e, node, ref, rows + other.rows, columns, matrix.string_bytes + other.string_bytes,
                       lambda: items + other.items)
    if node.operation == 'reverse':
        publish(e, node, ref, rows, columns, matrix.string_bytes, lambda: items[::-1])
        return None
    if node.operation == 'submatrix':
        fr, tr, fc, tc = args[1:]
        if 'to_row' in defaults:
            tr = rows
        if 'to_column' in defaults:
            tc = columns
        if any(type(n) is not int for n in (fr,tr,fc,tc)) or not 0 <= fr <= tr <= rows or not 0 <= fc <= tc <= columns:
            e.heap.error('MATRIX_INDEX', 'Invalid half-open submatrix bounds.', node)
        def positions():
            if fc < tc:
                for row in range(fr,tr):
                    yield from range(row * columns + fc, row * columns + tc)
        size = 0
        if matrix.string_bytes:
            e.heap.charge((tr-fr)*(tc-fc)+1,e,node)
            size = sum(string_bytes(items[i]) for i in positions())
        return publish(e,node,None,tr-fr,tc-fc,size,lambda:[items[i] for i in positions()])

    column = index(e.heap,args[1],columns,node)
    if not valid_order(args[2]):
        e.heap.error('MATRIX_ORDER','Expected a defined sort order.',node)
    field = field_layout(node.element,args[3],e.heap.object_types.values())[0] if len(args)==4 else None
    def ordered_rows():
        positions = sorted_indices(e.heap,e,node,matrix,field,args[2]=='descending',
                                   positions_in_buffer=range(column,len(items),columns))
        return [items[row*columns+j] for row in positions for j in range(columns)]
    temporary = Allocation(e.heap.account) if e.heap.account is not None else None
    try:
        if temporary:
            temporary.reserve(4*rows,e.heap.owner+' ordering scratch','pair')
            temporary.reserve(rows,e.heap.owner+' ordering indices','index')
        publish(e,node,ref,rows,columns,matrix.string_bytes,ordered_rows)
    finally:
        if temporary:
            temporary.close()
    return None
