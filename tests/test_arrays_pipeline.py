"""Primitive arrays through real examples, context/broker resume and provenance."""

import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import read_bars, data_identity
from tools.conformance import run_cases, ARRAY_CASES, context_inputs
from tools.array_spec import prepare, RELEASE
from tools.array_fixtures import prepare as fixtures
from tools.specification import ROOT, SpecError, read_json, encode, sha, load_snapshot


class ArrayPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_six_strategies_have_independent_filters_and_joint_durable_resume(self):
        example=ROOT/'examples/arrays';data=read_bars(example/'bars.json');closes=[bar.close for bar in data.bars]
        paths=sorted(example.glob('*.pine'));self.assertEqual(len(paths),6)
        for path in paths:
            with self.subTest(example=path.name):
                program=self.session.compile(path.read_text(encoding='utf-8'),path.name)
                expected=self.session.execute(program,data=data,**context_inputs(example/'contexts.json'))
                references=[]
                for i in range(len(closes)):
                    window=closes[max(0,i-5):i+1]
                    references.append(sum(value*(j+1) for j,value in enumerate(window))/sum(range(1,len(window)+1))
                        if path.name.startswith('weighted') else sum(window)/len(window))
                for actual,reference in zip(expected.outputs[0].values,references,strict=True):self.assertAlmostEqual(actual,reference,places=11)
                self.assertGreater(expected.simulation['statistics']['trades'],0)
                def fresh():
                    runtime=self.session.validate_program(program);primary=MemorySource(data);args=context_inputs(example/'contexts.json')
                    settings=settings_for(program,None)
                    contexts=self.session.context_scheduler(program,runtime,primary,args['primary_context'],args['context_sources'],settings=settings)
                    return HistoricalDriver(runtime.prepare(program,settings=settings),primary,contexts,broker_policy(program))
                for cut in (17,97):
                    first=fresh();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    second=fresh();second.restore(saved[0]);second.run()
                    self.assertEqual(second.finish(data_identity(data)),expected)

    def test_independent_corpus_covers_operations_and_explicit_version_rejections(self):
        report=run_cases(self.session,cases_path=ARRAY_CASES)
        self.assertEqual((len(report['results']),report['executed_programs'],report['expected_rejections']),(91,40,51))
        for version in range(1,7):
            features=set(report['operations_by_version'][str(version)])
            self.assertEqual('array.new' in features,version>=4)
            self.assertEqual('loop.for_in' in features,version>=5)
            if version>=4:self.assertTrue({'array.'+op for op in ('copy','push','shift','set','min','max','avg','sum','clear','is_na')}<=features)

    def test_immutable_release_fixtures_and_profiles_are_reproducible(self):
        for name,raw in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for name,raw in fixtures().items():self.assertEqual((ROOT/name).read_bytes(),raw)
        snapshot=load_snapshot()
        for version in range(1,7):self.assertEqual(snapshot.profile(version)['array_profile_ids'],[f'openpine.arrays.v{version}.1'])

    def test_fully_repinned_foreign_evidence_and_policy_are_rejected(self):
        for change in ('evidence','policy'):
            with tempfile.TemporaryDirectory(prefix='openpine-array-spec-') as temporary:
                root=Path(temporary);shutil.copytree(ROOT/'spec',root/'spec')
                lock=read_json(root/'spec/lock.json');directory=root/lock['array_release']['path'];data=read_json(directory/'arrays.json')
                if change=='policy':data['versions']['4']['policy']['negative_indices']=True
                else:data['versions']['4']['evidence']=copy.deepcopy(data['versions']['6']['evidence'])
                schema=read_json(root/'spec/schema/import.schema.json')
                schema['$defs']['array_policy']['properties']['versions']={'const':data['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (directory/'arrays.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
                manifest['files']['arrays.json']=sha((directory/'arrays.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
                lock['array_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError):load_snapshot(root)


if __name__=='__main__':unittest.main()
