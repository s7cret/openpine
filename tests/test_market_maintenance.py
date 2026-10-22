"""Segment continuation, reviewed retention, shared roots and 64-bit cursors."""
import tempfile
import time
import unittest
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from apps.openpine.market_maintenance import MarketMaintenance
from apps.openpine.recordings import Recordings
from apps.openpine.replay import Replays
from packages.contracts.realtime import MarketEvent
from packages.storage.live import LiveRepository
from packages.storage.backup import backup,restore
from test_recordings import recorded
from test_realtime import text,tape,configuration
from test_marketdata import KEY
from dataclasses import asdict


class MarketMaintenanceTests(unittest.TestCase):
    def test_forced_partial_segments_continue_after_restore_without_resetting_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
                raw=LiveRepository(manager.db,manager.objects);feed=raw.feed({'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'ingestion_sha256':'a'*64})
                events=[e.payload() for e in tape()]
                for index in range(3):
                    service.append(identifier,events[index*4:(index+1)*4])
                    for n in range(5):raw.append(feed,{'kind':'diagnostic','n':index*5+n})
                    report=MarketMaintenance(manager).rotate()
                    self.assertEqual(report['raw_segments'],index+1);self.assertEqual(report['event_segments'],index+1)
                    self.assertEqual((report['raw_tail_records'],report['event_tail_records']),(0,0))
                backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                raw=LiveRepository(manager.db,manager.objects)
                self.assertEqual([r['n'] for _,r in raw.records(feed,0,100)],list(range(15)))
                service=Replays(manager);self.assertEqual([e.payload() for e in service.repository.events(identifier,0,12)],events)
                service.advance(identifier);self.assertEqual(service.status(identifier)['confirmed_bars'],4)
                service.append(identifier,[events[0]])  # Old duplicates remain verifiable across segments.
                self.assertEqual(service.status(identifier)['accepted_events'],12)

    def test_shared_active_roots_and_stale_preview_are_protected_and_export_survives_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with local_manager(root/'live') as manager:
                history,live,first=recorded(manager);state=live.repository.session(first);feed=state['feed_id']
                config=live.repository.session_config(first)
                second=live.create({k:config[k] for k in ('input_ref','instrument','timeframe','start_ms','broker_settings','timeout_seconds')},connect=False)['session_id']
                maintenance=MarketMaintenance(manager,live=live);cutoff=int(time.time()*1000)
                before=maintenance.preview(cutoff,grace_seconds=0)
                self.assertNotIn(feed,before['feeds']);self.assertFalse(before['apply_allowed'])
                with self.assertRaises(ValueError):maintenance.apply(cutoff,before['preview_id'],grace_seconds=0)
                self.assertEqual(len(live.repository.sessions()),2)
                live.stop(second);Recordings(manager).export(first,root/'bundle');maintenance.rotate()
                with self.assertRaises(ValueError):maintenance.apply(cutoff,before['preview_id'],grace_seconds=0)
                cutoff=int(time.time()*1000);plan=maintenance.preview(cutoff,grace_seconds=0)
                self.assertTrue(plan['apply_allowed']);self.assertIn(feed,plan['feeds']);self.assertGreater(plan['reclaimable_object_bytes'],0)
                old_config=live.repository.config(feed);result=maintenance.apply(cutoff,plan['preview_id'],grace_seconds=0)
                self.assertEqual(result['sessions_removed'],2);self.assertEqual(live.repository.sessions(),[])
                self.assertGreater(manager.db.execute('SELECT COUNT(*) FROM datasets')[0][0],0)
                self.assertNotEqual(live.repository.feed_id(live.repository.choose_config(old_config)),feed)
                live.close();history.close()
            with local_manager(root/'offline') as manager:self.assertEqual(Recordings(manager).replay(root/'bundle')['status'],'passed')

    def test_missing_archive_fails_closed_and_global_cursors_cross_the_old_limit(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            raw=LiveRepository(manager.db,manager.objects);feed=raw.feed({'instrument':asdict(KEY),'timeframe':'1','start_ms':0,'ingestion_sha256':'b'*64})
            raw.append(feed,{'kind':'diagnostic'});MarketMaintenance(manager).rotate()
            manager.db.execute('DELETE FROM live_parts WHERE feed_id=?',(feed,))
            with self.assertRaisesRegex(ValueError,'Missing archived'):list(raw.records(feed))
            state=raw.state(feed);raw.save({**state,'accepted':10000001})
            self.assertEqual(raw.append(feed,{'kind':'diagnostic'}),10000001)
            self.assertEqual(list(raw.records(feed,10000001,1))[0][0],10000001)
            event=MarketEvent(10000001,'primary','close',0,60000,0,10.,11.,9.,10.,1.)
            self.assertEqual(MarketEvent.from_payload(event.payload()).sequence,10000001)
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            service.append(identifier,[next(tape()).payload()])
            service.repository.archive(identifier,Path(directory)/'tape',force=True)
            manager.db.execute('DELETE FROM replay_parts WHERE replay_id=?',(identifier,))
            with self.assertRaisesRegex(ValueError,'Missing archived'):service.repository.archive(identifier,Path(directory)/'tape',force=True)


if __name__=='__main__':unittest.main()
