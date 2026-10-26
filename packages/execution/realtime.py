"""Observed-event execution. Runtime kernels and the market broker are shared.

Only committed bars reach the output sink. Preview evaluations are disposable;
their persistent variables, kernels and histories never enter committed state.
"""

import copy
import re
from dataclasses import asdict
from decimal import Decimal

from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.calendar import aligned,next_open,requestable
from packages.contracts.context_ir import request_nodes, request_flags
from packages.contracts.realtime import MarketEvent, PROFILE, policy
from packages.contracts.live import RECEIVED_PROFILE, REQUEST_HISTORY, event_policy
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import digest, read_json
from packages.execution.identity import ROOT
from packages.execution.contexts import expression_program
from packages.execution.trading import TradingExecution
from packages.runtime.engine import program_hash
from packages.runtime.rollback import branch
from packages.contracts import TupleType

STATE = 'openpine.replay-state.2'


class Capture:
    def __init__(self): self.values = {}
    def emit(self, site, title, value_type, value, index, timestamp):
        self.values[site] = {'output_id': site, 'title': title, 'value_type': asdict(value_type), 'value': value}


def captured(sink):
    return sink.last if hasattr(sink,'last') else next(iter(sink.values.values()))['value']


def decoded_request(value,typ):
    if isinstance(typ,TupleType):
        if type(value) not in (tuple,list):raise ValueError('Invalid requested tuple encoding.')
        return tuple(value)
    return value


def frame(event, count):
    return BarFrame(event.context_id, count, event.time, event.close, event.open, event.high, event.low, event.volume)


def fork(evaluation):
    sink = Capture()
    return branch(evaluation,sink), sink


