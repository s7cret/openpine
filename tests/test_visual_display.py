"""Visual visibility is frozen through native state, durable rows and replay."""

import copy
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, RunResult
from packages.contracts.codec import decode, encode
from packages.contracts.inputs import settings_for
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.queries import ResultReader, legacy_response
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink, series_rows
from test_realtime import configuration, tape
from tools.bands_fixtures import source


BODY = """show=input.bool(true)
a=plot(close,offset=-1,show_last=3,trackprice=true,force_overlay=true,display=show ? display.all : display.none)
b=plot(close+2,offset=1,force_overlay=true,display=display.none)
fill(a,b,#12345680,show_last=2,display=display.all)
fill(a,b,close+8,close-5,#12345680,na,display=display.none)
plot(close+1)
"""
DISPLAY = ('none', 'none', 'all', 'none', None)


class VisualDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-bands/bars.json'))
        cls.frames = tuple(BarFrame(cls.data.context_id, i, bar.time, bar.close,
                                    bar.open, bar.high, bar.low, bar.volume)
                           for i, bar in enumerate(cls.data.bars))

    def displays(self, outputs):
        return tuple(getattr(output, 'display', None) for output in outputs)

    def test_saved_ir_and_native_checkpoint_freeze_the_input_mode(self):
        for version in (5, 6):
            with self.subTest(version=version):
                program, visible = self.session.run(source(version, BODY), data=self.data)
                _, hidden = self.session.run(source(version, BODY), data=self.data, inputs={'show': False})
                self.assertEqual(self.displays(visible.outputs), ('all', *DISPLAY[1:]))
                self.assertEqual(self.displays(hidden.outputs), DISPLAY)
                self.assertEqual([out.values for out in visible.outputs], [out.values for out in hidden.outputs])
                a, b, solid, gradient, _ = hidden.outputs
                self.assertEqual(solid.references, (a.output_id, b.output_id))
                self.assertEqual(gradient.references, solid.references)

                program = import_program(export_program(program))
                runtime = self.session.validate_program(program)
                settings = settings_for(program, {'show': False})
                evaluation = runtime.prepare(program, settings=settings)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                state = loads(canonical(evaluation.checkpoint()))
                forged = copy.deepcopy(state)
                forged['presentations'][a.output_id]['display'] = 'all'
                untouched = runtime.prepare(program, settings=settings)
                with self.assertRaises(ValueError):
                    untouched.restore(forged)
                self.assertEqual(untouched.count, 0)

                resumed = runtime.prepare(program, settings=settings)
                resumed.restore(state)
                for frame in self.frames[2:]:
                    resumed.step(frame)
                result = resumed.finish(data_identity(self.data))
                self.assertEqual(result.outputs, hidden.outputs)
                self.assertEqual(decode(encode(result), RunResult), result)

    def test_parquet_chart_and_full_export_keep_hidden_data(self):
        program, native = self.session.run(source(6, BODY), data=self.data, inputs={'show': False})
        runtime = self.session.validate_program(program)
        settings = settings_for(program, {'show': False})
        data = self.data

        class Source:
            identity = SimpleNamespace(count=len(data.bars), context_id=data.context_id,
                                       symbol=data.symbol, timeframe=data.timeframe)
            manifest = {'metadata': {'bar_schema': data.schema_version}}

            def __iter__(self):
                return iter(data.bars)

            def rows(self, offset, limit):
                return iter(data.bars[offset:offset+limit])

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            objects = ObjectStore(root/'objects')
            prefix, tail = ResultSink(objects, root/'prefix'), ResultSink(objects, root/'tail')
            try:
                evaluation = runtime.prepare(program, output_sink=prefix, settings=settings)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                saved = loads(canonical(prefix.checkpoint(2)))
                self.assertEqual(saved['outputs'][0]['presentation']['display'], 'none')
                tail.restore(saved, 2)
                resumed = runtime.prepare(program, output_sink=tail, settings=settings)
                resumed.restore(loads(canonical(evaluation.checkpoint())))
                for frame in self.frames[2:]:
                    resumed.step(frame)

                fields = asdict(native)
                summary = tail.finish({key:fields[key] for key in ('observation_count', 'declaration', 'identity', 'program_sha256')})
                response = dict(status='executed', python='3.14.8', result=summary,
                                snapshot_sha256='0'*64, artifact_ref='program')
                reader = ResultReader(None, objects, SimpleNamespace(source=lambda _:Source()), series_rows,
                                      SimpleNamespace(slot=lambda _:nullcontext()))
                reader.summary = lambda _:response
                first = reader.chart('job', 0, 2)['outputs']
                self.assertEqual(tuple(row.get('display') for row in first), DISPLAY)
                self.assertEqual(first[0]['values'], [10., 20.])
                self.assertEqual(first[0]['halo'][0]['value'], 30.)
                self.assertEqual(first[3]['gradients'][0]['top_value'], 18.)

                artifact = SimpleNamespace(get=lambda _:export_program(program))
                read_series = lambda _, saved, **kwargs:series_rows(objects, saved, **kwargs)
                exported = legacy_response(artifact, response, Source(), read_series)['result']['outputs']
                self.assertNotIn('display', exported[-1])
                expected = tuple({key:value for key, value in row.items() if key != 'display' or value is not None}
                                 for row in fields['outputs'])
                self.assertEqual(canonical(exported), canonical(expected))
            finally:
                tail.close()
                prefix.close()

    def test_replay_reopens_with_hidden_preview_and_confirmed_values(self):
        host = HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)/'store'
            with local_manager(root, host_admission=host) as manager:
                replays = Replays(manager)
                payload = configuration(manager, source(6, BODY))
                identifier = replays.create({**payload, 'inputs':{'show':False}})['replay_id']
                replays.append(identifier, [event.payload() for event in tape(2)])
                opened = replays.advance(identifier, max_events=4, checkpoint_every=1)
                self.assertEqual(opened['confirmed_bars'], 1)
                hidden = opened['outputs'][0]
                self.assertEqual(hidden['presentation']['display'], 'none')
                preview = opened['preview']['outputs'][0]
                self.assertEqual((preview['value'], preview['presentation']['display']), (12., 'none'))
                view = replays.view(identifier, 'chart', 0, 1, styled=True)
                self.assertIn((0, 0, hidden['output_id'], 11.), view['rows'])

            with local_manager(root, host_admission=host) as manager:
                replays = Replays(manager)
                self.assertEqual(replays.status(identifier)['preview'], opened['preview'])
                final = replays.advance(identifier, checkpoint_every=1)
                self.assertIsNone(final['preview'])
                self.assertEqual(final['outputs'][0]['presentation']['display'], 'none')
                view = replays.view(identifier, 'chart', 1, 1, styled=True)
                self.assertIn((1, 60000, hidden['output_id'], 13.), view['rows'])


if __name__ == '__main__':
    unittest.main()
