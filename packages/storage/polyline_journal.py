"""Persistent create/delete events for polyline snapshots."""
from packages.contracts.polylines import validate_polyline
from .drawing_journal import DrawingJournal, drawing_events


class PolylineJournal(DrawingJournal):
    def __init__(self, objects):
        super().__init__(objects, 'polyline', validate_polyline)


def polyline_events(objects, publication, start, limit):
    return drawing_events(objects, publication, start, limit, 'polyline', validate_polyline)
