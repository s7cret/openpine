import unittest

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
