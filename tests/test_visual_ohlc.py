"""Calculated OHLC layer admission, durable output, and replay qualification."""

from contextlib import nullcontext
import copy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.passport import describe
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError, RunResult
from packages.contracts.codec import decode, encode
from packages.contracts.markers import PROFILE as SIGNAL_PROFILE
from packages.contracts.visual_ohlc import PROFILE, SCHEMA
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.queries import ResultReader
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink, series_rows
from test_realtime import configuration, tape


SOURCE = """//@version=6
indicator("Calculated OHLC")
plotcandle(open+100, low+100, high+100, close>11 ? close+100 : na, "Candles",
           color=#12345680, wickcolor=#345678, bordercolor=na)
plotbar(open,high,low,close,"Bars",color=#765432,force_overlay=true)
plot(close)
"""


class VisualOHLCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-ohlc/bars.json'))
        cls.frames = [BarFrame(cls.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume)
                      for i,b in enumerate(cls.data.bars)]

    def test_result_codec_and_checkpoint_reject_forged_sample_or_call(self):
        program, result = self.session.run(SOURCE, 'ohlc.pine', data=self.data)
        self.assertEqual(decode(encode(result), RunResult), result)
        self.assertEqual(result.outputs[0].bars[1].high, 122.)
        self.assertEqual(result.outputs[0].bars[1].low, 118.)
        self.assertIsNone(result.outputs[0].bars[0])
        self.assertIsNone(result.outputs[0].bars[1].bordercolor)
        self.assertTrue(result.outputs[1].force_overlay)
        program = import_program(export_program(program))
        passport = describe(program, session=self.session)
        self.assertIn(PROFILE, passport['admission_profiles'])
        self.assertNotIn(SIGNAL_PROFILE, passport['admission_profiles'])
        profile, = (item for item in passport['profile_inventory'] if item['profile_id'] == PROFILE)
        self.assertEqual(profile['versions'], list(range(1,7)))
        runtime = self.session.validate_program(program)
        evaluation = runtime.prepare(program)
        for frame in self.frames[:2]:
            evaluation.step(frame)
        state = loads(canonical(evaluation.checkpoint()))
        self.assertEqual(state['schema_version'], 'openpine.runtime-state.15')
        restored = runtime.prepare(program)
        restored.restore(state)
        for frame in self.frames[2:]:
            restored.step(frame)
        self.assertEqual(restored.finish(data_identity(self.data)), result)
        for field, value in [('high', 0.0), ('wickcolor', 'black')]:
            invalid = copy.deepcopy(state)
            invalid['presentations'][result.outputs[0].output_id]['bars'][1][field] = value
            fresh = runtime.prepare(program)
            with self.assertRaises(ValueError):
                fresh.restore(invalid)
            self.assertEqual(fresh.count, 0)
        candle = program.statements[1]
        arguments = list(candle.arguments)
        title = arguments[4]
        arguments[4] = replace(title, origin='default')
        changed = replace(candle, arguments=tuple(arguments))
        forged = replace(program, statements=(program.statements[0], changed, *program.statements[2:]))
        with self.assertRaises(OpenPineError):
            self.session.validate_program(forged)

    def test_parquet_range_and_prefix_restore_preserve_aligned_samples(self):
        program, result = self.session.run(SOURCE, 'ohlc.pine', data=self.data)
        runtime = self.session.validate_program(program)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            objects = ObjectStore(root/'objects')
            sink = ResultSink(objects, root/'prefix')
            tail = ResultSink(objects, root/'tail')
            try:
                evaluation = runtime.prepare(program, output_sink=sink)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                state = loads(canonical(evaluation.checkpoint()))
                prefix = loads(canonical(sink.checkpoint(2)))
                tail.restore(prefix, 2)
                evaluation = runtime.prepare(program, output_sink=tail)
                evaluation.restore(state)
                for frame in self.frames[2:]:
                    evaluation.step(frame)
                summary = tail.finish({'observation_count': 4})
                rows = list(series_rows(objects, summary, 1, 3, presentation=True))
                self.assertEqual(rows[0][3], {'bar': asdict(result.outputs[0].bars[1]), 'value': None})
                self.assertEqual(rows[1][3]['bar']['color'], '#765432FF')
                self.assertEqual(rows[2][3], 20.0)
                self.assertEqual(list(series_rows(objects, summary, 1, 2))[0][3], None)
                source = SimpleNamespace(identity=SimpleNamespace(count=4),
                    rows=lambda offset, limit: iter(self.data.bars[offset:offset+limit]))
                reader = ResultReader(None, objects, SimpleNamespace(source=lambda _: source),
                                      series_rows, SimpleNamespace(slot=lambda _: nullcontext()))
                reader.summary = lambda _: dict(status='executed', result=summary, snapshot_sha256='0'*64)
                chart = reader.chart('job', 1, 2)
                self.assertEqual(chart['outputs'][0]['bars'], [asdict(b) for b in result.outputs[0].bars[1:3]])
                self.assertEqual(chart['outputs'][1]['presentation_schema'], SCHEMA)
                self.assertTrue(chart['outputs'][1]['force_overlay'])
                self.assertEqual(chart['outputs'][2]['values'], [20.,30.])
            finally:
                tail.close()
                sink.close()

    def test_replay_restart_preserves_market_and_calculated_bar_alignment(self):
        host = HostAdmission(probe=lambda: None)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'store'
            with local_manager(root, host_admission=host) as manager:
                replays = Replays(manager)
                identifier = replays.create(configuration(manager, SOURCE))['replay_id']
                replays.append(identifier, [event.payload() for event in tape(2)])
                replays.advance(identifier, max_events=1, checkpoint_every=1)
                first = replays.view(identifier, 'chart', styled=True)
                self.assertIsNone(first['preview']['outputs'][0]['presentation']['bar'])
                self.assertTrue(first['preview']['outputs'][1]['presentation']['force_overlay'])
                self.assertEqual(first['preview']['outputs'][1]['presentation']['bar']['close'], 10.)
                self.assertEqual(first['rows'], [])
                replays.advance(identifier, max_events=2, checkpoint_every=1)
            with local_manager(root, host_admission=host) as manager:
                replays = Replays(manager)
                state = replays.advance(identifier, max_events=1, checkpoint_every=1)
                view = replays.view(identifier, 'chart', styled=True)
                candle, bar, _ = state['outputs']
                self.assertTrue(bar['presentation']['force_overlay'])
                self.assertEqual(view['ohlc_rows'][0], (0, candle['output_id'], None))
                self.assertEqual(view['ohlc_rows'][1][2]['close'], 11.)
                self.assertEqual(view['preview']['outputs'][0]['presentation']['bar']['close'], 112.)
                self.assertEqual(replays.view(identifier, 'chart')['ohlc_rows'], view['ohlc_rows'])
                replays.advance(identifier, checkpoint_every=1)
                last = replays.view(identifier, 'chart', 1, 1, styled=True)
                self.assertEqual(last['ohlc_rows'][0][2]['close'], 113.)
                self.assertEqual(last['ohlc_rows'][0][2]['wickcolor'], '#345678FF')
                self.assertIsNone(last['preview'])
