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
