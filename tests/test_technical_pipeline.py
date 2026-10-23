"""Independent TA corpus, compiled market arguments and joint strategy restore."""
import copy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError
from packages.contracts.inputs import settings_for
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import read_bars, data_identity
from tools.conformance import context_inputs, observation_matches, run_cases, TECHNICAL_CASES
from tools.specification import ROOT, SpecError, encode, load_snapshot, read_json, sha

EXAMPLE=ROOT/'examples/technical'


class TechnicalPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_eighteen_strategies_restore_joint_state_before_and_after_warmup(self):
        bars=read_bars(EXAMPLE/'bars.json')
        for version in range(1,7):
            for family in ('atr-cross','rsi-trend','channel-filter'):
                with self.subTest(version=version,family=family):
                    path=EXAMPLE/f'{family}-v{version}.pine'
                    program=self.session.compile(path.read_text(encoding='utf-8'),path.name)
                    contexts=context_inputs(EXAMPLE/'contexts.json') if family=='channel-filter' else {}
                    baseline=self.session.execute(program,data=bars,**contexts)
                    self.assertGreaterEqual(baseline.simulation['statistics']['trades'],1)
                    for cut in (3,97):
                        def driver():
                            runtime=self.session.validate_program(program);source=MemorySource(bars)
                            inputs=context_inputs(EXAMPLE/'contexts.json') if family=='channel-filter' else {}
                            settings=settings_for(program,None)
                            scheduler=self.session.context_scheduler(program,runtime,source,inputs.get('primary_context'),inputs.get('context_sources',()),settings=settings)
                            return HistoricalDriver(runtime.prepare(program,settings=settings),source,scheduler,broker_policy(program))
                        first=driver();saved=[]
                        first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                        resumed=driver();resumed.restore(saved[0]);resumed.run()
                        self.assertEqual(resumed.finish(data_identity(bars)),baseline)

    def test_independent_corpus_covers_each_operation_and_version(self):
        report=run_cases(session=self.session,cases_path=TECHNICAL_CASES)
        self.assertEqual(len(report['results']),72)
        for version in range(1,7):
            self.assertTrue({'series.rma','series.atr','series.rsi','series.tr','series.highest','series.lowest','context.request'}
                <=set(report['operations_by_version'][str(version)]))

    def test_compiler_supplied_market_arguments_cannot_be_forged(self):
        for version in range(1,7):
            prefix='' if version<5 else 'ta.'
            text=f'//@version={version}\n'+('study' if version<5 else 'indicator')+f'("implicit")\nplot({prefix}atr(2))'
            program=self.session.compile(text)
            plot=program.statements[-1];atr=plot.arguments[0].expression
            wrong=replace(atr,arguments=(replace(atr.arguments[0],expression=atr.arguments[1].expression),*atr.arguments[1:]))
            forged=replace(program,statements=(*program.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=wrong),*plot.arguments[1:]))))
            with self.assertRaises(OpenPineError) as caught:self.session.validate_program(forged)
            self.assertEqual(caught.exception.diagnostic.code,'IR_TECHNICAL_INPUT')
            history=atr.arguments[2].expression
            wrong=replace(atr,arguments=(*atr.arguments[:2],replace(atr.arguments[2],expression=replace(history,offset=replace(history.offset,value=0))),*atr.arguments[3:]))
            forged=replace(program,statements=(*program.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=wrong),*plot.arguments[1:]))))
            with self.assertRaises(OpenPineError):self.session.validate_program(forged)

    def test_numeric_oracle_tolerance_does_not_hide_structure_or_material_errors(self):
        expected={'outputs':[{'type':'float','values':[None,60.]}],'observation_count':2}
        near=copy.deepcopy(expected);near['outputs'][0]['values'][1]=60.00000000000001
        tolerance={'absolute':1e-12,'relative':1e-12}
        self.assertTrue(observation_matches(near,expected,tolerance));self.assertFalse(observation_matches(near,expected))
        for value in (None,True,float('nan'),60.000001):
            malformed=copy.deepcopy(expected);malformed['outputs'][0]['values'][1]=value
            self.assertFalse(observation_matches(malformed,expected,tolerance))
        with self.assertRaises(ValueError):observation_matches(near,expected,{'absolute':1e-6,'relative':0})

    def test_technical_evidence_rejects_foreign_versions_even_when_repinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec')
            lock=read_json(root/'spec/lock.json');bundle=root/lock['technical_release']['path']
            data=read_json(bundle/'technical.json')
            data['versions']['6']['apis'][0]['evidence']=data['versions']['5']['apis'][0]['evidence']
            (bundle/'technical.json').write_bytes(encode(data));manifest=read_json(bundle/'manifest.json')
            manifest['files']['technical.json']=sha((bundle/'technical.json').read_bytes());(bundle/'manifest.json').write_bytes(encode(manifest))
            lock['technical_release']['manifest_sha256']=sha((bundle/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
            with self.assertRaises(SpecError) as caught:load_snapshot(root)
            self.assertEqual(caught.exception.code,'SPEC_PROVENANCE')


if __name__=='__main__':unittest.main()
