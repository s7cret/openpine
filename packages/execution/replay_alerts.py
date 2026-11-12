"""Replay alerts follow the actual observation and broker phase order."""
from copy import copy
from dataclasses import dataclass

from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.alerts import ALL, CALL, CLOSE, MAX_PER_EVENT, ORDER_FILL, definitions, nodes, valid_message
from packages.contracts.fill_alerts import (
    DISABLE, EXIT_MESSAGES, EXTENDED_OPERATIONS, EXTENDED_PROFILE, MESSAGE, OPERATIONS, PROFILE, argument, fields,
)
from packages.contracts.scalar_functions import ScalarBudget
from packages.contracts.strategy_ir import strategy_nodes
from packages.contracts.trading import STRATEGY_DECLARATIONS
from packages.runtime.history import StorageBudget

STATE = 'openpine.replay-alert-state.1'
FILL_STATE = 'openpine.replay-alert-state.2'


@dataclass(frozen=True)
class FillNotice:
    site: str
    order_id: str
    fill_id: str
    role: str | None


def pending_orders(broker):
    return (*broker.pending, *getattr(broker, 'entries', {}).values(), *getattr(broker, 'protections', {}).values())


class ReplayAlerts:
    def __init__(self, program, sink, *, every_tick, fills=False):
        self.sites = {node.call_site_id: node.operation for node in nodes(program.statements)}
        self.strategy = program.statements[0].operation in STRATEGY_DECLARATIONS
        self.conditions = [] if self.strategy else definitions(program)
        self.every_tick = every_tick
        self.sink = sink
        self.seen = set()
        self.records = 0
        self.fill_sites = ({node.call_site_id: node for node in strategy_nodes(program)
                            if node.operation in OPERATIONS} if fills else None)
        self.fill_profile = (EXTENDED_PROFILE if any(node.operation in EXTENDED_OPERATIONS
                             for node in (self.fill_sites or {}).values()) else PROFILE)
        self.disabled = set()
        self.observation = None

    def start(self, *, observed):
        self.observation = [] if observed else None

    def script(self, intents):
        if self.observation is not None:
            self.observation.extend(intents)

    def submitted(self, broker, first_order, disabled_sites):
        if disabled_sites:
            self.disabled.update(order.order_id for order in pending_orders(broker)
                                 if order.sequence >= first_order and order.intent.call_site_id in disabled_sites)

    def capture_fill(self, order, fill, role):
        site = order.intent.call_site_id
        if (self.observation is not None and site in self.fill_sites
                and fill.order_id not in self.disabled):
            self.observation.append(FillNotice(site, fill.order_id, fill.fill_id, role))

    def finish(self, event, frame, evaluation, broker):
        if self.disabled:
            self.disabled.intersection_update(order.order_id for order in pending_orders(broker))
        intents, self.observation = self.observation, None
        if not intents:
            return
        selected = copy(evaluation)
        selected.frame, selected.work = frame, None
        selected.observe(None)
        selected.budget, selected.scalar_budget = StorageBudget(), ScalarBudget()
        selected.scalar_mode = True
        self.observe(event, frame.bar_index, intents, selected)

    def message(self, notice, evaluation):
        node = self.fill_sites[notice.site]
        selected = argument(node, EXIT_MESSAGES.get(notice.role))
        message = evaluation.evaluate(selected) if selected is not None else None
        if message is None:
            fallback = argument(node, MESSAGE)
            message = evaluation.evaluate(fallback) if fallback is not None else None
        message = '' if message is None else message
        if not valid_message(message):
            raise OpenPineError(Diagnostic('RUNTIME_ERROR', 'FILL_ALERT_MESSAGE',
                'Order fill alert message exceeds 4096 UTF-8 bytes.', node.span,
                language_version=evaluation.program.identity.language_version))
        return message

    def observe(self, event, bar_index, intents, evaluation=None):
        seen = set(self.seen)
        events = []
        for intent in intents:
            base = {'ordinal': len(events), 'bar_index': bar_index, 'bar_time': event.time,
                    'revision': event.revision, 'phase': event.kind}
            if isinstance(intent, FillNotice):
                events.append({**base, 'kind': ORDER_FILL, 'call_site_id': intent.site,
                               'order_id': intent.order_id, 'fill_id': intent.fill_id,
                               'message': self.message(intent, evaluation), 'frequency': None})
                continue
            site, message, frequency = intent
            condition = self.sites[site] != CALL
            if condition or self.strategy and not self.every_tick:
                frequency = CLOSE
            if frequency == CLOSE and event.kind != 'close':
                continue
            if frequency != ALL and site in seen:
                continue
            if frequency != ALL:
                seen.add(site)
            events.append({**base, 'call_site_id': site,
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
        state = {'schema_version': STATE, 'records': self.records, 'seen': sorted(self.seen)}
        if self.fill_sites is not None:
            state.update(schema_version=FILL_STATE, disabled_orders=sorted(self.disabled))
        return state

    def restore(self, value, sequence, active, broker=None):
        keys = {'schema_version', 'records', 'seen'}
        if self.fill_sites is not None:
            keys.add('disabled_orders')
        if (type(value) is not dict or value.keys() != keys
                or value['schema_version'] != (FILL_STATE if self.fill_sites is not None else STATE)
                or type(value['records']) is not int
                or not 0 <= value['records'] <= (sequence + 1) * MAX_PER_EVENT
                or type(value['seen']) is not list or len(value['seen']) > min(value['records'], len(self.sites))
                or any(type(site) is not str or self.sites.get(site) != CALL for site in value['seen'])
                or value['seen'] != sorted(set(value['seen'])) or value['seen'] and active is None):
            raise ValueError('Invalid replay alert frequency boundary.')
        if self.fill_sites is not None:
            disabled = value['disabled_orders']
            eligible = {order.order_id for order in pending_orders(broker)
                        if order.intent.call_site_id in self.fill_sites
                        and DISABLE in fields(self.fill_sites[order.intent.call_site_id])}
            if (type(disabled) is not list or len(disabled) > len(eligible)
                    or any(type(item) is not str or item not in eligible for item in disabled)
                    or disabled != sorted(set(disabled))):
                raise ValueError('Invalid replay order fill alert boundary.')
            self.disabled = set(disabled)
        self.records = value['records']
        self.seen = set(value['seen'])
