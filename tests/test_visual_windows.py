"""Shared presentation windows, durable projection, and replay boundaries."""

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
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.queries import ResultReader, legacy_response
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink, series_rows
from test_realtime import configuration, tape


SOURCE = """//@version=6
indicator("Windows")
a=plot(close,offset=-1,show_last=2)
b=plot(close+2,offset=1)
fill(a,b,#12345680,show_last=2,fillgaps=true)
plot(close,offset=-2,force_overlay=true)
plotcandle(open,high,low,close,show_last=1,force_overlay=true)
barcolor(#12345680,offset=-1,show_last=1)
bgcolor(#12345680,show_last=0,force_overlay=true)
"""


class VisualWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-ohlc/bars.json'))
        cls.frames = tuple(BarFrame(cls.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume)
                           for i,b in enumerate(cls.data.bars))

    def test_saved_program_and_checkpoint_retain_windowed_output(self):
        program, expected = self.session.run(SOURCE, 'windows.pine', data=self.data)
        self.assertEqual(decode(encode(expected), RunResult), expected)
        program = import_program(export_program(program))
        runtime = self.session.validate_program(program)
        initial = runtime.prepare(program)
        for frame in self.frames[:2]:
            initial.step(frame)
        state = loads(canonical(initial.checkpoint()))
        restored = runtime.prepare(program)
        restored.restore(state)
        for frame in self.frames[2:]:
            restored.step(frame)
        self.assertEqual(restored.finish(data_identity(self.data)), expected)
        a,b,fill,plain,candle,bar,background = expected.outputs
        self.assertEqual((a.offset, a.show_last, b.offset, b.show_last), (-1,2,1,None))
        self.assertEqual((plain.offset, plain.show_last, plain.force_overlay), (-2,None,True))
        self.assertEqual((fill.references, fill.show_last, fill.fillgaps), ((a.output_id,b.output_id),2,True))
        self.assertEqual((candle.show_last,bar.show_last,background.show_last), (1,1,0))
        self.assertEqual(tuple(point.close for point in candle.bars), (10.,20.,30.,40.))
        for output,field,value in ((a,'offset',0),(fill,'show_last',3),(plain,'show_last',2),
                                   (candle,'show_last',2),(background,'show_last',1)):
            bad = copy.deepcopy(state)
            bad['presentations'][output.output_id][field] = value
            untouched = runtime.prepare(program)
            with self.assertRaises(ValueError):
                untouched.restore(bad)
            self.assertEqual(untouched.count,0)

    def test_parquet_resume_supplies_both_halos_and_preserves_full_export(self):
        program, expected = self.session.run(SOURCE, 'windows.pine', data=self.data)
        runtime = self.session.validate_program(program)
        data = self.data

        class Source:
            identity = SimpleNamespace(count=len(data.bars),context_id=data.context_id,
                                       symbol=data.symbol,timeframe=data.timeframe)
            manifest = {'metadata': {'bar_schema': data.schema_version}}

            def __iter__(self):
                return iter(data.bars)

            def rows(self, offset, limit):
                return iter(data.bars[offset:offset+limit])

        with TemporaryDirectory() as temp:
            root = Path(temp)
            objects = ObjectStore(root/'objects')
            prefix, tail = ResultSink(objects,root/'prefix'), ResultSink(objects,root/'tail')
            try:
                evaluation = runtime.prepare(program,output_sink=prefix)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                tail.restore(loads(canonical(prefix.checkpoint(2))),2)
                resumed = runtime.prepare(program,output_sink=tail)
                resumed.restore(loads(canonical(evaluation.checkpoint())))
                for frame in self.frames[2:]:
                    resumed.step(frame)
                native = asdict(expected)
                summary = tail.finish({k:native[k] for k in ('observation_count','declaration','identity','program_sha256')})
                response = dict(status='executed',python='3.14.8',result=summary,
                                snapshot_sha256='0'*64,artifact_ref='program')
                reader = ResultReader(None,objects,SimpleNamespace(source=lambda _:Source()),series_rows,
                                      SimpleNamespace(slot=lambda _:nullcontext()))
                reader.summary = lambda _:response
                left, right, fill, plain, candle, *_ = reader.chart('job',1,2)['outputs']
                self.assertEqual(left['values'],[20.,30.])
                self.assertEqual(right['values'],[22.,32.])
                self.assertEqual([(p['index'],p['value']) for p in left['halo']],[(2,40.)])
                self.assertEqual([(p['index'],p['value']) for p in right['halo']],[(-1,12.)])
                self.assertEqual([(p['index'],p['value']) for p in plain['halo']],[(2,40.)])
                self.assertEqual((fill['show_last'],candle['show_last']), (2,1))
                artifact = SimpleNamespace(get=lambda _:export_program(program))
                read_series = lambda _,saved,**kwargs:series_rows(objects,saved,**kwargs)
                exported = legacy_response(artifact,response,Source(),read_series)['result']
                self.assertEqual(canonical(exported['outputs']),canonical(native['outputs']))
            finally:
                tail.close()
                prefix.close()

    def test_replay_preview_and_confirmation_preserve_windowed_descriptors(self):
        host = HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as temp:
            root = Path(temp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replays = Replays(manager)
                identifier = replays.create(configuration(manager,SOURCE))['replay_id']
                replays.append(identifier,[event.payload() for event in tape(2)])
                opened = replays.advance(identifier,max_events=4,checkpoint_every=1)
                self.assertEqual(opened['confirmed_bars'],1)
                a,_,fill,_,candle,bar,background = opened['preview']['outputs']
                self.assertEqual((a['value'],a['presentation']['offset'],a['presentation']['show_last']), (12.,-1,2))
                self.assertEqual((fill['presentation']['show_last'],bar['presentation']['show_last'],
                                  background['presentation']['show_last']), (2,1,0))
                self.assertEqual(candle['presentation']['bar']['close'],12.)
            with local_manager(root,host_admission=host) as manager:
                replays = Replays(manager)
                self.assertEqual(replays.status(identifier)['preview'],opened['preview'])
                final = replays.advance(identifier,checkpoint_every=1)
                view = replays.view(identifier,'chart',0,1,styled=True)
                a,_,fill,_,candle,*_ = final['outputs']
                self.assertEqual(final['confirmed_bars'],2)
                self.assertIsNone(view['preview'])
                self.assertEqual([(p['index'],p['value']) for p in view['visual_halos'][a['output_id']]],[(1,13.)])
                self.assertEqual((fill['presentation']['show_last'],candle['presentation']['show_last']), (2,1))


if __name__ == '__main__':
    unittest.main()
