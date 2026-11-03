"""Persistent label event stream over the shared drawing journal."""
from packages.contracts.labels import validate_label
from .drawing_journal import DrawingJournal, drawing_events, mark_chunks


class LabelJournal(DrawingJournal):
    def __init__(self, objects):
        super().__init__(objects, 'label', validate_label)


def label_events(objects, publication, start, limit):
    return drawing_events(objects, publication, start, limit, 'label', validate_label)
