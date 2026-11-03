"""Label records owned by a confirmed evaluation or preview branch."""
from packages.contracts.labels import TEXT_LIMIT, validate_label
from .drawings import Drawings


class Labels(Drawings):
    kind = 'label'
    fields = ('x', 'y', 'text')
    updates = {'set_xy': ('x', 'y'), 'set_text': ('text',)}
    validate = staticmethod(validate_label)
    invalid_record = f'Label text requires a string up to {TEXT_LIMIT} characters.'
