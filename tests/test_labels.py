"""Regression coverage for label identity, rollback and durable event ranges."""
import copy
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.labels import LIMIT
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.label_journal import LabelJournal, label_events, mark_chunks
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from tests.test_realtime import PRIMARY, historical, tape
from tools.labels_fixtures import source


class LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

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
