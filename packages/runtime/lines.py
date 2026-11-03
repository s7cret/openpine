"""Line records owned by a confirmed evaluation or preview branch."""
from packages.contracts.lines import validate_line
from .drawings import Drawings


class Lines(Drawings):
    kind = 'line'
    fields = ('x1', 'y1', 'x2', 'y2')
    updates = {'set_xy1': ('x1', 'y1'), 'set_xy2': ('x2', 'y2')}
    validate = staticmethod(validate_line)
    invalid_record = 'Line coordinates require bar indices and finite prices or na.'
