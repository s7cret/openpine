import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.openpine.bootstrap import local_manager
from packages.contracts.wire import read_json
from packages.marketdata.providers import input_snapshot
from test_environment_pipeline import wait


class StrategyBatchTests(unittest.TestCase):
    def test_shared_snapshot_cancel_and_compare_to_standalone(self):
        folder = Path(__file__).resolve().parents[1]/'examples/account-ledger'
        source = (folder/'partial.pine').read_text()
        with TemporaryDirectory() as state, local_manager(state) as manager:
            snapshot = manager.market.save(input_snapshot(read_json(folder/'bars.json')))
            requests = []
            for label,direction in [('long','long'), ('short','short')]:
                ref = manager.freeze_input(dict(source=source.replace('strategy.long','strategy.'+direction),
                                                source_name=label+'.pine'))
                requests.append(dict(schema_version='openpine.job-request.2',input_ref=ref,timeout_seconds=60))
            solo = []
            for item in requests:
                key = manager.submit({**item,'snapshot_id':snapshot})['job_id']
                wait(manager,key)
                solo.append(manager.result(key)['result'])
            self.assertEqual(solo[0]['outputs'][0]['values'],[0,4,2,0])
            self.assertEqual(solo[1]['outputs'][0]['values'],[0,-4,-2,0])
            before = len(manager.repository.list())
            with self.assertRaises(ValueError):
                manager.submit_batch(snapshot,[requests[0],{'schema_version':'bad'}])
            self.assertEqual(len(manager.repository.list()),before)
            with manager.lock:
                group = manager.submit_batch(snapshot,[requests[0],requests[0],requests[1]])
                left,cancelled,right = group['job_ids']
                manager.cancel(cancelled)
            self.assertEqual(len({left,cancelled,right}),3)
            self.assertEqual(manager.get(cancelled)['status'],'cancelled')
            for key,baseline in zip((left,right),solo,strict=True):
                wait(manager,key)
                self.assertEqual(manager.request(key)['snapshot_id'],snapshot)
                result = manager.result(key)['result']
                self.assertEqual(result['outputs'],baseline['outputs'])
                self.assertEqual(result['simulation'],baseline['simulation'])
