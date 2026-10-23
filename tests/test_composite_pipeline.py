"""Real examples, artifact admission and joint strategy/context recovery."""
from pathlib import Path
import copy
import shutil
import tempfile
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.marketdata import read_bars,data_identity
from tools.conformance import run_cases,COMPOSITE_CASES,context_inputs
from tools.composite_spec import prepare,RELEASE
from tools.specification import ROOT,load_snapshot,SpecError,read_json,encode,sha

EXAMPLE=ROOT/'examples/composite'


class CompositePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_twelve_strategies_and_joint_context_broker_checkpoints(self):
        data=read_bars(EXAMPLE/'bars.json')
        for version in range(1,7):
            for family in ('macd-cross','bb-reversion'):
                with self.subTest(version=version,family=family):
                    path=EXAMPLE/f'{family}-v{version}.pine';program=self.session.compile(path.read_text(encoding='utf-8'),path.name)
                    expected=self.session.execute(program,data=data,**context_inputs(EXAMPLE/'contexts.json'))
                    self.assertGreaterEqual(expected.simulation['statistics']['trades'],1)
                    self.assertTrue(all(r.historical_future==(version<3) for r in expected.requests))
                    def fresh():
                        runtime=self.session.validate_program(program);primary=MemorySource(data);args=context_inputs(EXAMPLE/'contexts.json');settings=settings_for(program,None)
                        contexts=self.session.context_scheduler(program,runtime,primary,args['primary_context'],args['context_sources'],settings=settings)
                        return HistoricalDriver(runtime.prepare(program,settings=settings),primary,contexts,broker_policy(program))
                    for cut in (3,97):
                        first=fresh();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                        resumed=fresh();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(data)),expected)

    def test_independent_corpus_has_all_versions_and_composite_operation_families(self):
        report=run_cases(self.session,cases_path=COMPOSITE_CASES)
        self.assertEqual(len(report['results']),72)
        for version in range(1,7):
            operations={op.removesuffix('.population') for op in report['operations_by_version'][str(version)]}
            self.assertTrue({'tuple.result','tuple.unpack','series.macd','series.bb','series.variance','series.stdev','context.request'}<=operations)

    def test_release_is_reproducible_and_legacy_bb_is_disclosed(self):
        for name,raw in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        snapshot=load_snapshot()
        for version in range(1,7):
            row=snapshot.composite['versions'][str(version)];bb=next(api for api in row['apis'] if api['name'].endswith('bb'))
            self.assertEqual(bool(bb['evidence']),version>=4)
            self.assertEqual(bb['basis'],'explicit_reconstruction' if version<4 else 'artifact_defined_with_explicit_reconstruction')
            self.assertIn('external equivalence is unverified',' '.join(snapshot.semantic_identity(version)[2]))
        meta=read_json(EXAMPLE/'fixtures.json')
        for name,digest in meta['files'].items():self.assertEqual(sha((EXAMPLE/name).read_bytes()),digest)

    def test_fully_repinned_foreign_evidence_or_changed_policy_is_rejected(self):
        for change in ('evidence','policy'):
            with tempfile.TemporaryDirectory(prefix='openpine-composite-spec-') as temporary:
                root=Path(temporary);shutil.copytree(ROOT/'spec',root/'spec')
                lock=read_json(root/'spec/lock.json');directory=root/lock['composite_release']['path'];data=read_json(directory/'composite.json')
                if change=='policy':data['versions']['6']['policy']['nested']=True
                else:data['versions']['3']['apis'][0]['evidence']=copy.deepcopy(data['versions']['6']['apis'][0]['evidence'])
                schema=read_json(root/'spec/schema/import.schema.json')
                schema['$defs']['composite_policy']['properties']['versions']={'const':data['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (directory/'composite.json').write_bytes(encode(data));manifest=read_json(directory/'manifest.json')
                manifest['files']['composite.json']=sha((directory/'composite.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest))
                lock['composite_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError):load_snapshot(root)


if __name__=='__main__':unittest.main()
