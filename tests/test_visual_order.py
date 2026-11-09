"""Visual order survives the admitted program, persisted replay, and wire codec."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.codec import decode, encode
from packages.contracts.model import IndicatorDeclaration
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.trading import StrategyDeclaration
from packages.contracts.visual_order import VisualOrder
from packages.execution.artifacts import export_program, import_program
from packages.execution.host_memory import HostAdmission
from packages.execution.session import Session
from packages.marketdata import read_bars
from test_chart_scale import script
from test_realtime import configuration, tape


class VisualOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(Path('conformance/visual-signals/bars.json'))

    def test_version_defaults_and_bool_qualifier(self):
        for version in range(1, 7):
            with self.subTest(version=version):
                name = 'study' if version < 5 else 'indicator'
                for kind in (name, 'strategy'):
                    header = f'{kind}("Order",overlay=true'
                    if kind == 'strategy':
                        header += ',default_qty_type=strategy.fixed'
                    program, result = self.session.run(script(version, header + ')'), data=self.data)
                    behind = True if version >= 5 else None
                    default = VisualOrder(False, behind) if version >= 4 else None
                    self.assertEqual(getattr(result.declaration, 'visual_order', None), default)
                    if version < 4:
                        self.assertNotIn('visual_order', encode(result.declaration))
                        with self.assertRaises(OpenPineError) as error:
                            self.session.compile(script(version, header + ',explicit_plot_zorder=true)'))
                        self.assertEqual(error.exception.diagnostic.code, 'VISUAL_ORDER_VERSION')
                        continue
                    explicit, result = self.session.run(
                        script(version, header + ',explicit_plot_zorder=not false)'), data=self.data)
                    self.assertEqual(result.declaration.visual_order, VisualOrder(True, behind))
                    if kind == 'strategy':
                        self.assertEqual(broker_policy(explicit), broker_policy(program))
                    if version == 4:
                        with self.assertRaises(OpenPineError) as error:
                            self.session.compile(script(version, header + ',behind_chart=false)'))
                        self.assertEqual(error.exception.diagnostic.code, 'VISUAL_ORDER_VERSION')
        with self.assertRaises(OpenPineError) as error:
            self.session.compile(script(6, 'indicator("Order",behind_chart=bar_index>0)'))
        self.assertEqual(error.exception.diagnostic.code, 'ARGUMENT_QUALIFIER')

    def test_old_codec_and_combined_options_preserve_saved_ir(self):
        old = {'$type': 'IndicatorDeclaration', 'title': 'Order', 'shorttitle': '', 'overlay': False}
        self.assertEqual(encode(decode(old, IndicatorDeclaration)), old)
        for kind in ('indicator','strategy'):
            with self.subTest(kind=kind):
                sizing = 'default_qty_type=strategy.fixed,' if kind == 'strategy' else ''
                source = script(6, f'{kind}("Order",overlay=true,scale=scale.left,precision=4,{sizing}'
                                   'behind_chart=false,explicit_plot_zorder=true)')
                program = self.session.compile(source)
                restored = import_program(export_program(program))
                result = self.session.execute(restored, data=self.data)
                declaration = result.declaration
                self.assertEqual(declaration.visual_order, VisualOrder(True, False))
                self.assertEqual(declaration.chart_scale, 'left')
                self.assertEqual(declaration.number_format.declaration.precision, 4)
                self.assertEqual(result.outputs[0].values, (10.,20.,30.,40.))
                record_type = StrategyDeclaration if kind == 'strategy' else IndicatorDeclaration
                self.assertEqual(decode(encode(declaration), record_type), declaration)

    def test_ir_cannot_override_the_implicit_behind_chart_default(self):
        program = self.session.compile(script(6, 'indicator("Order",explicit_plot_zorder=true)'))
        header = program.statements[0]
        argument = header.arguments[-1]
        self.assertEqual(argument.origin, 'default')
        forged = replace(header, arguments=(*header.arguments[:-1],
            replace(argument, expression=replace(argument.expression,value=False))))
        runtime = self.session.validate_program(program)
        with self.assertRaises(OpenPineError) as error:
            runtime.prepare(replace(program, statements=(forged,*program.statements[1:])))
        self.assertEqual(error.exception.diagnostic.code, 'IR_VISUAL_ORDER')

    def test_replay_order_and_source_sites_survive_preview_and_reopen(self):
        source = script(6, 'indicator("Order",overlay=true,scale=scale.left,'
                           'behind_chart=false,explicit_plot_zorder=true)',
                        'plot(close)\nplot(close,force_overlay=true)')
        host = HostAdmission(probe=lambda:None)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)/'store'
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                identifier = replay.create(configuration(manager,source))['replay_id']
                replay.append(identifier,[event.payload() for event in tape(2)])
                replay.advance(identifier,max_events=4,checkpoint_every=1)
                opened = replay.view(identifier,'chart',0,1,styled=True)
                self.assertEqual(opened['visual_order'],{'explicit_plot_zorder':True,'behind_chart':False})
                self.assertEqual(opened['chart_scale'],'left')
                sites = [row['output_id'] for row in opened['preview']['outputs']]
                self.assertEqual(sites,[row['output_id'] for row in opened['outputs']])
            with local_manager(root,host_admission=host) as manager:
                replay = Replays(manager)
                replay.advance(identifier,checkpoint_every=1)
                closed = replay.view(identifier,'chart',1,1,styled=True)
                self.assertEqual(closed['visual_order'],opened['visual_order'])
                self.assertIsNone(closed['preview'])
                self.assertEqual([row[2] for row in closed['rows']],sites)
                self.assertEqual([row[3] for row in closed['rows']],[13.,13.])


if __name__ == '__main__':
    unittest.main()
