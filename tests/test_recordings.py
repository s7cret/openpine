"""Portable recorded prefixes replay offline independently of worker batching."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.recordings import Recordings,open_recording
from packages.contracts.wire import read_json
from test_live_market import prepare,message
from test_realtime import text


def recorded(manager,source=None):
    history,live,identifier=prepare(manager,source=source)
    live.tick(identifier);history.process(live.repository.session(identifier)['warmup_task'])
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        live.tick(identifier)
        if live.status(identifier)['replay']['confirmed_bars']==4:break
    else:raise AssertionError(live.status(identifier))
    state=live.status(identifier)
    live.hub.ingest(state['feed_id'],message(),received_at=400001)
    live.hub.ingest(state['feed_id'],message(closed=True,volume='101'),received_at=400002)
    while time.monotonic()<deadline:
        live.tick(identifier)
        if live.status(identifier)['replay']['confirmed_bars']==5:break
    else:raise AssertionError(live.status(identifier))
    live.stop(identifier)
    return history,live,identifier


class RecordingTests(unittest.TestCase):
    def test_portable_source_ir_warmup_raw_and_outputs_are_equal_for_different_batches(self):
        for version,strategy in ((1,False),(6,True)):
            with self.subTest(version=version,strategy=strategy),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);bundle=root/'bundle'
                with local_manager(root/'live') as manager:
                    history,live,identifier=recorded(manager,text(version,strategy))
                    with self.assertRaises(ValueError):Recordings(manager).export(identifier,manager.directory/'bad')
                    receipt=Recordings(manager).export(identifier,bundle)
                    live.close();history.close()
                manifest,objects,sha=open_recording(bundle)
                self.assertEqual(receipt['recording_id'],sha)
                self.assertEqual(manifest['published_events'],6)
                self.assertTrue(manifest['datasets'])
                self.assertEqual(manifest['raw_sources'][0]['accepted'],2)
                raw=objects.get(manifest['raw_sources'][0]['parts'][0]['object_id'])
                self.assertEqual(raw[0][1]['raw_payload'],message())
                for batch in (1,1000):
                    with local_manager(root/f'offline-{batch}') as manager,patch('urllib.request.urlopen',side_effect=AssertionError('Offline execution contacted the network.')):
                        result=Recordings(manager).replay(bundle,batch_size=batch)
                        self.assertEqual(result['status'],'passed')
                        self.assertEqual(result['result'],manifest['expected'])

    def test_corruption_and_unsafe_paths_are_rejected_before_creating_a_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle'
            with local_manager(root/'live') as manager:
                history,live,identifier=recorded(manager);Recordings(manager).export(identifier,bundle)
                live.close();history.close()
            value=read_json(bundle/'recording.json')['recording'];sha=value['event_parts'][0]['object_id']
            (bundle/'objects'/sha[:2]/sha).write_bytes(b'[]')
            with local_manager(root/'offline') as manager:
                with self.assertRaises(ValueError):Recordings(manager).replay(bundle)
                self.assertEqual(manager.db.execute('SELECT COUNT(*) FROM replays'),[(0,)])


if __name__=='__main__':unittest.main()
