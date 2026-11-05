"""Shared-heap points and coordinate snapshots for drawing operations."""
from packages.contracts.chart_points import BASE, CONSTRUCTORS, POINT_SETTERS, PRICE
from packages.contracts.drawings import PLURALS
from .drawings import coordinate_mode


def construct(evaluation, node, arguments):
    heap = evaluation.heap
    heap.owner = evaluation.storage_owner + ' / chart points / ' + str(evaluation.context_id)
    match node.operation:
        case 'chart.point.now':
            items = [evaluation.frame.time, evaluation.frame.bar_index, arguments[0]]
        case 'chart.point.from_index':
            items = [None, *arguments]
        case 'chart.point.from_time':
            items = [arguments[0], None, arguments[1]]
        case 'chart.point.copy':
            items = heap.record(arguments[0], node).items.copy()
        case _:
            items = list(arguments)
    heap.charge(len(items), evaluation, node)
    return heap.create_record(BASE, items, evaluation, node)


def evaluate(evaluation, node, arguments):
    operation = node.operation
    if operation in CONSTRUCTORS:
        return construct(evaluation, node, arguments)

    kind = operation.split('.', 1)[0]
    store = getattr(evaluation, PLURALS[kind])

    def coordinates(ref, mode):
        items = evaluation.heap.record(ref, node).items
        return items[0 if mode == 'bar_time' else 1], items[PRICE]

    if operation in POINT_SETTERS:
        identity, point = arguments
        record = store.live.get(identity)
        if record is None:
            return None
        return store.apply(POINT_SETTERS[operation], (identity, *coordinates(point, record.get('xloc', 'bar_index'))))

    if kind == 'label':
        point, label_text, xloc = arguments
        values = (*coordinates(point, coordinate_mode(xloc)), label_text, xloc)
    elif kind == 'line':
        first, second, xloc, extend = arguments
        mode = coordinate_mode(xloc)
        values = (*coordinates(first, mode), *coordinates(second, mode), xloc, extend)
    else:
        first, second, border, xloc, background = arguments
        mode = coordinate_mode(xloc)
        values = (*coordinates(first, mode), *coordinates(second, mode), border, background, xloc)
    return store.apply(kind + '.new_xloc', values)
