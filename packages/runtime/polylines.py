"""Bounded snapshots of ordered chart-point coordinates."""
from packages.contracts.chart_points import PRICE
from packages.contracts.drawings import LIMIT
from packages.contracts.polylines import MAX_BAR_VERTICES, MAX_LIVE_VERTICES, MAX_VERTICES, STYLED_CONSTRUCTOR, validate_polyline
from packages.runtime.series import KernelError
from .drawings import Drawings, coordinate_mode
from .lines import stroke_value


class Polylines(Drawings):
    kind = 'polyline'
    validate = staticmethod(validate_polyline)
    invalid_record = 'Polyline vertices require finite prices, integer X coordinates and a valid contour.'

    @classmethod
    def restore(cls, state, completed, *, capacity=LIMIT):
        store = super().restore(state, completed, capacity=capacity)
        if sum(len(row['vertices']) for row in store.live.values()) > MAX_LIVE_VERTICES:
            raise ValueError('Polyline checkpoint exceeds the live vertex limit.')
        for row in store.live.values():
            row['vertices'] = tuple(tuple(vertex) for vertex in row['vertices'])
        return store

    def reserve_vertices(self, count, closed):
        if type(closed) is not bool or not (3 if closed else 2) <= count <= MAX_VERTICES:
            raise KernelError('POLYLINE_VERTICES', 'Polyline requires 2–512 vertices, or 3–512 when closed.', 'rule.polylines')
        live = sum(len(row['vertices']) for row in self.live.values())
        if len(self.live) == self.capacity:
            live -= len(next(iter(self.live.values()))['vertices'])
        created = sum(len(event[self.kind]['vertices']) for event in self.pending if event['action'] == 'create')
        if live + count > MAX_LIVE_VERTICES or created + count > MAX_BAR_VERTICES:
            raise KernelError('POLYLINE_VERTEX_BUDGET', 'Polyline vertex budget exceeded.', 'rule.polylines')


def evaluate(evaluation, node, arguments):
    store = evaluation.polylines
    if node.operation == 'polyline.delete':
        return store.apply(node.operation, arguments)
    ref, _, closed, xloc, line_color, fill_color, *appearance = arguments
    style = {}
    if node.operation == STYLED_CONSTRUCTOR:
        line_style, line_width, *appearance = appearance
        style = dict(line_style=stroke_value('style', line_style), line_width=stroke_value('width', line_width))
    items = evaluation.heap.object(ref, node).items
    store.reserve_vertices(len(items), closed)
    mode = coordinate_mode(xloc)
    axis = 0 if mode == 'bar_time' else 1
    evaluation.heap.charge(len(items), evaluation, node)
    vertices = []
    for ref in items:
        if ref is None:
            raise KernelError('POLYLINE_VALUE', store.invalid_record, 'rule.polylines')
        fields = evaluation.heap.record(ref, node).items
        vertices.append((fields[axis], fields[PRICE]))
    record = dict(vertices=tuple(vertices), closed=closed, xloc=mode, line_color=line_color, fill_color=fill_color, **style)
    if appearance and appearance[0] is True:
        record['force_overlay'] = True
    return store.create(record)
