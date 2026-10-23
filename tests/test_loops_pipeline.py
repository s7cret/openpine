"""Versioned examples, joint recovery and independently pinned loop provenance."""

import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical, loads
from packages.execution.driver import HistoricalDriver, MemorySource
from packages.execution.session import Session
from packages.marketdata import read_bars, data_identity
from tools.conformance import run_cases, LOOP_CASES, context_inputs
from tools.loop_spec import prepare, RELEASE
from tools.loop_fixtures import prepare as fixtures
from tools.specification import ROOT, SpecError, load_snapshot, read_json, encode, sha


class LoopPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_seven_strategies_and_joint_context_broker_recovery(self):
        example = ROOT/'examples/loops'
        data = read_bars(example/'bars.json')
        for path in sorted(example.glob('*.pine')):
            with self.subTest(example=path.name):
                program = self.session.compile(path.read_text(encoding='utf-8'), path.name)
                expected = self.session.execute(program, data=data, **context_inputs(example/'contexts.json'))
                closes = [b.close for b in data.bars]
                mean = [sum(closes[max(0,i-5):i+1])/min(i+1,6) for i in range(len(closes))]
                for actual, reference in zip(expected.outputs[0].values, mean, strict=True):
                    self.assertAlmostEqual(actual, reference, places=11)
                self.assertGreater(expected.simulation['statistics']['trades'], 0)
                def fresh():
                    runtime = self.session.validate_program(program); primary = MemorySource(data)
                    args = context_inputs(example/'contexts.json'); settings = settings_for(program, None)
                    contexts = self.session.context_scheduler(program, runtime, primary,
                        args['primary_context'], args['context_sources'], settings=settings)
                    return HistoricalDriver(runtime.prepare(program, settings=settings), primary, contexts, broker_policy(program))
                for cut in (17,97):
                    first = fresh(); saved = []
                    first.run(pause_after=cut, checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    resumed = fresh(); resumed.restore(saved[0]); resumed.run()
                    self.assertEqual(resumed.finish(data_identity(data)), expected)

    def test_independent_corpus_contains_explicit_version_admission_and_rejection(self):
        report = run_cases(self.session, cases_path=LOOP_CASES)
        self.assertEqual((len(report['results']), report['executed_programs'], report['expected_rejections']), (92,55,37))
        for version in range(1,7):
            features = set(report['operations_by_version'][str(version)])
            self.assertEqual('loop.for' in features, version>=2)
            self.assertEqual('loop.while' in features, version>=5)

    def test_immutable_release_and_fixtures_are_reproducible(self):
        for name, raw in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(), raw)
        for name, raw in fixtures().items():self.assertEqual((ROOT/name).read_bytes(), raw)
        snapshot = load_snapshot()
        for v in range(1,7):
            self.assertEqual(snapshot.profile(v)['loop_profile_ids'], [f'openpine.loops.v{v}.1'])
            self.assertEqual(snapshot.loops['versions'][str(v)]['policy']['for'], v>=2)
            self.assertEqual(snapshot.loops['versions'][str(v)]['policy']['while'], v>=5)
            self.assertIn('historical reconstructions', ' '.join(snapshot.semantic_identity(v)[2]))

    def test_fully_repinned_foreign_evidence_or_policy_is_rejected(self):
        for change in ('evidence','policy'):
            with tempfile.TemporaryDirectory(prefix='openpine-loop-spec-') as temporary:
                root = Path(temporary); shutil.copytree(ROOT/'spec', root/'spec')
                lock = read_json(root/'spec/lock.json'); directory = root/lock['loop_release']['path']
                data = read_json(directory/'loops.json')
                if change=='policy':data['versions']['2']['policy']['for_final']='each_iteration'
                else:data['versions']['2']['evidence']=copy.deepcopy(data['versions']['6']['evidence'])
                schema = read_json(root/'spec/schema/import.schema.json')
                schema['$defs']['loop_policy']['properties']['versions']={'const':data['versions']}
                (root/'spec/schema/import.schema.json').write_bytes(encode(schema))
                lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
                (directory/'loops.json').write_bytes(encode(data)); manifest=read_json(directory/'manifest.json')
                manifest['files']['loops.json']=sha((directory/'loops.json').read_bytes())
                (directory/'manifest.json').write_bytes(encode(manifest))
                lock['loop_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
                (root/'spec/lock.json').write_bytes(encode(lock))
                with self.assertRaises(SpecError):load_snapshot(root)


if __name__=='__main__':unittest.main()
