"""Versioned number formatting through typed IR, checkpoint and replay."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import BarFrame, HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.inputs import configured_result, settings_for
from packages.contracts.model import IndicatorDeclaration
from packages.contracts.number_format import FIELDS, NumberFormat, available, instrument_precision
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.trading import StrategyDeclaration
from packages.contracts.wire import canonical, loads
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import data_identity, read_bars
from test_realtime import configuration, tape


def script(version, body):
    return f'//@version={version}\n{body}\n'


HEADER = 'indicator("Numbers",format=format.percent,precision=4)'
BODY = """places=input.int(0)
mode=input.string("price")
plot(close-20,"scalar",precision=places,format=mode,display=display.data_window,offset=-1)
plotshape(close>20,"shape",format=format.percent,display=display.data_window)
plotchar(close,"char",precision=16)
plotarrow(close-20,"arrow",format=format.volume)
plotbar(open,high,low,close,"bars",precision=places,display=display.price_scale)
plotcandle(open,high,low,close,"candles",format=format.volume,precision=0)
"""


class NumberFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-signals/bars.json'))
        cls.frames = tuple(BarFrame(cls.data.context_id, index, bar.time, bar.close,
                                    bar.open, bar.high, bar.low, bar.volume)
                           for index, bar in enumerate(cls.data.bars))

    def test_declaration_availability_and_independent_strategy_policy(self):
        for version in range(1, 7):
            name = 'study' if version < 5 else 'indicator'
            opts = ',"",false,0' if version < 4 else ',"",false,format.inherit,16'
            with self.subTest(version=version):
                entries = {entry['name']:entry for entry in self.session.snapshot.references[version]['entries']
                           if entry['category']=='functions'}
                operations = {name:'harness.indicator','strategy':'simulation.strategy','plot':'colors.plot',
                              'plotshape':'markers.plotshape','plotchar':'markers.plotchar','plotarrow':'markers.plotarrow',
                              'plotbar':'ohlc.plotbar','plotcandle':'ohlc.plotcandle'}
                for public, operation in operations.items():
                    params = entries[public]['parameters']
                    self.assertEqual(set(available(operation,version)), {p['name'] for p in params} & set(FIELDS), public)
                _, result = self.session.run(script(version, f'{name}("Numbers"{opts})\nplot(close)'), data=self.data)
                declaration = result.declaration
                expected = NumberFormat(precision=0) if version < 4 else NumberFormat('inherit',16)
                self.assertEqual(declaration.number_format.declaration, expected)
                self.assertEqual(decode(encode(declaration), IndicatorDeclaration), declaration)
                self.assertEqual(result.outputs[0].values, (10.,20.,30.,40.))
                if version < 5:
                    with self.assertRaises(OpenPineError) as rejected:
                        self.session.compile(script(version,f'{name}("Numbers")\nplot(close,precision=2)'))
                    self.assertEqual(rejected.exception.diagnostic.code,'NUMBER_FORMAT_VERSION')

                header = 'strategy("Numbers",default_qty_type=strategy.fixed'
                if version < 3:
                    with self.assertRaises(OpenPineError) as rejected:
                        self.session.compile(script(version,header+',precision=3)\nplot(close)'))
                    self.assertEqual(rejected.exception.diagnostic.code,'NUMBER_FORMAT_VERSION')
                    continue
                plain = self.session.compile(script(version,header+')\nplot(close)'))
                formatted, result = self.session.run(script(version,header+',precision=3)\nplot(close)'),data=self.data)
                self.assertEqual(broker_policy(formatted),broker_policy(plain))
                self.assertEqual(result.declaration.number_format.declaration,NumberFormat(precision=3))
                self.assertEqual(decode(encode(result.declaration), StrategyDeclaration),result.declaration)
                self.assertEqual(result.outputs[0].values,(10.,20.,30.,40.))
        from packages.contracts.intrabar import source_setting
        intrabar = self.session.compile(script(6,'strategy("Numbers",default_qty_type=strategy.fixed,'
                                                   'calc_on_every_tick=true,precision=4)\nplot(close)'))
        self.assertIs(source_setting(intrabar), True)
        self.assertEqual(instrument_precision('0.0025000'), 4)
        self.assertIsNone(instrument_precision(None))

    def test_saved_ir_freezes_inputs_for_six_plot_families_and_native_checkpoint(self):
        for version in (5, 6):
            with self.subTest(version=version):
                program = self.session.compile(script(version, HEADER+'\n'+BODY))
                program = import_program(export_program(program))
                runtime = self.session.validate_program(program)
                settings = settings_for(program, {'places': 2, 'mode': 'percent'})
                evaluation = runtime.prepare(program,settings=settings)
                for frame in self.frames[:2]:
                    evaluation.step(frame)
                restored = runtime.prepare(program,settings=settings)
                restored.restore(loads(canonical(evaluation.checkpoint())))
                for frame in self.frames[2:]:
                    restored.step(frame)
                result = restored.finish(data_identity(self.data))
                metadata = result.declaration.number_format
                self.assertEqual(metadata.declaration, NumberFormat('percent',4))
                self.assertEqual([row.output_id for row in metadata.plots],[out.output_id for out in result.outputs])
                self.assertEqual([row.style for row in metadata.plots],[NumberFormat('percent',2),
                    NumberFormat('percent'),NumberFormat(precision=16),NumberFormat('volume'),
                    NumberFormat(precision=2),NumberFormat('volume',0)])
                self.assertEqual(result.outputs[0].values,(-10.,0.,10.,20.))
                self.assertEqual(result.outputs[0].display,('data_window',))
                self.assertEqual(result.outputs[1].values,(False,False,True,True))
                self.assertEqual(result.outputs[-1].bars[-1].close,40.)
                direct = self.session.execute(program,data=self.data,inputs={'places':2,'mode':'percent'})
                self.assertEqual(configured_result(result,settings),direct)

    def test_forged_format_defaults_and_out_of_range_frozen_values_are_rejected(self):
        program = self.session.compile(script(6,HEADER+'\nplaces=input.int(0)\nplot(close,precision=places)'))
        runtime = self.session.validate_program(program)
        call = program.statements[-1]
        mode, places = call.arguments[-2:]
        forged = replace(call, arguments=(*call.arguments[:-2],
                         replace(mode, expression=replace(mode.expression,value='price')), places))
        with self.assertRaises(OpenPineError) as rejected:
            runtime.prepare(replace(program,statements=(*program.statements[:-1],forged)))
        self.assertEqual(rejected.exception.diagnostic.code,'IR_NUMBER_FORMAT')
        with self.assertRaises(OpenPineError) as rejected:
            runtime.prepare(program,settings=settings_for(program,{'places':17}))
        self.assertEqual(rejected.exception.diagnostic.code,'NUMBER_FORMAT_INPUT')
        for invalid, code in (('precision=-1','NUMBER_FORMAT_VALUE'),('format=format.inherit','NUMBER_FORMAT_VALUE'),
                              ('precision=bar_index','ARGUMENT_QUALIFIER')):
            with self.assertRaises(OpenPineError) as rejected:
                self.session.compile(script(6,HEADER+f'\nplot(close,{invalid})'))
            self.assertEqual(rejected.exception.diagnostic.code,code)

    def test_durable_replay_preserves_one_format_across_preview_close_and_reopen(self):
        source = script(6, HEADER+'\nplaces=input.int(0)\nplot(close,precision=places,display=display.all - display.pane)')
        host = HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                payload = {**configuration(manager,source),'inputs':{'places':16}}
                identifier = replay.create(payload)['replay_id']
                replay.append(identifier,[event.payload() for event in tape(2)])
                replay.advance(identifier,max_events=4,checkpoint_every=1)
                opened = replay.view(identifier,'chart',0,1,styled=True)
                self.assertEqual(opened['number_format']['declaration'],{'format':'percent','precision':4})
                self.assertEqual(opened['number_format']['plots'][0]['style'],{'format':None,'precision':16})
                self.assertIsNone(opened['price_precision'])
                self.assertEqual(opened['preview']['outputs'][0]['value'],12.)
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                replay.advance(identifier,checkpoint_every=1)
                closed = replay.view(identifier,'chart',1,1,styled=True)
                self.assertEqual(closed['number_format'],opened['number_format'])
                self.assertIsNone(closed['preview'])
                self.assertEqual(closed['rows'][0][3],13.)


if __name__ == '__main__':
    unittest.main()
