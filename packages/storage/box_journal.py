"""Persistent box events over the shared drawing journal."""
from packages.contracts.boxes import validate_box
from .drawing_journal import DrawingJournal, drawing_events


class BoxJournal(DrawingJournal):
    def __init__(self, objects):
        super().__init__(objects, 'box', validate_box)


def box_events(objects, publication, start, limit):
    return drawing_events(objects, publication, start, limit, 'box', validate_box)
