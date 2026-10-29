"""Diagnostic ownership survives maintenance, backup and injected resource failures."""
import unittest,time,uuid
from pathlib import Path
from unittest.mock import patch
from test_comparisons import ComparisonFixture,IdIndexContext
from packages.contracts.wire import digest

class ComparisonLifecycleTests(ComparisonFixture):
    def test_running_cancel_reopen_resume_preserves_exact_chains(self):
        import threading
        from packages.storage.comparison_readers import rows
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id'];entered=threading.Event();release=threading.Event()
        def blocked(*args):
            for index,row in enumerate(rows(*args)):
                if index==1:
                    entered.set()
                    if not release.wait(10):raise TimeoutError('Owned cancellation fixture timed out.')
                yield row
        with patch('apps.openpine.comparison_engine.rows',side_effect=blocked):
            self.service.launch(key)
            try:
                self.assertTrue(entered.wait(10));self.service.control(key,'cancel')
            finally:release.set();self.service.threads[key].join(timeout=15)
        cancelled=self.service.status(key);self.assertEqual(cancelled['state']['status'],'cancelled');self.assertEqual(cancelled['state']['checked']['rows'],1)
        self.close();self.open();self.service.launch(key,background=False)
        resumed=self.service.status(key);fresh=self.compare(a,a)
        self.assertEqual(resumed['state']['status'],'equal');self.assertEqual(resumed['state']['chains'],fresh['state']['chains'])
    def test_backup_restore_prune_and_gc_keep_comparison_pins(self):
        from packages.storage.backup import backup,restore
        from packages.storage.database import Database
        from packages.storage.objects import ObjectStore
        from packages.storage.lifecycle import reachable
        from apps.openpine.comparisons import Comparisons
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id'];self.service.control(key,'pause')
        plan=self.manager.services.lifecycle.prune(time.time(),expire_inputs=True,expire_coverage=True)
        self.assertNotIn(a,plan['jobs']);self.assertNotIn(self.manager.request(a)['snapshot_id'],plan['datasets'])
        saved=backup(self.manager.db,self.directory,Path(self.temp.name)/'backup')
        restored=restore(saved,Path(self.temp.name)/'restored')
        from apps.openpine.bootstrap import local_manager
        from packages.execution.host_memory import HostAdmission
        with local_manager(restored,autostart=False,host_admission=HostAdmission(probe=lambda:None)) as manager:
            service=Comparisons(manager,start=False);service.launch(key,background=False)
            self.assertEqual(service.status(key)['state']['status'],'equal')
            self.assertTrue(reachable(manager.db,manager.objects));self.assertEqual(service.status(key)['configuration'],value['configuration'])
        self.assertEqual(self.service.status(key)['state']['status'],'paused')
        self.manager.services.lifecycle.collect(grace_seconds=0,apply=True)
        self.assertEqual(self.manager.summary(a)['status'],'executed')
    def test_active_and_stale_trace_deletion_are_rejected(self):
        from apps.openpine.comparison_trace import create
        a=self.job(source='//@version=6\nindicator("A")\nplot(close+1)');b=self.job(source='//@version=6\nindicator("B")\nplot(close+2)')
        value=self.compare(a,b);traced=create(self.service,value['comparison_id']);trace=traced['sides'][0]['trace_id']
        from test_jobs import wait
        wait(self.manager,trace)
        with self.traces.lock:self.traces.active.add(trace)
        try:
            with self.assertRaisesRegex(ValueError,'active'):self.retention.preview('trace',trace)
        finally:self.traces.active.discard(trace)
        preview=self.retention.preview('trace',trace)
        from packages.storage.traces import get,put
        config=get(self.manager.db,'trace:config:'+trace);put(self.manager.db,'trace:config:'+trace,{**config,'created_at':config['created_at']+1})
        with self.assertRaisesRegex(ValueError,'stale'):self.retention.delete('trace',trace,preview['preview_id'])
    def test_disk_cap_and_item_cap_never_publish_equality(self):
        from packages.storage.comparison_ids import IdIndex
        index=IdIndex(self.directory/'limited.sqlite3',['owned'],max_bytes=1)
        try:
            with self.assertRaises(OverflowError):
                for i in range(1000):index.normalize(0,{'order_id':digest({'program':'owned','kind':'order','sequence':i})})
        finally:index.close()
        a=self.job();value=self.compare(a,a,run=False);key=value['comparison_id']
        with patch('apps.openpine.comparison_engine.MAX_ITEMS',2):self.service.launch(key,background=False)
        limited=self.service.status(key);self.assertEqual(limited['state']['status'],'limited');self.assertEqual(limited['state']['checked']['rows'],2)
        self.service.launch(key,background=False);self.assertEqual(self.service.status(key)['state']['status'],'equal')
    def test_archived_partial_prefix_is_verified_and_not_index_copied(self):
        from apps.openpine.replay import Replays
        from test_realtime import configuration,text,PRIMARY
        from packages.contracts.realtime import MarketEvent
        from packages.storage.comparison_inputs import prefix,clone
        service=Replays(self.manager);repo=service.repository;key=service.create(configuration(self.manager,text()))['replay_id']
        events=[MarketEvent(i,PRIMARY.context_id,'close',i*60000,(i+1)*60000,0,1.,1.,1.,1.,1.).payload() for i in range(10000)]
        service.append(key,events);ref=prefix(repo,key,6500);manifest=self.manager.objects.get(ref)
        self.assertEqual(manifest['events'],6500);self.assertTrue(all(p['rows']<=1024 for p in manifest['parts']))
        other=clone(repo,ref);self.assertEqual(repo.row(other)[3],6500)
        self.assertEqual([e.payload() for e in repo.events(other,6499,6500)],events[6499:6500])
        self.assertEqual(self.manager.db.execute('SELECT count(*) FROM replay_events WHERE replay_id=?',(other,)),[(0,)])
        path=self.manager.objects.path(manifest['parts'][-1]['object_id']);path.write_bytes(b'owned corruption')
        with self.assertRaises(ValueError):list(repo.events(other,6499,6500))

if __name__=='__main__':unittest.main()
