"""Replay alert frequency state follows observed events, including open bars."""
from packages.contracts.alerts import ALL, CALL, CLOSE, MAX_PER_EVENT, ONCE, definitions, nodes
from packages.contracts.trading import STRATEGY_DECLARATIONS

STATE = 'openpine.replay-alert-state.1'


class ReplayAlerts:
    def __init__(self, program, sink, *, every_tick):
        self.sites = {node.call_site_id: node.operation for node in nodes(program.statements)}
        self.strategy = program.statements[0].operation in STRATEGY_DECLARATIONS
        self.conditions = [] if self.strategy else definitions(program)
        self.every_tick = every_tick
        self.sink = sink
        self.seen = set()
        self.records = 0

    def observe(self, event, bar_index, intents):
        seen = set(self.seen)
        events = []
        for site, message, frequency in intents:
            condition = self.sites[site] != CALL
            if condition or self.strategy and not self.every_tick:
                frequency = CLOSE
            if frequency == CLOSE and event.kind != 'close':
                continue
            if frequency != ALL and site in seen:
                continue
            if frequency != ALL:
                seen.add(site)
            events.append({'ordinal': len(events), 'call_site_id': site, 'bar_index': bar_index,
                           'bar_time': event.time, 'revision': event.revision, 'phase': event.kind,
                           'kind': 'alertcondition' if condition else 'alert', 'message': message,
                           'frequency': None if condition else frequency})
        if len(events) > MAX_PER_EVENT:
            raise ValueError('Replay alert event budget exceeded.')
        if events and self.sink is not None:
            self.sink.emit(event.sequence, event.event_time, events)
        self.records += len(events)
        self.seen = set() if event.kind == 'close' else seen

    def status(self):
        return {'records': self.records, 'conditions': self.conditions}

    def checkpoint(self):
        return {'schema_version': STATE, 'records': self.records, 'seen': sorted(self.seen)}

    def restore(self, value, sequence, active):
        if (type(value) is not dict or value.keys() != {'schema_version', 'records', 'seen'}
                or value['schema_version'] != STATE or type(value['records']) is not int
                or not 0 <= value['records'] <= (sequence + 1) * MAX_PER_EVENT
                or type(value['seen']) is not list or len(value['seen']) > min(value['records'], len(self.sites))
                or any(type(site) is not str or self.sites.get(site) != CALL for site in value['seen'])
                or value['seen'] != sorted(set(value['seen'])) or value['seen'] and active is None):
            raise ValueError('Invalid replay alert frequency boundary.')
        self.records = value['records']
        self.seen = set(value['seen'])
