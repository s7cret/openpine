import unittest
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.execution.artifacts import export_program, import_program
from packages.execution.session import Session


class DynamicRequestDeclarationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def compile(self, version, header, arguments):
        return self.session.compile(
            f'//@version={version}\n{header}{arguments})\nplot(close)\n', 'request-mode.pine')

    def test_header_modes_survive_artifact_round_trip(self):
        for version in (5, 6):
            for header in ('indicator("Mode"', 'strategy("Mode",default_qty_type=strategy.fixed'):
                for arguments, mode in (('', version == 6), (',dynamic_requests=false', False),
                                        (',dynamic_requests=2 > 1', True),
                                        (',dynamic_requests=not (false or 2 > 3) and true', True)):
                    with self.subTest(version=version, header=header, arguments=arguments):
                        program = self.compile(version, header, arguments)
                        restored = import_program(export_program(program))
                        self.session.validate_program(restored)
                        self.assertEqual(restored, program)
                        self.assertIs(restored.identity.dynamic_requests_enabled, mode)

    def test_invalid_declarations(self):
        cases = [(version, f'{name}("Mode"', ',dynamic_requests=true', 'DYNAMIC_REQUESTS_VERSION')
                 for version in range(1, 5) for name in ('study', 'strategy')]
        cases.extend((6, 'indicator("Mode"', arguments, code) for arguments, code in (
            (',dynamic_requests=close > 0', 'DYNAMIC_REQUESTS_TYPE'),
            (',dynamic_requests=1', 'DYNAMIC_REQUESTS_TYPE'),
            (',dynamic_requests=true,dynamic_requests=false', 'DUPLICATE_ARGUMENT'),
            (',dynamic_requests=true,false', 'POSITIONAL_AFTER_NAMED')))
        for version, header, arguments, code in cases:
            with self.subTest(version=version, header=header, arguments=arguments):
                with self.assertRaises(OpenPineError) as caught:
                    self.compile(version, header, arguments)
                self.assertEqual(caught.exception.diagnostic.code, code)

    def test_positional_headers_preserve_mode_and_reject_unsupported_options(self):
        for version in (5, 6):
            margin, currency = ('0.0', 'currency.NONE') if version == 5 else ('100.0', 'syminfo.currency')
            headers = (
                ('indicator("I"', ',"Short",true,format.price,2,scale.right,0,timeframe.period,true,false,50,50,50,0,50'),
                ('strategy("S"', ',"",true,format.price,2,scale.right,0,false,false,0,0,strategy.fixed,1,'
                 f'100000,{currency},0,strategy.commission.percent,0,false,"FIFO",{margin},{margin},false,'
                 '50,50,50,0,2.0,false,false,50'),
            )
            for header, prefix in headers:
                for flag in ('false', 'true'):
                    with self.subTest(version=version, header=header, flag=flag):
                        p = self.compile(version, header, prefix + ',' + flag)
                        restored = import_program(export_program(p))
                        self.session.validate_program(restored)
                        self.assertIs(restored.identity.dynamic_requests_enabled, flag == 'true')
                        self.assertEqual(p.statements, restored.statements)
                        mixed = self.compile(version, header, prefix + ',dynamic_requests=' + flag)
                        def without_spans(value):
                            if isinstance(value, dict):
                                return {k: without_spans(v) for k, v in value.items() if k not in ('span', 'call_site_id')}
                            if isinstance(value, tuple):
                                return tuple(without_spans(v) for v in value)
                            return value
                        self.assertEqual(without_spans(asdict(p.statements[0])),
                                         without_spans(asdict(mixed.statements[0])))
                with self.assertRaises(OpenPineError) as caught:
                    self.compile(version, header, prefix + ',true,dynamic_requests=false')
                self.assertEqual(caught.exception.diagnostic.code, 'DUPLICATE_ARGUMENT')
            with self.assertRaises(OpenPineError) as caught:
                self.compile(version, headers[0][0], headers[0][1].replace('timeframe.period', '"240"') + ',true')
            self.assertEqual(caught.exception.diagnostic.code, 'DECLARATION_OPTION')

    def test_disabled_mode_rejects_series_in_target_slots(self):
        for version, option in ((5, ''), (5, ',dynamic_requests=false'), (6, ',dynamic_requests=false')):
            for name in ('request.security', 'request.security_lower_tf'):
                for args in ('symbol=sym,timeframe="1",expression=close', '"TEST:A",tf,close'):
                    with self.subTest(version=version, name=name, args=args):
                        source = (f'//@version={version}\nindicator("Mode"{option})\n'
                                  'sym=close > open ? "TEST:A" : "TEST:B"\n'
                                  'tf=close > open ? "1" : "5"\n'
                                  f'value={name}({args})\nplot(close)\n')
                        with self.assertRaises(OpenPineError) as caught:
                            self.session.compile(source, 'disabled-mode.pine')
                        self.assertEqual(caught.exception.diagnostic.code, 'ARGUMENT_QUALIFIER')
                        self.assertIn('dynamic_requests=true', caught.exception.diagnostic.message)

    def test_plan_only_resolves_raw_source_and_preserves_deferred_errors(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'indicator.pine'
            command = [sys.executable, '-m', 'apps.openpine', 'job', '--plan-only', '--source', str(source),
                       '--bars', str(root/'examples/pipeline/bars.json'), '--state-dir', str(Path(folder)/'state')]
            for header, mode in [('indicator("I",dynamic_requests=false)', False), ('unknown()', None)]:
                source.write_text(f'//@version=6\n{header}\nplot(close)\n', encoding='utf-8')
                preview = json.loads(subprocess.check_output(command, cwd=root))
                self.assertIn('plan', preview)
                self.assertIs(preview.get('dynamic_requests'), mode)

    def test_three_market_rotation_keeps_history_on_unselected_bars(self):
        from packages.contracts import HistoricalBar, HistoricalData
        from packages.contracts.contexts import ContextBinding, MarketContext
        from packages.execution.driver import MemorySource

        def data(name, closes):
            return HistoricalData(name, 'TEST:' + name, '1',
                                  tuple(HistoricalBar(i*60_000, float(c)) for i,c in enumerate(closes)))

        chart = data('chart', [1,2,3,1,3,2])
        sources = []
        for market, closes in [('A',[10,12,16,14,18,20]), ('B',[30,37,35,41,36,48]), ('C',[80,70,90,66,86,88])]:
            src = MemorySource(data(market, closes))
            src.bind_identity()
            context = MarketContext(market, 'TEST:' + market, '1', 'fixture')
            sources.append((ContextBinding(context,src.identity.sha256),src))
        path = Path(__file__).resolve().parents[1]/'examples/dynamic-requests/rotation.pine'
        source = path.read_text()
        for version in (5,6):
            with self.subTest(version=version):
                text = source if version == 6 else source.replace('version=6','version=5').replace(
                    '"Three-market rotation")','"Three-market rotation", dynamic_requests=true)')
                program = self.session.compile(text, path.name)
                result = self.session.execute(import_program(export_program(program)), data=chart,
                    primary_context=MarketContext('chart','TEST:chart','1','fixture'),context_sources=sources)
                self.assertEqual(result.outputs[0].values,(10.,37.,90.,14.,86.,48.))
                self.assertEqual(result.outputs[1].values,(None,30.,70.,16.,66.,36.))
                self.assertEqual(result.outputs[2].values,(10.,33.5,82.5,13.75,80.125,42.40625))

    def test_interval_rotation_keeps_independent_previous_closes(self):
        from packages.contracts import HistoricalBar, HistoricalData
        from packages.contracts.contexts import ContextBinding, MarketContext
        from packages.execution.driver import MemorySource
        from packages.contracts.prepared_requests import graph

        def market(key, symbol, interval, prices):
            bars = tuple(HistoricalBar(i*60_000*int(interval), float(p)) for i,p in enumerate(prices))
            return HistoricalData(key, symbol, interval, bars)

        chart = market('chart','TEST:chart','1',[1,2,1,2,1,2])
        primary = MarketContext('chart','TEST:chart','1','fixture')
        contexts = []
        for interval, prices in [('1',[10,11,12,13,14,15]),('2',[100,120,140])]:
            key = 'market' + interval
            symbol = 'TEST:A' if interval == '1' else 'TEST:B'
            src = MemorySource(market(key,symbol,interval,prices))
            src.bind_identity()
            contexts.append((ContextBinding(MarketContext(key,symbol,interval,'fixture'),src.identity.sha256),src))
        program = self.session.compile('//@version=6\nindicator("Intervals")\n'
            'first=close==1\nsym=first ? "TEST:A" : "TEST:B"\ntf=first ? "1" : "2"\n'
            '[value,prior]=request.security(sym,tf,[close,close[1]])\n'
            'plot(value)\nplot(prior)\n', 'intervals.pine')
        program = import_program(export_program(program))
        routes = graph(program, primary=primary)['requests']
        self.assertEqual([(r['symbol'],r['timeframe']) for r in routes], [('TEST:A','1'),('TEST:B','2')])
        self.assertEqual(len({r['call_site_id'] for r in routes}), 2)
        result = self.session.execute(program, data=chart, primary_context=primary, context_sources=contexts)
        self.assertEqual(result.outputs[0].values, (10.,100.,12.,120.,14.,140.))
        self.assertEqual(result.outputs[1].values, (None,None,11.,100.,13.,120.))

    def test_shared_selector_only_requires_reachable_pairs(self):
        from packages.contracts.prepared_requests import graph

        base = ('//@version=6\nindicator("Pairs")\nfirst=close > open\nsecond=close > 9\n'
                'symbol=first ? "TEST:A" : "TEST:B"\n'
                'tf=GUARD ? "1" : "2"\nplot(request.security(symbol,tf,close))\n')
        all_pairs = {('TEST:A', '1'), ('TEST:B', '2'), ('TEST:A', '2'), ('TEST:B', '1')}
        for guard, expected in [('first', {('TEST:A', '1'), ('TEST:B', '2')}), ('second', all_pairs)]:
            with self.subTest(guard=guard):
                program = self.session.compile(base.replace('GUARD', guard), 'pairs.pine')
                routes = graph(import_program(export_program(program)))['requests']
                self.assertEqual({(r['symbol'], r['timeframe']) for r in routes}, expected)

    def test_worker_resumes_rotation_before_revisiting_first_market(self):
        self.check_worker_resume('rotation.pine', [[10, 37, 90, 14, 86, 48],
                               [None, 30, 70, 16, 66, 36], [10, 33.5, 82.5, 13.75, 80.125, 42.40625]])

    def test_worker_restores_inline_selector_history_and_kernel_on_chart(self):
        self.check_worker_resume('history-selector.pine', [[30, 12, 35, 41, 18, 48],
                                                           [None, 10, 37, 35, 14, 36]], pause_after=1)

    def test_worker_restores_conditional_requests_in_function_and_if(self):
        self.check_worker_resume('conditional-function.pine', [[None, -1, -1, 16, -1, -1],
                                                               [-1, 37, -1, -1, -1, 48]])

    def test_worker_restores_loop_scanner(self):
        self.check_worker_resume('loop-scanner.pine', [[120, 119, 141, 121, 140, 156]])

    def test_worker_restores_dynamic_lower_windows(self):
        self.check_worker_resume('lower-rotation.pine', [[22, 76, 38], [2, 2, 2]], pause_after=2,
                                 bars='primary2.json', contexts='lower-contexts.json')

    def test_worker_restores_pending_order_from_requested_market_signal(self):
        result = self.check_worker_resume('rotation-strategy.pine', [[10, 37, 90, 14, 86, 48],
                                                                      [0, 0, 0, 1, 0, 1]])
        events = result['simulation']['events']
        self.assertEqual([(e['bar_index'], e['value']['price']) for e in events if e['kind'] == 'fill'],
                         [(3, '1'), (4, '3'), (5, '2')])
        self.assertEqual([e['value']['net_profit'] for e in events if e['kind'] == 'trade'], ['2'])
        self.assertEqual(events[-1]['value']['equity'], '1002')

    def test_local_request_admission_follows_declaration_mode(self):
        example = Path(__file__).resolve().parents[1] / 'examples/dynamic-requests'
        for name in ('conditional-function.pine', 'loop-scanner.pine'):
            text = (example / name).read_text()
            for version, flag in ((5, 'true'), (5, 'false'), (6, 'true'), (6, 'false')):
                with self.subTest(name=name, version=version, flag=flag):
                    source = text.replace('version=6', f'version={version}').replace('")\n', f'",dynamic_requests={flag})\n', 1)
                    if flag == 'true':
                        program = self.session.compile(source, name)
                        self.session.validate_program(import_program(export_program(program)))
                    else:
                        with self.assertRaises(OpenPineError) as caught:
                            self.session.compile(source, name)
                        self.assertIn(caught.exception.diagnostic.code, ('REQUEST_SCOPE', 'ARGUMENT_QUALIFIER'))

    def test_skipped_literal_request_in_function_cannot_first_run_in_realtime(self):
        from packages.contracts.contexts import MarketContext
        from packages.contracts.realtime import MarketEvent
        from packages.execution.realtime import Capture, ReplayEngine

        source = ('//@version=6\nindicator("Skipped call")\nreadA() => request.security("TEST:A","1",close)\n'
                  'plot(close == 2 ? readA() : -1.0)\n')
        program = self.session.compile(source)
        contexts = tuple(MarketContext(k, s, '1', 'recorded.1') for k, s in [('chart','TEST:CHART'),('a','TEST:A')])
        replay = ReplayEngine(self.session, program, contexts, 'chart', Capture(),
                              environments={'chart': {'instrument': None, 'history_last_ms': 0}})
        replay.apply(MarketEvent(0, 'a', 'close', 0, 60000, 0, 10., 10., 10., 10., 1.))
        replay.apply(MarketEvent(1, 'chart', 'close', 0, 60000, 0, 1., 1., 1., 1., 1.))
        self.assertEqual(replay.checkpoint()['historical_dynamic_sites'], [])
        with self.assertRaises(OpenPineError) as caught:
            replay.apply(MarketEvent(2, 'chart', 'open', 60000, 60000, 0, 2., 2., 2., 2., 0.))
        self.assertEqual(caught.exception.diagnostic.code, 'DYNAMIC_REQUEST_FIRST_REALTIME')

    def check_worker_resume(self, filename, expected, pause_after=3, *, bars='primary.json', contexts='contexts.json'):
        example = Path(__file__).resolve().parents[1] / 'examples/dynamic-requests'
        source = example / filename
        program = self.session.compile(source.read_text(), source.name)
        with TemporaryDirectory() as folder:
            artifact = Path(folder) / 'rotation.ir.json'
            artifact.write_text(json.dumps(export_program(program)), encoding='utf-8')
            command = [sys.executable, '-m', 'apps.openpine', 'job', '--state-dir', folder]
            inputs = ['--artifact', str(artifact), '--bars', str(example / bars), '--contexts', str(example / contexts)]

            def job(*options):
                return json.loads(subprocess.check_output([*command, *options], cwd=example.parents[1]))

            continuous = job(*inputs)
            paused = job(*inputs, '--pause-after', str(pause_after))['job']
            self.assertEqual(paused['status'], 'paused')
            resumed = job('--resume', paused['job_id'])
            self.assertEqual(resumed['job']['status'], 'succeeded')
            self.assertEqual(resumed['response'], continuous['response'])
            self.assertEqual(resumed['job']['result_sha256'], continuous['job']['result_sha256'])
            self.assertEqual([r['values'] for r in resumed['response']['result']['outputs']], expected)
            return resumed['response']['result']
