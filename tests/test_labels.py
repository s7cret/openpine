"""Regression coverage for label identity, rollback and durable event ranges."""
import copy
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.labels import LIMIT
from packages.contracts.trading import BrokerSettings
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.checkpoint_records import get as checkpoint_get
from packages.storage.label_journal import LabelJournal, label_events, mark_chunks
from packages.storage.objects import ObjectStore
from packages.storage.results import MIXED_OUTPUT_PREFIX, ResultSink
from tests.test_realtime import PRIMARY, configuration, historical, tape
from tools.labels_fixtures import source, strategy_source


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_strategy_labels_leave_orders_fills_and_equity_unchanged(self):
        def trading_moments(run):
            return [(event['kind'], event['bar_index'], event['time']) for event in run.simulation['events']
                    if event['kind'] in ('order', 'fill', 'trade')]

        data = historical(tape())
        for version in (4, 5, 6):
            with self.subTest(version=version):
                name = 'strategy-labels.pine'
                program = self.session.compile(strategy_source(version), name)
                saved = import_program(export_program(program))
                result = self.session.execute(saved, data=data)
                _, plain = self.session.run(strategy_source(version, with_labels=False), name, data=data)
                self.assertEqual(result.outputs[0].values, plain.outputs[0].values)
                self.assertEqual(result.simulation['statistics'], plain.simulation['statistics'])
                self.assertEqual(trading_moments(result), trading_moments(plain))
                fills = [event['value'] for event in result.simulation['events'] if event['kind'] == 'fill']
                self.assertEqual([(row['bar_index'], row['price'], row['quantity']) for row in fills],
                                 [(1, '12', '1'), (3, '16', '1')])
                self.assertEqual(result.tables, plain.tables)
                self.assertEqual([(row.id, row.x, row.text) for row in result.labels],
                                 [(2, 0, 'Entry submitted'), (3, 2, 'Exit submitted')])

    def test_strategy_label_and_trade_journals_resume_in_history(self):
        from test_checkpoints import paused
        from test_jobs import wait
        from test_strategies import request

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        text = strategy_source(6)
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            req = request(manager, text=text)
            baseline = manager.submit(req)['job_id']
            self.assertEqual(wait(manager, baseline)['status'], 'succeeded')
            expected = manager.result(baseline)['result']
            expected_events = manager.label_events(baseline)['events']
            job = manager.submit(req, pause_after=2)['job_id']
            paused(manager, job)
            checkpoint = manager.services.checkpoints.manifest(manager.get(job)['checkpoint_id'])
            prefix = checkpoint_get(manager.objects, checkpoint['output_ref'])
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertEqual(prefix['completed'], 2)
            self.assertEqual(prefix['label_journal']['records'], 4)
            self.assertIn('simulation', prefix)
        with local_manager(root, host_admission=host, max_workers=1) as manager:
            manager.resume(job)
            self.assertEqual(wait(manager, job)['status'], 'succeeded', manager.summary(job))
            self.assertEqual(manager.result(job)['result'], expected)
            events = manager.label_events(job)['events']
            self.assertEqual(events, expected_events)
            self.assertEqual([row['event']['action'] for row in events],
                             ['create', 'create', 'update', 'update', 'delete', 'create'])
            self.assertEqual(events[4]['event'], {'action': 'delete', 'id': 1, 'reason': 'capacity'})
            window = manager.chart(job, start=2, limit=1)
            self.assertEqual([(row['id'], row['x']) for row in window['labels']], [(3, 2)])
            self.assertEqual(window['tables'][0]['cells'][0]['text'], '1004')

    def test_strategy_label_preview_and_two_journals_resume_in_replay(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        host = HostAdmission(probe=lambda: None)
        events = [event.payload() for event in tape()]
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            req = configuration(manager, strategy_source(6, on_tick=True))
            req['broker_settings'] = asdict(BrokerSettings(tick_size='0.5'))
            baseline = replay.create(req)['replay_id']
            replay.append(baseline, events)
            replay.advance(baseline, checkpoint_every=1)
            expected_chart = replay.view(baseline, 'chart')
            expected_labels = replay.view(baseline, 'label_events', limit=100)['events']
            expected_ledger = replay.view(baseline, 'ledger')['events']
            identifier = replay.create(req)['replay_id']
            replay.append(identifier, events)
            first = replay.advance(identifier, max_events=1, checkpoint_every=1)
            self.assertEqual(first['labels'], [])
            self.assertEqual([label['id'] for label in first['preview']['labels']], [1, 2])
            prefix = replay.repository.checkpoint(identifier)['output']
            self.assertEqual(prefix['schema_version'], MIXED_OUTPUT_PREFIX)
            self.assertEqual((prefix['completed'], prefix['simulation_completed'], prefix['observations']), (0, 1, 1))
            self.assertEqual(prefix['label_journal']['records'], 0)
            replay.advance(identifier, max_events=4, checkpoint_every=1)
            partial = replay.view(identifier, 'chart')
            self.assertEqual(partial['labels'][0], {'id': 1, 'x': 0, 'y': 11., 'text': 'Pending'})
            self.assertEqual(partial['preview']['labels'][0], {'id': 1, 'x': 1, 'y': 12.5, 'text': 'Open'})
            self.assertEqual(replay.view(identifier, 'label_events', limit=100)['total'], 2)
        with local_manager(root, host_admission=host) as manager:
            replay = Replays(manager)
            self.assertEqual(replay.view(identifier, 'chart'), partial)
            replay.advance(identifier, checkpoint_every=1)
            self.assertEqual(replay.view(identifier, 'chart'), expected_chart)
            self.assertEqual(replay.view(identifier, 'label_events', limit=100)['events'], expected_labels)
            self.assertEqual(replay.view(identifier, 'ledger')['events'], expected_ledger)

    def test_preview_rollback_checkpoint_and_close_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = ObjectStore(root / 'objects')
            events = list(tape())
            script = source(6)
            program = self.session.compile(script, 'label.pine')
            for profile in (None, INTRABAR):
                with self.subTest(profile=profile):
                    def engine(name):
                        sink = ResultSink(objects, root / name)
                        sink.enable_labels()
                        return ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink,
                                            calculation_profile=profile), sink

                    first, sink = engine('first')
                    first.apply(events[0])
                    self.assertEqual(first.status()['labels'], [])
                    self.assertEqual(first.preview['labels'][0]['y'], 10.)
                    first.apply(events[1])
                    self.assertEqual(first.preview['labels'][0], {'id': 1, 'x': 0, 'y': 10.5, 'text': 'early'})
                    self.assertEqual(sink.label_journal.records, 0)
                    first.apply(events[2])
                    self.assertEqual(first.status()['labels'][0]['y'], 11.)
                    self.assertEqual(sink.label_journal.records, 4)
                    first.apply(events[3])
                    self.assertEqual(first.preview['labels'][0]['x'], 1)
                    self.assertEqual(first.status()['labels'][0]['x'], 0)
                    state, prefix = first.checkpoint(), sink.checkpoint(1)
                    resumed, output = engine('resumed')
                    resumed.restore(copy.deepcopy(state))
                    output.restore(copy.deepcopy(prefix), 1)
                    output.validate_labels(resumed.evaluation.labels)
                    self.assertEqual(resumed.status(), first.status())
                    for event in events[4:]:
                        resumed.apply(event)
                    _, expected = self.session.run(script, 'label.pine', data=historical(events))
                    self.assertEqual(resumed.status()['labels'], [asdict(label) for label in expected.labels])
                    journal = output.finish(resumed.status())['label_journal']
                    rows = list(label_events(objects, journal, 0, 20))
                    self.assertEqual(len(rows), 12)
                    self.assertEqual([row['bar_index'] for row in rows], [0] * 4 + [1] * 4 + [2] * 2 + [3] * 2)
                    self.assertEqual(rows[6]['event']['label']['id'], 3)
                    self.assertEqual(rows[7]['event'], {'action': 'delete', 'id': 3, 'reason': 'explicit'})

    def test_eviction_deleted_alias_and_restore_guard(self):
        script = source(6, """var label oldest = label.new(0, close)
if bar_index == 0
    for i = 1 to 49
        label.new(i, close)
if bar_index > 0
    label.new(bar_index, close)
label.set_text(oldest, "first")
""")
        program = self.session.compile(script, 'eviction.pine')
        events = list(tape(2))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            objects = ObjectStore(root / 'objects')
            sink = ResultSink(objects, root / 'replay')
            sink.enable_labels()
            engine = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)
            for event in events[:3]:
                engine.apply(event)
            state = engine.checkpoint()
            self.assertEqual(len(engine.status()['labels']), LIMIT)
            self.assertEqual(engine.status()['labels'][0]['text'], 'first')
            invalid = copy.deepcopy(state)
            bindings = invalid['runtime']['persistent']
            bindings[next(iter(bindings))] = invalid['runtime']['labels']['next_id']
            other = ReplayEngine(self.session, program, (PRIMARY,), PRIMARY.context_id, sink)
            with self.assertRaisesRegex(ValueError, 'persisted label reference'):
                other.restore(invalid)
            other.restore(state)
            for event in events[3:]:
                other.apply(event)
            labels = other.status()['labels']
            self.assertEqual([label['id'] for label in labels], list(range(2, 52)))
            journal = sink.finish(other.status())['label_journal']
            rows = list(label_events(objects, journal, 51, 10))
            self.assertEqual([row['event'] for row in rows], [
                {'action': 'delete', 'id': 1, 'reason': 'capacity'},
                {'action': 'create', 'label': {'id': 51, 'x': 1, 'y': 13., 'text': ''}},
            ])

    def test_journal_crosses_chunk_boundary_without_sealing_checkpoint_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            objects = ObjectStore(Path(directory) / 'objects')
            journal = LabelJournal(objects)
            events = [{'action': 'update', 'label': {'id': 1, 'x': 0, 'y': 12., 'text': 'x' * 4096}}] * 70
            journal.emit(0, 0, events)
            prefix = journal.checkpoint()
            self.assertTrue(prefix['parts'])
            self.assertIsNotNone(prefix['pending'])
            marked = set()
            mark_chunks(prefix, marked.add)
            self.assertEqual(len(marked), 2)
            resumed = LabelJournal(objects)
            resumed.restore(prefix, 1)
            tail = {'action': 'delete', 'id': 1, 'reason': 'explicit'}
            resumed.emit(1, 60000, [tail])
            publication = resumed.publication()
            self.assertEqual(publication['records'], 71)
            self.assertEqual(len(list(label_events(objects, publication, 59, 8))), 8)
            self.assertEqual(list(label_events(objects, publication, 70, 1)), [{'bar_index': 1, 'time': 60000, 'event': tail}])
