"""Validate gradient masks and stops through typed artifacts and durable results."""

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


BODY = """a=plot(close,offset=-1,show_last=3)
b=plot(close+2,offset=1)
fill(a,b,close+8,close-5,#12345680,close<40 ? #0000FF : na,fillgaps=true)
hi=hline(30)
lo=hline(5)
fill(hi,lo,close,30,#76543280,na)
"""


class GradientFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(Path('conformance/visual-bands/bars.json'))
        cls.frames=tuple(BarFrame(cls.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume)
                         for i,b in enumerate(cls.data.bars))

    def test_saved_ir_and_checkpoint_restore_both_masks(self):
        for version in (5,6):
            with self.subTest(version=version):
                program,result=self.session.run(source(version,BODY),data=self.data)
                self.assertEqual(decode(encode(result),RunResult),result)
                a,b,gradient,high,low,levels=result.outputs
                self.assertEqual((gradient.mask,gradient.references),('plot',(a.output_id,b.output_id)))
                self.assertEqual((levels.mask,levels.references),('hline',(high.output_id,low.output_id)))
                self.assertEqual([sample.top_value for sample in gradient.gradients],[18.,28.,38.,48.])
                self.assertEqual([sample.bottom_color for sample in gradient.gradients],['#0000FFFF']*3+[None])
                program=import_program(export_program(program))
                runtime=self.session.validate_program(program)
                initial=runtime.prepare(program)
                for frame in self.frames[:2]:
                    initial.step(frame)
                state=loads(canonical(initial.checkpoint()))
                resumed=runtime.prepare(program)
                resumed.restore(state)
                for frame in self.frames[2:]:
                    resumed.step(frame)
                self.assertEqual(resumed.finish(data_identity(self.data)),result)
                for field,value in [('mask','hline'),('fillgaps',False),('references',[a.output_id,high.output_id]),
                                    ('gradients',[dict(asdict(gradient.gradients[0]),top_value=True)]*2)]:
                    bad=copy.deepcopy(state)
                    bad['presentations'][gradient.output_id][field]=value
                    untouched=runtime.prepare(program)
                    with self.assertRaises(ValueError):
                        untouched.restore(bad)
                    self.assertEqual(untouched.count,0)

    def test_parquet_prefix_chart_and_export_keep_original_stop_order(self):
        program,expected=self.session.run(source(6,BODY),data=self.data)
        runtime=self.session.validate_program(program)
        data=self.data

        class Source:
            identity=SimpleNamespace(count=len(data.bars),context_id=data.context_id,
                                     symbol=data.symbol,timeframe=data.timeframe)
            manifest={'metadata':{'bar_schema':data.schema_version}}

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
                saved=loads(canonical(prefix.checkpoint(2)))
                bad=copy.deepcopy(saved)
                bad['outputs'][2]['presentation']['mask']='hline'
                with self.assertRaises(ValueError):
                    ResultSink(objects,root/'bad').restore(bad,2)
                tail.restore(saved,2)
                resumed=runtime.prepare(program,output_sink=tail)
                resumed.restore(loads(canonical(evaluation.checkpoint())))
                for frame in self.frames[2:]:
                    resumed.step(frame)
                native=asdict(expected)
                summary=tail.finish({k:native[k] for k in ('observation_count','declaration','identity','program_sha256')})
                response=dict(status='executed',python='3.14.8',result=summary,snapshot_sha256='0'*64,artifact_ref='program')
                reader=ResultReader(None,objects,SimpleNamespace(source=lambda _:Source()),series_rows,
                                    SimpleNamespace(slot=lambda _:nullcontext()))
                reader.summary=lambda _:response
                for start in (0,2):
                    window=reader.chart('job',start,2)
                    for index in (2,5):
                        self.assertEqual(window['outputs'][index]['gradients'],
                                         list(native['outputs'][index]['gradients'][start:start+2]))
                        self.assertEqual(window['outputs'][index]['values'],[None,None])
                    if start==0:
                        self.assertEqual(window['outputs'][0]['halo'][0]['value'],30.)
                    else:
                        self.assertEqual(window['outputs'][1]['halo'][0]['value'],22.)
                plain=list(series_rows(objects,summary,1,2))
                self.assertIsNone(plain[2][3])
                artifact=SimpleNamespace(get=lambda _:export_program(program))
                read_series=lambda _,saved,**kwargs:series_rows(objects,saved,**kwargs)
                exported=legacy_response(artifact,response,Source(),read_series)['result']
                self.assertEqual(canonical(exported['outputs']),canonical(native['outputs']))
            finally:
                tail.close()
                prefix.close()

    def test_open_replay_gradient_is_replaced_by_confirmed_stops(self):
        host=HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replays=Replays(manager)
                identifier=replays.create(configuration(manager,source(6,BODY)))['replay_id']
                replays.append(identifier,[event.payload() for event in tape(2)])
                opened=replays.advance(identifier,max_events=4,checkpoint_every=1)
                self.assertEqual(opened['confirmed_bars'],1)
                plot=opened['outputs'][2]['output_id']
                provisional=opened['preview']['outputs'][2]
                self.assertEqual(provisional['presentation']['gradient']['top_value'],20.)
                self.assertIsNone(provisional['value'])
                view=replays.view(identifier,'chart',0,1,styled=True)
                self.assertEqual(view['gradient_rows'][0][0:2],(0,plot))
                self.assertEqual(view['gradient_rows'][0][2]['top_value'],19.)
            with local_manager(root,host_admission=host) as manager:
                replays=Replays(manager)
                self.assertEqual(replays.status(identifier)['preview'],opened['preview'])
                final=replays.advance(identifier,checkpoint_every=1)
                self.assertIsNone(final['preview'])
                view=replays.view(identifier,'chart',1,1,styled=True)
                self.assertEqual(view['gradient_rows'][0][0:2],(1,plot))
                self.assertEqual(view['gradient_rows'][0][2]['top_value'],21.)
                self.assertEqual(view['gradient_rows'][1][2]['top_value'],13.)


if __name__=='__main__':
    unittest.main()
