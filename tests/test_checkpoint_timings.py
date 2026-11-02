"""Diagnostic timing must preserve checkpoint bytes, failures and limits."""
import tempfile,unittest
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from packages.storage.objects import ObjectStore
from packages.storage.checkpoints import CheckpointStore
from packages.contracts.checkpoint_stream import RecordSequence

@dataclass(frozen=True)
class Identity:
    count:int=1

class CheckpointTimingTests(unittest.TestCase):
    def test_installed_receipt_retains_sampled_phase_evidence(self):
        import json
        from tools.installed_suites import checkpoint_diagnostics
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint-timings.json'
            path.write_text(json.dumps({'schema_version':'openpine.checkpoint-timings.1','latest':[
                {'completed':1,'total_seconds':2.,'object_write_seconds':.5},
                {'completed':2,'total_seconds':3.,'object_write_seconds':1.}]}))
            report=checkpoint_diagnostics(directory)
            self.assertEqual((report['files'],report['samples'],report['unreadable_files']),(1,2,0))
            self.assertEqual(report['maximum_seconds'],{'total_seconds':3.,'object_write_seconds':1.})
            self.assertEqual(report['sampled_total_seconds'],{'total_seconds':5.,'object_write_seconds':1.5})

    def test_same_durable_root_and_nonnegative_phase_measurements(self):
        for streaming in (False,True):
            with self.subTest(streaming=streaming),tempfile.TemporaryDirectory() as directory:
                objects=ObjectStore(Path(directory));store=CheckpointStore(objects)
                plan=SimpleNamespace(magnifier=None,memory_mb=256,snapshot_id='0'*64,payload=lambda:{})
                state={'values':[1,2,3]}
                if streaming:state={'values':RecordSequence(3,lambda i:i)}
                evaluation=SimpleNamespace(count=1,last_time=0,checkpoint=lambda **kw:state)
                sink=SimpleNamespace(checkpoint=lambda count:{'count':count})
                args=dict(plan=plan,source=SimpleNamespace(identity=Identity()),artifact_ref=objects.put({'ir':'fixture'}),program_sha256='2'*64)
                baseline=store.save(evaluation,sink,**args);phases={}
                measured=store.save(evaluation,sink,timings=phases,**args)
                self.assertEqual(measured,baseline)
                self.assertEqual(set(phases),{'state_capture_seconds','output_capture_seconds','object_encode_seconds','object_write_seconds','traversal_validation_seconds','manifest_seconds','total_seconds'})
                self.assertTrue(all(type(v) is float and v>=0 for v in phases.values()))
                self.assertGreaterEqual(phases['total_seconds'],phases['object_write_seconds'])
                self.assertEqual(store.encoded_bytes(measured),store.encoded_bytes(baseline))

    def test_failed_publication_records_total_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(Path(directory));store=CheckpointStore(objects);phases={}
            plan=SimpleNamespace(magnifier=None,memory_mb=256)
            evaluation=SimpleNamespace(checkpoint=lambda **kw:{'value':1})
            with patch.object(objects,'publish',side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError,'disk full'):
                    store.save(evaluation,None,plan=plan,source=None,artifact_ref=None,program_sha256=None,timings=phases)
            self.assertGreaterEqual(phases['total_seconds'],phases['object_write_seconds'])
            self.assertFalse(list(Path(directory).glob('.object-*')))

if __name__=='__main__':unittest.main()
