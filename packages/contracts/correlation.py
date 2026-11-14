"""Versioned correlation contracts and the logical cost of their windows."""

PEARSON = 'correlation.pearson'
RCI = 'correlation.rci'
EFFECTS = ('reads_history', 'updates_state')
MOMENTS = 5
LENGTH_INDEX = {PEARSON: 2, RCI: 1}
OPERATIONS = frozenset(LENGTH_INDEX)


def parameters(operation, version):
    if operation == RCI:
        return (('source', ('int', 'float'), 'series'), ('length', ('int',), 'simple'))
    names = ('x', 'y', 'z') if version < 3 else (
        ('source_a', 'source_b', 'length') if version < 5 else ('source1', 'source2', 'length'))
    return (*((name, ('int', 'float'), 'series') for name in names[:2]),
            (names[2], ('int',), 'series' if version >= 4 else 'simple'))
