"""Box records owned by a confirmed evaluation or preview branch."""
from packages.contracts.boxes import COORDINATES, validate_box
from .drawings import Drawings


class Boxes(Drawings):
    kind = 'box'
    fields = (*COORDINATES, 'border_color', 'bgcolor')
    updates = {'set_lefttop': ('left', 'top'), 'set_rightbottom': ('right', 'bottom'),
               'set_border_color': ('border_color',), 'set_bgcolor': ('bgcolor',)}
    validate = staticmethod(validate_box)
    invalid_record = 'Box coordinates require bar indices and finite prices or na, with RGBA colors or na.'
