"""Chart scale preserves versioned declarations, stored IR and raw execution."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.model import IndicatorDeclaration
from packages.contracts.number_format import NumberFormat
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.trading import StrategyDeclaration
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from test_realtime import configuration, tape


def script(version, header, body='plot(close)'):
    return f'//@version={version}\n{header}\n{body}\n'


class ChartScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-signals/bars.json'))

    def test_indicator_positional_and_named_scale_follow_the_pinned_versions(self):
        for version in range(1, 7):
            with self.subTest(version=version):
                name = 'study' if version < 5 else 'indicator'
                entries = self.session.snapshot.references[version]['entries']
                archived = next(entry for entry in entries if entry['name'] == name and entry['category'] == 'functions')
                self.assertIn('scale', {p['name'] for p in archived['parameters']})
                number = '0' if version < 4 else 'format.percent,0'
                source = script(version, f'{name}("Scale","S",true,{number},scale.none)')
                program, result = self.session.run(source, data=self.data)
                self.assertEqual(result.declaration.chart_scale, 'none')
                self.assertTrue(result.declaration.overlay)
                self.assertEqual(result.declaration.number_format.declaration,
                                 NumberFormat(None if version < 4 else 'percent',0))
                self.assertEqual(decode(encode(result.declaration), IndicatorDeclaration),result.declaration)
                self.assertEqual(import_program(export_program(program)),program)
                for mode in ('left','right'):
                    _, other = self.session.run(script(version, f'{name}("Scale",scale=scale.{mode})'),data=self.data)
                    self.assertEqual(other.declaration.chart_scale,mode)
                    self.assertFalse(other.declaration.overlay)
                    self.assertFalse(hasattr(other.declaration,'number_format'))
                    self.assertEqual(other.outputs[0].values,(10.,20.,30.,40.))

    def test_strategy_versions_keep_broker_arguments_and_settings(self):
        for version in range(1,7):
            with self.subTest(version=version):
                entries = self.session.snapshot.references[version]['entries']
                archived = next(entry for entry in entries if entry['name'] == 'strategy' and entry['category'] == 'functions')
                self.assertEqual('scale' in {p['name'] for p in archived['parameters']}, version >= 3)
                header = 'strategy("Scale",overlay=true,default_qty_type=strategy.fixed,default_qty_value=2'
                source = script(version,header+',scale=scale.right,precision=4)')
                if version < 3:
                    with self.assertRaises(OpenPineError) as error:
                        self.session.compile(script(version,header+',scale=scale.right)'))
                    self.assertEqual(error.exception.diagnostic.code,'CHART_SCALE_VERSION')
                    continue
                plain = self.session.compile(script(version,header+')'))
                program,result = self.session.run(source,data=self.data)
                self.assertEqual(broker_policy(plain),broker_policy(program))
                self.assertEqual(result.declaration.chart_scale,'right')
                self.assertEqual(decode(encode(result.declaration),StrategyDeclaration),result.declaration)
                self.assertEqual(result.outputs[0].values,(10.,20.,30.,40.))
        from packages.contracts.intrabar import source_setting
        program = self.session.compile(script(6,'strategy("Scale",scale=scale.left,calc_on_every_tick=true)'))
        self.assertIs(source_setting(program),True)

    def test_invalid_constants_and_forged_ir_are_rejected(self):
        for options,code in (
            ('scale=scale.none','CHART_SCALE_OVERLAY'),
            ('overlay=false,scale=scale.none','CHART_SCALE_OVERLAY'),
            ('scale="scale.left"','CHART_SCALE_VALUE'),
            ('scale=1','CHART_SCALE_VALUE'),
            ('scale=true ? scale.left : scale.right','EXPRESSION_TYPE'),
        ):
            with self.subTest(options=options):
                with self.assertRaises(OpenPineError) as error:
                    self.session.compile(script(6,f'indicator("Scale",{options})'))
                self.assertEqual(error.exception.diagnostic.code,code)
        program = self.session.compile(script(6,'indicator("Scale",scale=scale.left)'))
        runtime = self.session.validate_program(program)
        header = program.statements[0]
        option = header.arguments[-1]
        for value in ('none','not-a-scale'):
            forged = replace(header,arguments=(*header.arguments[:-1],
                replace(option,expression=replace(option.expression,value=value))))
            with self.assertRaises(OpenPineError) as error:
                runtime.prepare(replace(program,statements=(forged,*program.statements[1:])))
            self.assertEqual(error.exception.diagnostic.code,'IR_CHART_SCALE')

    def test_native_checkpoint_and_replay_keep_scale_and_raw_values(self):
        source = script(6,'indicator("Scale",overlay=true,scale=scale.left,format=format.percent)',
                        'plot(close*1000000000,display=display.price_scale)\nplot(close,force_overlay=true)')
        program = import_program(export_program(self.session.compile(source)))
        runtime = self.session.validate_program(program)
        frames = [BarFrame(self.data.context_id,index,bar.time,bar.close,bar.open,bar.high,bar.low,bar.volume)
                  for index,bar in enumerate(self.data.bars)]
        evaluation = runtime.prepare(program)
        for frame in frames[:2]:
            evaluation.step(frame)
        restored = runtime.prepare(program)
        restored.restore(loads(canonical(evaluation.checkpoint())))
        for frame in frames[2:]:
            restored.step(frame)
        result = restored.finish(data_identity(self.data))
        self.assertEqual(result,self.session.execute(program,data=self.data))
        self.assertEqual(result.declaration.chart_scale,'left')
        self.assertEqual(result.outputs[0].values,(10e9,20e9,30e9,40e9))
        self.assertTrue(result.outputs[1].force_overlay)
        self.assertEqual(result.outputs[1].values,(10.,20.,30.,40.))

        host = HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                identifier = replay.create(configuration(manager,source))['replay_id']
                replay.append(identifier,[event.payload() for event in tape(2)])
                replay.advance(identifier,max_events=4,checkpoint_every=1)
                opened = replay.view(identifier,'chart',0,1,styled=True)
                self.assertEqual(opened['chart_scale'],'left')
                self.assertEqual([o['value'] for o in opened['preview']['outputs']],[12e9,12.])
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                replay.advance(identifier,checkpoint_every=1)
                closed = replay.view(identifier,'chart',1,1,styled=True)
                self.assertEqual(closed['chart_scale'],opened['chart_scale'])
                self.assertIsNone(closed['preview'])
                self.assertEqual([row[3] for row in closed['rows']],[13e9,13.])


if __name__ == '__main__':
    unittest.main()