class ReplayEngine:
    def __init__(self, session, program, contexts, primary_id, sink, *, broker_settings=None, profile=PROFILE,settings=None,environments=None,broker_profile=None):
        if session.profile_id != HISTORICAL_PROFILE_ID: raise ValueError('Replay requires the historical typed IR profile.')
        self.runtime = session.validate_program(program)
        from packages.contracts.observed_broker import PROFILE as OBSERVED_BROKER
        if broker_profile not in (None,OBSERVED_BROKER):raise ValueError('Unknown observed broker profile.')
        self.broker_profile=broker_profile
        from packages.contracts.strategy_ir import strategy_nodes
        if broker_profile is None and any(n.operation=='simulation.exit_extended' for n in strategy_nodes(program)):
            from packages.contracts import Diagnostic,OpenPineError
            raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','PROTECTION_CONFIRMED_ONLY','Relative and trailing exits require confirmed historical jobs.',language_version=program.identity.language_version))
        from packages.contracts.protection import PROFILE as PROTECTION_PROFILE
        selected=broker_policy(program,broker_settings)
        if broker_profile is None and selected and selected.profile_id in (PROTECTION_PROFILE,'openpine.partial-broker.1','openpine.pending-entry-broker.1','openpine.lot-broker.1','openpine.order-group-broker.1','openpine.trailing-broker.1','openpine.economics-broker.1','openpine.risk-broker.1','openpine.performance-broker.1'):
            from packages.contracts import Diagnostic,OpenPineError
            raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','PROTECTION_CONFIRMED_ONLY',
                'Protective OHLC exits require confirmed historical jobs; observed/live tick execution is outside this profile.',
                language_version=program.identity.language_version))
        qualification = read_json(ROOT/'spec/replay-profile.json')
        if (qualification['source_lock_sha256'] != digest(session.snapshot.lock)
                or qualification['versions'][str(program.identity.language_version)]['policy'] != policy(program.identity.language_version)):
            raise ValueError('Observed-event profile differs from the pinned specification/execution choices.')
        if broker_profile:
            from packages.contracts.observed_broker import policy as observed_policy
            observed=read_json(ROOT/'spec/observed-broker-profile.json')
            if (observed['source_lock_sha256']!=digest(session.snapshot.lock) or observed['legacy_replay_sha256']!=digest(qualification)
                    or observed['versions'][str(program.identity.language_version)]!=observed_policy(program.identity.language_version)):
                raise ValueError('Observed broker differs from its pinned per-version execution contract.')
        self.program, self.sink = program, sink
        event_policy(program.identity.language_version,profile)
        self.profile=profile
        if (type(contexts) is not tuple or not 1 <= len(contexts) <= 8
                or any(type(c) is not MarketContext for c in contexts)
                or len({c.context_id for c in contexts}) != len(contexts)
                or len({(c.symbol, c.timeframe) for c in contexts}) != len(contexts)):
            raise ValueError('Replay needs one to eight unambiguous market contexts.')
        self.contexts = {c.context_id: c for c in contexts}
        if primary_id not in self.contexts: raise ValueError('Unknown primary replay context.')
        from packages.contracts.environment import Environment,uses_environment
        self.has_environment=uses_environment(program) or environments is not None
        if environments is not None and (type(environments) is not dict or not environments.keys()<=self.contexts.keys()):raise ValueError('Invalid replay environment contexts.')
        self.environments={}
        for key,context in self.contexts.items():
            selected=(environments or {}).get(key,{'instrument':None,'history_last_ms':None})
            if type(selected) is not dict or selected.keys()!={'instrument','history_last_ms'}:raise ValueError('Replay environment requires instrument and historical-end fields.')
            self.environments[key]=Environment(key,context.symbol,context.timeframe,None,selected['instrument'],'observed',selected['history_last_ms'])
        self.environment_inputs={key:{'instrument':env.payload()['instrument'],'history_last_ms':env.history_last_ms} for key,env in self.environments.items()}
        self.primary_id = primary_id
        self.members = {key: {'active': None, 'last_closed': None, 'count': 0} for key in self.contexts}
        self.evaluation = self.runtime.prepare(program, output_sink=sink,settings=settings)
        if self.has_environment:self.evaluation.environment=self.environments[primary_id]
        self.input_settings=self.evaluation.input_settings
        selected = broker_policy(program, broker_settings)
        if selected is None and broker_settings is not None: raise ValueError('Broker settings require a strategy program.')
        if self.has_environment:
            from packages.execution.environment import broker_environment
            selected=broker_environment(self.environments[primary_id],selected)
        if selected and broker_profile:
            from packages.contracts.observed_broker import promote
            from packages.execution.observed_trading import ObservedTrading
            selected=promote(selected,program.identity.language_version,self.contexts[primary_id].timeframe)
            self.simulation=ObservedTrading(self.evaluation,selected)
        else:self.simulation = TradingExecution(self.evaluation, selected) if selected else None
        self.broker_boundary = None
        self.sequence, self.event_time, self.order_key = -1, None, None
        self.chain = digest({'profile': self.profile, 'program': program_hash(program),
            **({'broker_execution':self.qualification()['broker_execution']} if broker_profile else {}),
            'contexts': [asdict(c) for c in contexts], 'primary_id': primary_id,
            **({'environments':self.environment_inputs} if self.has_environment else {}),
            'broker': asdict(selected) if selected else None,**({'settings_sha256':self.input_settings['sha256']} if self.input_settings else {})})
        self.identity = self.chain
        self.last_event = None
        self.preview = None
        self.requests = {}
        available = {(c.symbol, c.timeframe): c.context_id for c in contexts}
        for node in request_nodes(program):
            key = tuple(node.arguments[i].expression.value for i in (0, 1))
            if key not in available: raise ValueError('Requested context is absent from the replay.')
            context_id = available[key]
            if not requestable(self.contexts[primary_id].timeframe,self.contexts[context_id].timeframe): raise ValueError('Replay requests require supported same/higher timeframes.')
            private = expression_program(program, node.arguments[2].expression, self.runtime, node.span)
            capture = Capture()
            self.requests[node.call_site_id] = {'context_id': context_id, 'node': node,
                'evaluation': (self.runtime.prepare_expression(private,capture,settings=self.input_settings)
                    if isinstance(node.value_type,TupleType) or node.value_type.base=='bool' else self.runtime.prepare(private, output_sink=capture,settings=self.input_settings)), 'sink': capture,
                'last': None, 'primary_cursor': 0,'history':[]}
            if self.has_environment:self.requests[node.call_site_id]['evaluation'].environment=self.environments[context_id]

    def phase(self,event):
        env=self.environments[event.context_id]
        if env.history_last_ms is not None and event.time<=env.history_last_ms:return 'historical'
        return 'close_first' if event.kind=='close' and event.revision==0 else event.kind

    def visible(self,record,event):
        ceiling=next_open(event.time,self.contexts[self.primary_id].timeframe)
        history=record['history']
        for row in reversed(history):
            if row['end']<=ceiling:return row['value'],row['count']
        if history and history[0]['count']>1:raise ValueError('Primary lag exceeds the retained requested-value history; rebuild this replay from its recording.')
        return None,0

    def key(self, event):
        # At a shared timestamp, secondary close/update precedes primary close;
        # all closing events precede openings of the next interval.
        if self.profile==RECEIVED_PROFILE:return (event.event_time,event.sequence)
        return (event.event_time, 2 if event.kind == 'open' or event.event_time == event.time else 1 if event.context_id == self.primary_id else 0)

    def validate(self, event):
        if type(event) is not MarketEvent or event.context_id not in self.contexts: raise ValueError('Unknown replay event/context.')
        if event.sequence != self.sequence + 1: raise ValueError('Missing/out-of-order event sequence; replay the missing prefix.')
        key = self.key(event)
        if self.order_key is not None and key < self.order_key: raise ValueError('Event availability/tie order moved backwards.')
        for context_id, state in self.members.items() if self.profile==PROFILE else ():
            if state['active'] is None: continue
            end = next_open(state['active']['time'],self.contexts[context_id].timeframe)
            if (end < event.event_time or event.kind == 'open' and end <= event.event_time
                    or event.kind == 'close' and event.context_id == self.primary_id and context_id != self.primary_id and end <= event.event_time):
                raise ValueError('Missing candle close before advancing availability; supply the missing event prefix.')
        member = self.members[event.context_id]
        if self.has_environment and self.phase(event)=='historical' and (event.kind!='close' or event.revision!=0):raise ValueError('Pinned historical range accepts standalone confirmed closes only.')
        tf=self.contexts[event.context_id].timeframe;end=next_open(event.time,tf)
        if self.profile==PROFILE and (not aligned(event.time,tf) or (event.kind == 'open' and event.event_time != event.time
                or event.kind == 'update' and not event.time <= event.event_time < end
                or event.kind == 'close' and event.event_time != end)):
            raise ValueError('Event violates aligned UTC open/update/close availability.')
        if self.profile==RECEIVED_PROFILE and (not aligned(event.time,tf) or event.event_time<event.time
                or event.kind=='close' and event.event_time<end):
            raise ValueError('Arrival-time event precedes its candle availability.')
        active, last = member['active'], member['last_closed']
        if active is None:
            if event.kind == 'update' or event.revision != 0: raise ValueError('A new bar starts with revision zero open or historical close.')
            if last is not None and event.time < next_open(last,tf): raise ValueError('Closed candles are immutable; create a new replay for corrections.')
        else:
            if (event.kind == 'open' or event.time != active['time'] or event.revision != active['revision'] + 1
                    or event.open != active['open'] or event.high < active['high'] or event.low > active['low']
                    or event.volume < active['volume']):
                raise ValueError('Missing revision, overlapping bar, or inconsistent cumulative OHLCV update.')

    def request_values(self, event):
        result = {}
        for site, record in self.requests.items():
            member = self.members[record['context_id']]
            gaps, _ = request_flags(record['node'], self.program.identity.language_version)
            value,count = self.visible(record,event)
            if gaps == 'on':
                if count == record['primary_cursor']: value = None
            elif member['active'] is not None:
                developing = MarketEvent.from_payload(member['active'])
                end=next_open(developing.time,self.contexts[developing.context_id].timeframe)
                if developing.time <= event.time < end:
                    temporary, capture = fork(record['evaluation'])
                    temporary.environment_phase=self.phase(developing)
                    temporary.step(frame(developing, temporary.count))
                    value = captured(capture)
            if value is None:
                from packages.contracts.context_ir import missing_request
                value=missing_request(record['node'].value_type,self.program.identity.language_version)
            result[site] = value
        return result

    def refresh_preview(self, event):
        active = self.members[self.primary_id]['active']
        if active is None or self.simulation is not None:
            self.preview = None
            return
        current = MarketEvent.from_payload(active)
        temporary, capture = fork(self.evaluation)
        temporary.request_values = self.request_values(current)
        temporary.environment_phase=self.phase(current)
        temporary.step(frame(current, temporary.count))
        self.preview = {'time': current.time, 'revision': current.revision,
            'observed_at': event.event_time, 'confirmed': False, 'outputs': list(capture.values.values())}

    def apply(self, event):
        # Arbitrarily old duplicates are resolved by the durable input journal.
        # The engine also supports immediate retransmission without a journal.
        if type(event) is MarketEvent and event.sequence == self.sequence:
            if event.payload() != self.last_event: raise ValueError('Conflicting duplicate event.')
            return self.status()
        self.validate(event)
        member = self.members[event.context_id]
        first = member['active'] is None
        member['active'] = event.payload()
        if event.context_id == self.primary_id and self.simulation is not None:
            if self.broker_profile:self.simulation.observe(event,first=first)
            elif first: self.simulation.begin(event)
            else:
                self.simulation.broker.mark = Decimal(str(event.close))
                self.evaluation.strategy_values = self.simulation.broker.views()
        if event.kind == 'close':
            for record in self.requests.values():
                if record['context_id'] == event.context_id:
                    selected = record['evaluation']
                    selected.environment_phase=self.phase(event)
                    selected.step(frame(event, selected.count))
                    record['last'] = captured(record['sink'])
                    record['history'].append({'time':event.time,'end':next_open(event.time,self.contexts[event.context_id].timeframe),
                        'count':member['count']+1,'value':record['last']})
                    if len(record['history'])>REQUEST_HISTORY:del record['history'][:-REQUEST_HISTORY]
            member['count'] += 1
            member['last_closed'] = event.time
            member['active'] = None
            if event.context_id == self.primary_id:
                self.evaluation.request_values = self.request_values(event)
                self.evaluation.environment_phase=self.phase(event)
                self.evaluation.step(frame(event, self.evaluation.count))
                if self.simulation:
                    self.simulation.commit(event)
                    self.broker_boundary = copy.deepcopy(self.simulation.checkpoint())
                for record in self.requests.values():record['primary_cursor']=self.visible(record,event)[1]
        self.sequence, self.event_time, self.order_key = event.sequence, event.event_time, self.key(event)
        self.last_event = event.payload()
        self.chain = digest({'previous': self.chain, 'event': self.last_event})
        if self.broker_profile and self.simulation and event.context_id==self.primary_id:
            self.broker_boundary=self.simulation.checkpoint()
        self.refresh_preview(event)
        return self.status()

    def status(self):
        return {'schema_version': 'openpine.replay-status.1', 'profile_id': self.profile,
            'sequence': self.sequence, 'event_time': self.event_time, 'input_chain': self.chain,
            'confirmed_bars': self.evaluation.count, 'preview': self.preview,
            'contexts': copy.deepcopy(self.members),
            'simulation': self.simulation.summary() if self.simulation else None,
            **({'settings':self.input_settings} if self.input_settings else {})}

    def checkpoint(self):
        return {'schema_version': STATE, 'identity': self.identity, 'sequence': self.sequence,
            'event_time': self.event_time, 'order_key': self.order_key, 'chain': self.chain,
            'last_event': self.last_event, 'members': copy.deepcopy(self.members),
            'runtime': self.evaluation.checkpoint() if self.evaluation.count else None,
            'broker_boundary': self.broker_boundary,
            'requests': {site: {'runtime': r['evaluation'].checkpoint() if r['evaluation'].count else None,
                'last': r['last'], 'primary_cursor': r['primary_cursor'],'history':copy.deepcopy(r['history'])} for site, r in self.requests.items()}}

    def restore(self, state):
        expected = {'schema_version', 'identity', 'sequence', 'event_time', 'order_key', 'chain', 'last_event',
            'members', 'runtime', 'broker_boundary', 'requests'}
        if (self.sequence != -1 or type(state) is not dict or state.keys() != expected
                or state['schema_version'] != STATE or state['identity'] != self.identity
                or type(state['sequence']) is not int or not -1 <= state['sequence'] <= 2**63-2
                or type(state['members']) is not dict or state['members'].keys() != self.members.keys()
                or type(state['requests']) is not dict or state['requests'].keys() != self.requests.keys()):
            raise ValueError('Replay checkpoint belongs to another program/context/policy.')
        if type(state['chain']) is not str or not re.fullmatch(r'[0-9a-f]{64}',state['chain']): raise ValueError('Invalid replay hash chain.')
        state = copy.deepcopy(state)
        for key, member in state['members'].items():
            if (type(member) is not dict or member.keys() != {'active', 'last_closed', 'count'}
                    or type(member['count']) is not int or not 0 <= member['count'] <= state['sequence']+1
                    or (member['last_closed'] is None) != (member['count'] == 0)):
                raise ValueError('Invalid replay context boundary.')
            if member['last_closed'] is not None and (type(member['last_closed']) is not int
                    or not aligned(member['last_closed'],self.contexts[key].timeframe)
                    or type(state['event_time']) is not int or next_open(member['last_closed'],self.contexts[key].timeframe)>state['event_time']):
                raise ValueError('Invalid confirmed context time.')
            if member['active'] is not None:
                active = MarketEvent.from_payload(member['active'])
                tf=self.contexts[key].timeframe
                if (active.context_id != key or active.kind == 'close' or active.sequence > state['sequence']
                        or not aligned(active.time,tf) or active.event_time > state['event_time']
                        or not active.time <= active.event_time
                        or self.profile==PROFILE and active.event_time>=next_open(active.time,tf)
                        or member['last_closed'] is not None and active.time < next_open(member['last_closed'],tf)):
                    raise ValueError('Invalid active replay candle.')
        primary = state['members'][self.primary_id]
        if (state['runtime'] is None) != (primary['count'] == 0): raise ValueError('Missing primary runtime boundary.')
        if state['runtime'] is not None:
            self.evaluation.restore(state['runtime'])
            if (self.evaluation.count, self.evaluation.last_time, self.evaluation.context_id) != (primary['count'], primary['last_closed'], self.primary_id):
                raise ValueError('Runtime cursor differs from confirmed market cursor.')
        self.members = state['members']
        for site, row in state['requests'].items():
            record = self.requests[site]; member = self.members[record['context_id']]
            if (type(row) is not dict or row.keys() != {'runtime', 'last', 'primary_cursor','history'}
                    or type(row['primary_cursor']) is not int or not 0 <= row['primary_cursor'] <= member['count']
                    or (row['runtime'] is None) != (member['count'] == 0)):
                raise ValueError('Invalid requested replay cursor.')
            row=dict(row)
            if member['count']:row['last']=decoded_request(row['last'],record['node'].value_type)
            history=row['history']
            if type(history) is not list or len(history)!=min(member['count'],REQUEST_HISTORY):raise ValueError('Invalid requested-value history size.')
            history=[dict(item) if type(item) is dict else item for item in history]
            prior=None
            for index,item in enumerate(history,member['count']-len(history)+1):
                if (type(item) is not dict or item.keys()!={'time','end','count','value'} or item['count']!=index
                        or not aligned(item['time'],self.contexts[record['context_id']].timeframe)
                        or item['end']!=next_open(item['time'],self.contexts[record['context_id']].timeframe)
                        or prior is not None and item['time']<prior):raise ValueError('Invalid requested-value history boundary.')
                from packages.contracts import ValueType
                item['value']=decoded_request(item['value'],record['node'].value_type)
                if not record['evaluation'].matches(item['value'],record['node'].value_type):raise ValueError('Invalid requested historical value.')
                prior=item['end']
            if history and (history[-1]['time']!=member['last_closed'] or history[-1]['value']!=row['last']):raise ValueError('Requested history differs from its committed cursor.')
            if row['runtime'] is not None:
                record['evaluation'].restore(row['runtime'])
                evaluation = record['evaluation']
                if (evaluation.count, evaluation.last_time, evaluation.context_id) != (member['count'], member['last_closed'], record['context_id']):
                    raise ValueError('Requested runtime differs from its confirmed context.')
                from packages.contracts import ValueType
                if not evaluation.matches(row['last'],record['node'].value_type): raise ValueError('Invalid requested last value.')
            elif row['last'] is not None: raise ValueError('Empty requested context has a last value.')
            record['last'], record['primary_cursor'],record['history'] = row['last'], row['primary_cursor'],history
        self.broker_boundary = state['broker_boundary']
        if self.simulation:
            if self.broker_profile:
                if self.broker_boundary is None:
                    if primary['count'] or primary['active']:raise ValueError('Missing observed broker boundary.')
                else:self.simulation.restore_observation(self.broker_boundary,primary['count'],primary['last_closed'],primary['active'],
                    sequence=state['sequence'],context_id=self.primary_id,available_at=state['event_time'])
            else:
                if (self.broker_boundary is None) != (primary['count'] == 0): raise ValueError('Missing confirmed broker boundary.')
                if self.broker_boundary is not None: self.simulation.restore(self.broker_boundary, primary['count'], primary['last_closed'])
                # Legacy market fills are regenerated from the immutable open.
                if primary['active'] is not None: self.simulation.begin(MarketEvent.from_payload(primary['active']))
        elif self.broker_boundary is not None: raise ValueError('Unexpected broker state.')
        self.sequence, self.event_time, self.chain = state['sequence'], state['event_time'], state['chain']
        self.order_key = tuple(state['order_key']) if state['order_key'] is not None else None
        self.last_event = state['last_event']
        if self.sequence >= 0:
            event = MarketEvent.from_payload(self.last_event)
            if event.sequence != self.sequence or event.event_time != self.event_time or self.key(event) != self.order_key:
                raise ValueError('Replay checkpoint event boundary differs.')
            self.refresh_preview(event)

    def qualification(self):
        value=event_policy(self.program.identity.language_version,self.profile)
        if self.broker_profile:
            from packages.contracts.observed_broker import policy as observed_policy
            value={**value,'broker_execution':observed_policy(self.program.identity.language_version)}
        return value
