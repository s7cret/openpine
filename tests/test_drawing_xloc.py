"""Global drawing positions, durable replay boundaries and located records."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.codec import decode, encode
from packages.contracts.contexts import MarketContext
from packages.contracts.drawing_clock import projected, stream_positions, timestamps
from packages.contracts.intrabar import PROFILE as INTRABAR
from packages.contracts.realtime import MarketEvent
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.candles import CandleStore
from packages.storage.database import Database
from packages.storage.objects import ObjectStore
from packages.storage.replay import ReplayRepository
from packages.storage.results import ResultSink
from tests.test_realtime import historical
from tools.drawing_xloc_fixtures import BOX, FLOOR, LABEL, LINE, MIXED, TIMES, source
from tools.installed_lines_smoke import market_events
from tools.specification import ROOT


PRIMARY = MarketContext('primary', 'TEST:LINES', '1', 'recorded.1')
BARS = ROOT / 'conformance/drawing-xloc/bars.json'


class DrawingXlocTests(unittest.TestCase):
    def test_clock_stable_across_parquet_parts_and_slices(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = Database(root / 'catalog.sqlite3')
        self.addCleanup(db.close)
        store = CandleStore(root, db)
        bars = historical([MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)], PRIMARY).bars
        metadata = dict(context_id=PRIMARY.context_id, symbol=PRIMARY.symbol, timeframe='1',
                        bar_schema='openpine.historical-bars.2')
        with patch('packages.storage.candles.BLOCK_ROWS', 2):
            key = store.ingest(metadata, bars, {'provider': 'test'})
        requested = {-60000, 0, 60000, 150000, 240000, 360000}
        expected = {-60000: -1., 0: 0, 60000: 1, 150000: 1.5, 240000: 2, 360000: 4.}
        self.assertEqual(store.source(key).drawing_positions(requested), expected)
        self.assertEqual(stream_positions(requested, zip(TIMES, range(4), strict=True)), expected)
        sliced = store.source(store.slice(key, 60000, 300001))
        self.assertEqual(sliced.drawing_positions({60000, 150000, 300000, 360000}),
                         {60000: 0, 150000: .5, 300000: 2, 360000: 3.})
        short_tail = store.source(store.slice(key, 0, 240001))
        self.assertEqual(short_tail.drawing_positions({240000, 420000}), {240000: 2, 420000: 3.})
        single = store.source(store.slice(key, 60000, 60001))
        self.assertEqual(single.drawing_positions({0, 60000, 120000}), {0: None, 60000: 0, 120000: None})

    def test_replay_clock_ignores_accepted_future_and_duplicate_appends(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        db = Database(root / 'catalog.sqlite3')
        self.addCleanup(db.close)
        repository = ReplayRepository(db, ObjectStore(root / 'objects'))
        replay = repository.create({'identity': 'test', 'primary_id': PRIMARY.context_id,
                                    'contexts': [{'context_id': PRIMARY.context_id}], 'drawing_clock': True})
        events = market_events(BARS, PRIMARY)
        repository.append(replay, events)
        repository.append(replay, events)
        count = db.execute('SELECT count(*) FROM replay_bar_clock WHERE replay_id=?', (replay,))[0][0]
        self.assertEqual(count, 4)
        self.assertEqual(repository.drawing_positions(replay, {0, 150000}, {'confirmed_bars': 0}, preview_time=0),
                         {0: 0, 150000: None})
        state = {'confirmed_bars': 1, 'sequence': 2, 'contexts': {PRIMARY.context_id: {'last_closed': 0}}}
        self.assertEqual(repository.drawing_positions(replay, {0, 150000}, state), {0: 0, 150000: None})
        self.assertEqual(repository.drawing_positions(replay, {0, 150000}, state, preview_time=60000),
                         {0: 0, 150000: 2.5})
        state = {'confirmed_bars': 4, 'sequence': 7, 'contexts': {PRIMARY.context_id: {'last_closed': 300000}}}
        self.assertEqual(repository.drawing_positions(replay, {150000, 360000}, state), {150000: 1.5, 360000: 4.})

    def test_saved_ir_and_checkpoint_retain_located_records_and_previews(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        objects = ObjectStore(root / 'objects')
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        text = source(6, MIXED)
        program = import_program(export_program(session.compile(text, 'xloc.pine')))
        self.assertEqual(program.schema_version, 'openpine.typed-ir.29')
        events = [MarketEvent.from_payload(e) for e in market_events(BARS, PRIMARY)]
        result = session.execute(program, data=historical(events, PRIMARY))
        self.assertEqual(decode(encode(result), type(result)), result)

        def make():
            sink = ResultSink(objects, tempfile.mkdtemp(dir=root))
            for kind in ('labels', 'lines', 'boxes'):
                getattr(sink, 'enable_' + kind)()
            return ReplayEngine(session, program, (PRIMARY,), PRIMARY.context_id, sink, calculation_profile=INTRABAR)

        first = make()
        for event in events[:3]:
            first.apply(event)
        status = first.status()
        self.assertEqual(status['lines'][0]['y1'], 25.)
        self.assertEqual(status['preview']['lines'][0]['y1'], 24.)
        checkpoint = first.checkpoint()
        self.assertEqual(checkpoint['runtime']['schema_version'], 'openpine.runtime-state.21')
        self.assertEqual(checkpoint['runtime']['labels']['live'][0]['xloc'], 'bar_time')
        prefix = first.evaluation.output_sink.checkpoint(1)
        second = make()
        second.restore(checkpoint)
        second.evaluation.output_sink.restore(prefix, 1)
        self.assertEqual(second.status(), status)
        for event in events[3:]:
            second.apply(event)
        final = second.status()
        self.assertEqual(final['lines'], [LINE, FLOOR])
        self.assertEqual(final['labels'], [LABEL])
        self.assertEqual(final['boxes'], [BOX])
        positions = stream_positions(timestamps(final), zip(TIMES, range(4), strict=True))
        projection = projected(final, positions)
        self.assertEqual(projection['labels'][0], {**LABEL, 'chart_x': 1.5})
        self.assertEqual(projection['boxes'][0], {**BOX, 'chart_left': 1.5, 'chart_right': 4.})
        self.assertEqual(projection['lines'], [{**LINE, 'chart_x1': 1, 'chart_x2': 3}, FLOOR])
