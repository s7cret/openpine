"""Persistent line event stream over the shared drawing journal."""
from packages.contracts.lines import validate_line
from .drawing_journal import DrawingJournal, drawing_events


class LineJournal(DrawingJournal):
    def __init__(self, objects):
        super().__init__(objects, 'line', validate_line)


def line_events(objects, publication, start, limit):
    return drawing_events(objects, publication, start, limit, 'line', validate_line)
