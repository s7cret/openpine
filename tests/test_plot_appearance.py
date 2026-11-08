"""Plot appearance dispatch, typed results, and durable terminal samples."""

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
from tools.bands_fixtures import source


APPEARANCE = """a=plot(close,style=plot.style_area,offset=-1,show_last=2,histbase=25,trackprice=true,
linestyle=plot.linestyle_dashed,force_overlay=true,color=close<40 ? #123456 : #ABCDEF)
b=plot(close+2,style=plot.style_cross,join=true,force_overlay=true)
fill(a,b,#12345680,show_last=2)
plot(close,style=plot.style_stepline_diamond,linestyle=plot.linestyle_dotted)
plot(close<40 ? close : na,trackprice=true)
"""


class PlotAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/colors/bars.json'))
        cls.frames = tuple(BarFrame(cls.data.context_id, i, b.time, b.close, b.open, b.high, b.low, b.volume)
                           for i,b in enumerate(cls.data.bars))

    def test_legacy_plot_dispatch_preserves_sixth_argument_and_supports_both_orders(self):
        body = ('plot(close,"",#123456,2,line,90)\n'
                'plot(close,"",#123456,2,areabr,true,0,25.0,-1,false)\n'
                'a=plot(close,"",#123456,areabr,2,false,0,25.0,1,true)\n'
                'b=plot(close+2)\nfill(a,b,#12345680)')
        for version in (1,2):
            with self.subTest(version=version):
                program,result = self.session.run(source(version,body),data=self.data)
                old,width_first,style_first,upper,fill = result.outputs
                self.assertFalse(hasattr(old,'trackprice'))
                self.assertEqual(old.colors,('#1234561A',)*4)
                self.assertEqual((width_first.trackprice,width_first.histbase,width_first.offset),(True,25.,-1))
                self.assertEqual((style_first.style,style_first.linewidth,style_first.offset),('areabr',2,1))
                self.assertEqual(fill.references,(style_first.output_id,upper.output_id))
                self.session.validate_program(import_program(export_program(program)))

    def test_native_checkpoint_rejects_valid_but_different_appearance(self):
        program,result = self.session.run(source(6,APPEARANCE),data=self.data)
        self.assertEqual(decode(encode(result),RunResult),result)
        runtime = self.session.validate_program(import_program(export_program(program)))
        original = runtime.prepare(program)
        for frame in self.frames[:2]:
            original.step(frame)
        state = loads(canonical(original.checkpoint()))
        resumed = runtime.prepare(program)
        resumed.restore(state)
        for frame in self.frames[2:]:
            resumed.step(frame)
        self.assertEqual(resumed.finish(data_identity(self.data)),result)
        for field,value in [('histbase',26),('join',True),('trackprice',False),('linestyle','solid')]:
            bad = copy.deepcopy(state)
            bad['presentations'][result.outputs[0].output_id][field] = value
            untouched = runtime.prepare(program)
            with self.assertRaises(ValueError):
                untouched.restore(bad)
            self.assertEqual(untouched.count,0)

    def test_parquet_checkpoint_chart_and_full_export_use_the_same_appearance(self):
        program,expected = self.session.run(source(6,APPEARANCE),data=self.data)
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

        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            objects=ObjectStore(root/'objects')
            prefix,tail=ResultSink(objects,root/'prefix'),ResultSink(objects,root/'tail')
            try:
                evaluation=runtime.prepare(program,output_sink=prefix)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                prefix_state=loads(canonical(prefix.checkpoint(2)))
                self.assertEqual(prefix_state['outputs'][0]['last_color'],'#123456FF')
                corrupted=copy.deepcopy(prefix_state)
                corrupted['outputs'][0]['last_color']='invalid'
                with self.assertRaises(ValueError):
                    ResultSink(objects,root/'corrupted').restore(corrupted,2)
                tail.restore(prefix_state,2)
                restored=runtime.prepare(program,output_sink=tail)
                restored.restore(loads(canonical(evaluation.checkpoint())))
                for frame in self.frames[2:]:
                    restored.step(frame)
                native=asdict(expected)
                summary=tail.finish({k:native[k] for k in ('observation_count','declaration','identity','program_sha256')})
                response=dict(status='executed',python='3.14.8',result=summary,snapshot_sha256='0'*64,artifact_ref='program')
                reader=ResultReader(None,objects,SimpleNamespace(source=lambda _:Source()),series_rows,
                                    SimpleNamespace(slot=lambda _:nullcontext()))
                reader.summary=lambda _:response
                for start in (0,2):
                    first,join,fill,diamond,missing=reader.chart('job',start,2)['outputs']
                    self.assertEqual((first['last'],first['last_color'],first['linestyle']),(40.,'#ABCDEFFF','dashed'))
                    self.assertEqual((join['join'],fill['show_last'],missing['last']),(True,2,None))
                    if start==2:
                        self.assertEqual([(p['index'],p['value']) for p in diamond['halo']],[(-1,20.)])
                artifact=SimpleNamespace(get=lambda _:export_program(program))
                read_series=lambda _,saved,**kwargs:series_rows(objects,saved,**kwargs)
                exported=legacy_response(artifact,response,Source(),read_series)['result']
                self.assertEqual(canonical(exported['outputs']),canonical(native['outputs']))
            finally:
                tail.close()
                prefix.close()

    def test_replay_keeps_confirmed_track_sample_until_the_bar_closes(self):
        host=HostAdmission(probe=lambda:None)
        body='plot(close>12 ? na : close,color=close<12 ? #112233 : #445566,trackprice=true)'
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replays=Replays(manager)
                identifier=replays.create(configuration(manager,source(6,body)))['replay_id']
                replays.append(identifier,[event.payload() for event in tape(2)])
                opened=replays.advance(identifier,max_events=4,checkpoint_every=1)
                self.assertEqual((opened['outputs'][0]['last'],opened['outputs'][0]['last_color']),(11.,'#112233FF'))
                self.assertEqual((opened['preview']['outputs'][0]['value'],
                                  opened['preview']['outputs'][0]['presentation']['color']),(12.,'#445566FF'))
            with local_manager(root,host_admission=host) as manager:
                replays=Replays(manager)
                self.assertEqual(replays.status(identifier)['preview'],opened['preview'])
                final=replays.advance(identifier,checkpoint_every=1)
                self.assertIsNone(final['preview'])
                self.assertEqual((final['outputs'][0]['last'],final['outputs'][0]['last_color']),(None,'#445566FF'))


if __name__=='__main__':
    unittest.main()
