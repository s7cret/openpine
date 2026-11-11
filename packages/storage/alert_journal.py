"""Content-addressed replay alert events ordered by input observation."""
from packages.contracts.alerts import FREQUENCIES, MAX_PER_EVENT, valid_message
from packages.contracts.realtime import integer
from .drawing_journal import EventJournal, journal_events


def validate_event(event):
    if (type(event) is not dict or event.keys() != {
            'ordinal', 'call_site_id', 'bar_index', 'bar_time', 'revision', 'phase',
            'kind', 'message', 'frequency'}
            or not integer(event['ordinal'], high=MAX_PER_EVENT-1)
            or not all(integer(event[name]) for name in ('bar_index', 'bar_time', 'revision'))
            or event['phase'] not in ('open', 'update', 'close')
            or event['kind'] not in ('alertcondition', 'alert')
            or type(event['call_site_id']) is not str or not 0 < len(event['call_site_id']) <= 512
            or not valid_message(event['message'])
            or (event['frequency'] is not None or event['phase'] != 'close') and event['kind'] == 'alertcondition'
            or event['kind'] == 'alert' and event['frequency'] not in FREQUENCIES):
        raise ValueError('Invalid replay alert event.')


class AlertJournal(EventJournal):
    def __init__(self, objects):
        super().__init__(objects, 'alert', validate_event, position='sequence', event_limit=MAX_PER_EVENT)


def alert_events(objects, publication, start, limit):
    return journal_events(objects, publication, start, limit, 'alert', 'sequence', validate_event)
