"""Content-addressed replay alert events ordered by input observation."""
import re

from packages.contracts.alerts import FREQUENCIES, MAX_PER_EVENT, ORDER_FILL, valid_message
from packages.contracts.realtime import integer
from .drawing_journal import EventJournal, journal_events


def broker_identifier(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def validate_event(event):
    fill = type(event) is dict and event.get('kind') == ORDER_FILL
    keys = {
            'ordinal', 'call_site_id', 'bar_index', 'bar_time', 'revision', 'phase',
            'kind', 'message', 'frequency'}
    if fill:
        keys.update(('order_id', 'fill_id'))
    if (type(event) is not dict or event.keys() != keys
            or not integer(event['ordinal'], high=MAX_PER_EVENT-1)
            or not all(integer(event[name]) for name in ('bar_index', 'bar_time', 'revision'))
            or event['phase'] not in ('open', 'update', 'close')
            or event['kind'] not in ('alertcondition', 'alert', ORDER_FILL)
            or type(event['call_site_id']) is not str or not 0 < len(event['call_site_id']) <= 512
            or not valid_message(event['message'])
            or (event['frequency'] is not None or event['phase'] != 'close') and event['kind'] == 'alertcondition'
            or event['kind'] == 'alert' and event['frequency'] not in FREQUENCIES
            or fill and (event['frequency'] is not None
                         or not all(broker_identifier(event[key]) for key in ('order_id', 'fill_id')))):
        raise ValueError('Invalid replay alert event.')


class AlertJournal(EventJournal):
    def __init__(self, objects):
        super().__init__(objects, 'alert', validate_event, position='sequence', event_limit=MAX_PER_EVENT)


def alert_events(objects, publication, start, limit):
    return journal_events(objects, publication, start, limit, 'alert', 'sequence', validate_event)
