"""No-write inspection, durable upgrade boundaries and atomic replacement jobs."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import apsw

from apps.openpine.bootstrap import local_manager
from apps.openpine.diagnostics import doctor, compatibility, diagnostic_package, current_identity, Compatibility
from apps.openpine.restarts import preview, apply
from packages.contracts.wire import canonical, digest, read_json
from packages.storage.database import Database, SCHEMA_VERSION
from packages.storage.inspection import snapshot, lock_state
from packages.storage.lease import Lease
from packages.storage.objects import ObjectStore
from packages.storage.backup import restore
from packages.storage.upgrades import upgrade, verify_backup
from test_jobs import request, wait


def fingerprints(root):
    result={}
    for p in root.rglob('*'):
        if not p.is_file():continue
        try:sha=hashlib.sha256(p.read_bytes()).hexdigest()
        except PermissionError:sha='locked'
        result[p.relative_to(root).as_posix()]=(sha,p.stat().st_mtime_ns,p.stat().st_size)
    return result


def old_schema(root):
    """Version 15 differs from 16 only by the empty lineage relation."""
    db=Database(root/'catalog.sqlite3')
    db.execute('DROP TABLE job_lineage');db.connection.pragma('user_version',15);db.close()


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_missing_store_is_not_created(self):
        target=self.root/'missing'
        self.assertEqual(doctor(target)['status'],'attention');self.assertFalse(target.exists())

    def test_live_wal_snapshot_and_lock_probe_change_no_source_bytes(self):
        root=self.root/'store';db=Database(root/'catalog.sqlite3');self.addCleanup(db.close)
        db.execute("INSERT INTO imports VALUES('in-wal',NULL,NULL)")
        with Lease(root/'.manager.lock'):
            before=fingerprints(root)
            self.assertEqual(lock_state(root/'.manager.lock'),'busy')
            with snapshot(root) as copied:
                self.assertEqual(copied.execute('SELECT path FROM imports'),[('in-wal',)])
                with self.assertRaises(apsw.ReadOnlyError):copied.execute("DELETE FROM imports")
            value=doctor(root)
            self.assertEqual(value['catalog']['stored'],SCHEMA_VERSION)
            self.assertEqual(value['ownership'],'busy')
            self.assertEqual(before,fingerprints(root))

    def test_old_and_future_schema_report_without_migration(self):
        old_schema(self.root)
        before=fingerprints(self.root)
        self.assertEqual(doctor(self.root)['catalog']['action'],'upgrade_required')
        self.assertEqual(compatibility(self.root)['status'],'schema_upgrade_required')
        self.assertEqual(before,fingerprints(self.root))
        db=apsw.Connection(str(self.root/'catalog.sqlite3'));db.pragma('user_version',999);db.close()
        before=fingerprints(self.root)
        self.assertEqual(doctor(self.root)['catalog']['action'],'newer_installation_required')
        self.assertEqual(before,fingerprints(self.root))

    def test_copy_limit_fails_before_copying_or_migrating(self):
        db=Database(self.root/'catalog.sqlite3');db.close();before=fingerprints(self.root)
        with self.assertRaisesRegex(ValueError,'INSPECTION_COPY_LIMIT'):
            with snapshot(self.root,max_bytes=1):pass
        self.assertEqual(before,fingerprints(self.root))

    def test_corrupt_catalog_is_reported_and_not_repaired(self):
        (self.root/'catalog.sqlite3').write_bytes(b'invalid sqlite')
        before=fingerprints(self.root)
        self.assertEqual(doctor(self.root)['issues'][0]['code'],'INSPECTION_FAILED')
        with self.assertRaises(apsw.Error):
            with snapshot(self.root):pass
        self.assertEqual(before,fingerprints(self.root))

    def test_changing_wal_is_retried_instead_of_returning_a_mixed_copy(self):
        db=Database(self.root/'catalog.sqlite3');self.addCleanup(db.close)
        from packages.storage import inspection
        original=inspection.shutil.copyfile;counter=[0]
        def changing(source,target):
            result=original(source,target)
            if Path(source).name.endswith('-wal'):
                counter[0]+=1;db.execute('INSERT INTO imports VALUES(?,NULL,NULL)',('change-'+str(counter[0]),))
            return result
        with patch.object(inspection.shutil,'copyfile',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'CATALOG_CHANGING'):
                with snapshot(self.root):pass
        self.assertEqual(counter[0],3)

    def test_corrupt_store_still_exports_a_redacted_diagnostic_package(self):
        (self.root/'catalog.sqlite3').write_bytes(b'corrupt')
        value=diagnostic_package(self.root)
        self.assertEqual(value['doctor']['status'],'attention')
        self.assertTrue(all(p['status']=='unavailable' for p in value['pages']))
        self.assertNotIn(str(self.root).encode(),canonical(value))


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.parent=Path(self.temp.name);self.root=self.parent/'store';self.root.mkdir()
        old_schema(self.root);self.backup=self.parent/'backup';self.identity=current_identity()

    def test_application_requires_explicit_upgrade(self):
        before=(self.root/'catalog.sqlite3').read_bytes()
        with self.assertRaisesRegex(ValueError,'UPGRADE_REQUIRED'):local_manager(self.root)
        self.assertEqual(before,(self.root/'catalog.sqlite3').read_bytes())

    def test_exact_backup_keeps_old_schema_and_unknown_immutable_objects(self):
        sha=ObjectStore(self.root/'objects').put({'unreferenced':'keep in pre-upgrade copy'})
        receipt=upgrade(self.root,self.backup,target_identity=self.identity)
        self.assertEqual(receipt['phase'],'complete')
        self.assertEqual(upgrade(self.root,self.backup,target_identity=self.identity),receipt)
        with snapshot(self.backup) as old:self.assertEqual(old.version,15)
        with snapshot(self.root) as new:self.assertEqual(new.version,SCHEMA_VERSION)
        self.assertIn('objects/'+sha[:2]+'/'+sha,verify_backup(self.backup)['files'])
        rolled=self.parent/'rollback';restore(self.backup,rolled,migrate=False)
        with snapshot(rolled) as old:self.assertEqual(old.version,15)
        self.assertEqual(ObjectStore(rolled/'objects').get(sha),{'unreferenced':'keep in pre-upgrade copy'})

    def test_every_durable_boundary_can_resume_and_blocks_regular_open(self):
        for phase in ('planned','backed_up','migrated','verified'):
            root=self.parent/phase;root.mkdir();old_schema(root);target=self.parent/(phase+'-backup')
            def crash(at):
                if at==phase:raise RuntimeError('crash '+phase)
            with self.assertRaisesRegex(RuntimeError,'crash'):
                upgrade(root,target,target_identity=self.identity,hook=crash)
            with self.assertRaisesRegex(ValueError,'UPGRADE_INCOMPLETE'):Database(root/'catalog.sqlite3')
            self.assertEqual(upgrade(root,target,target_identity=self.identity)['phase'],'complete')
            with snapshot(target) as old:self.assertEqual(old.version,15)

    def test_failed_migration_transaction_rolls_back_and_preserves_backup(self):
        def fail(version):raise RuntimeError('migration power loss')
        with self.assertRaisesRegex(RuntimeError,'power loss'):
            upgrade(self.root,self.backup,target_identity=self.identity,migration_hook=fail)
        with snapshot(self.root) as db:
            self.assertEqual(db.version,15)
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema WHERE name='job_lineage'"),[])
        verify_backup(self.backup)
        self.assertEqual(upgrade(self.root,self.backup,target_identity=self.identity)['phase'],'complete')

    def test_refuses_busy_owner_or_worker(self):
        with Lease(self.root/'.manager.lock'):
            with self.assertRaises(ValueError):upgrade(self.root,self.backup,target_identity=self.identity)
        path=self.root/'attempts'/('a'*32)/('b'*32);path.mkdir(parents=True)
        with Lease(path/'.active.lock'):
            with self.assertRaisesRegex(ValueError,'WORKER_ACTIVE'):upgrade(self.root,self.backup,target_identity=self.identity)
        self.assertFalse(self.backup.exists())
        replay=self.root/'replay-attempt-probe';replay.mkdir()
        with Lease(replay/'.active.lock'):
            with self.assertRaisesRegex(ValueError,'WORKER_ACTIVE'):upgrade(self.root,self.backup,target_identity=self.identity)

    def test_backup_failure_leaves_schema_untouched(self):
        with patch('packages.storage.upgrades.durable_copy',side_effect=OSError('disk full')):
            ObjectStore(self.root/'objects').put({'data':1})
            with self.assertRaises(OSError):upgrade(self.root,self.backup,target_identity=self.identity)
        with snapshot(self.root) as db:self.assertEqual(db.version,15)
        self.assertFalse(self.backup.exists())
        self.assertEqual(upgrade(self.root,self.backup,target_identity=self.identity)['phase'],'complete')

    def test_changed_target_or_tampered_backup_cannot_resume(self):
        def crash(phase):
            if phase=='backed_up':raise RuntimeError('stop')
        with self.assertRaises(RuntimeError):upgrade(self.root,self.backup,target_identity=self.identity,hook=crash)
        with self.assertRaisesRegex(ValueError,'TARGET_CHANGED'):
            upgrade(self.root,self.backup,target_identity={**self.identity,'execution_sha256':'0'*64})
        (self.backup/'catalog.sqlite3').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            upgrade(self.root,self.backup,target_identity=self.identity)

    def test_real_process_exit_at_every_stage_releases_lease_and_recovers(self):
        source=Path(__file__).resolve().parents[1]
        script="""import os,sys,json
sys.path.insert(0,sys.argv[1])
from packages.storage.upgrades import upgrade
phase=sys.argv[4]
def hook(at):
 if at==phase:os._exit(86)
def migration(version):
 if phase=='migration':os._exit(86)
upgrade(sys.argv[2],sys.argv[3],target_identity=json.loads(sys.argv[5]),hook=hook,migration_hook=migration)
"""
        for phase in ('planned','backed_up','migration','migrated','verified'):
            root=self.parent/('process-'+phase);root.mkdir();old_schema(root);target=self.parent/('copy-'+phase)
            result=subprocess.run([sys.executable,'-X','utf8','-c',script,str(source),str(root),str(target),phase,json.dumps(self.identity)],
                                  capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            self.assertEqual(result.returncode,86,result.stderr)
            self.assertEqual(lock_state(root/'.manager.lock'),'available')
            with self.assertRaisesRegex(ValueError,'UPGRADE_INCOMPLETE'):Database(root/'catalog.sqlite3')
            self.assertEqual(upgrade(root,target,target_identity=self.identity)['phase'],'complete')
            with snapshot(target) as db:self.assertEqual(db.version,15)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        with local_manager(self.root) as manager:
            self.job=manager.submit(request())['job_id'];self.assertEqual(wait(manager,self.job)['status'],'succeeded')

    def test_compatibility_distinguishes_build_from_computation_and_full_audit(self):
        with local_manager(self.root,autostart=False) as manager:
            identity=current_identity();identity['build']={'scheme':'openpine.build-identity.1','sha256':'0'*64}
            self.assertEqual(Compatibility(manager.db,manager.objects,identity=identity).job(self.job)['status'],'compatible')
            identity['execution_sha256']='0'*64
            row=Compatibility(manager.db,manager.objects,identity=identity).job(self.job)
            self.assertEqual(row['status'],'recompile_required');self.assertEqual(row['action'],'new_run')
            self.assertEqual([c['field'] for c in row['changes']],['execution_sha256'])
        self.assertEqual(doctor(self.root,full=True)['status'],'ok')

    def test_preview_has_no_attempts_and_apply_is_atomic_idempotent_and_auditable(self):
        with local_manager(self.root,autostart=False) as manager:
            original=manager.request(self.job);old_result=manager.get(self.job)['result_id']
            report=preview(manager,self.job);self.assertFalse(report['reuses_checkpoint'])
            before=manager.db.execute('SELECT count(*) FROM jobs')[0][0]
            receipt=apply(manager,self.job,preview_id=report['preview_id'],request_id='a'*32)
            self.assertEqual(receipt,apply(manager,self.job,preview_id=report['preview_id'],request_id='a'*32))
            self.assertEqual(manager.db.execute('SELECT count(*) FROM jobs')[0][0],before+1)
            replacement=manager.get(receipt['job_id']);self.assertEqual(replacement['attempt_count'],0)
            self.assertIsNone(replacement['checkpoint_id']);self.assertEqual(manager.request(self.job),original)
            self.assertEqual(manager.get(self.job)['result_id'],old_result)
            self.assertEqual(manager.request(receipt['job_id'])['snapshot_id'],original['snapshot_id'])
            with self.assertRaisesRegex(ValueError,'conflicts'):
                apply(manager,self.job,preview_id='0'*64,request_id='a'*32)
        self.assertEqual(doctor(self.root,full=True)['status'],'ok')
        with local_manager(self.root) as manager:self.assertEqual(wait(manager,receipt['job_id'])['status'],'succeeded')

    def test_lineage_failure_rolls_back_job_and_plan(self):
        with local_manager(self.root,autostart=False) as manager:
            report=preview(manager,self.job);before=manager.db.execute('SELECT count(*) FROM jobs')
            original=manager.db.execute
            def fail(sql,bindings=()):
                if sql.startswith('INSERT INTO job_lineage'):raise OSError('publication failed')
                return original(sql,bindings)
            with patch.object(manager.db,'execute',side_effect=fail):
                with self.assertRaises(OSError):apply(manager,self.job,preview_id=report['preview_id'],request_id='b'*32)
            self.assertEqual(manager.db.execute('SELECT count(*) FROM jobs'),before)
            self.assertEqual(manager.db.execute('SELECT count(*) FROM job_lineage'),[(0,)])

    def test_pruned_restart_keeps_its_request_key_reserved(self):
        with local_manager(self.root,autostart=False) as manager:
            report=preview(manager,self.job)
            receipt=apply(manager,self.job,preview_id=report['preview_id'],request_id='d'*32)
            manager.cancel(receipt['job_id'])
            manager.services.lifecycle.prune(time.time(),apply=True)
            self.assertEqual(manager.db.execute('SELECT deleted FROM job_lineage'),[(1,)])
            with self.assertRaisesRegex(ValueError,'RESTART_GONE'):
                apply(manager,self.job,preview_id=report['preview_id'],request_id='d'*32)
        self.assertEqual(doctor(self.root,full=True)['status'],'ok')

    def test_stale_preview_and_artifact_without_source_are_rejected(self):
        with local_manager(self.root,autostart=False) as manager:
            report=preview(manager,self.job)
            with self.assertRaisesRegex(ValueError,'PREVIEW_STALE'):
                apply(manager,self.job,preview_id='0'*64,request_id='c'*32)
            source_ref=manager.request(self.job)['input_ref']
            with patch('apps.openpine.restarts._source',return_value=None):
                with self.assertRaisesRegex(ValueError,'SOURCE_REQUIRED'):preview(manager,self.job)
            self.assertTrue(manager.objects.verify(source_ref).exists())

    def test_paged_reports_and_redacted_bounded_package(self):
        before=fingerprints(self.root)
        for kind in ('jobs','programs','replays','live','research','holdouts','launches'):
            page=compatibility(self.root,kind=kind,limit=1);self.assertLessEqual(len(page['items']),1)
        package=diagnostic_package(self.root,max_bytes=4096)
        raw=canonical(package);self.assertLessEqual(len(raw),4096)
        self.assertNotIn(str(self.root).encode(),raw)
        self.assertNotIn(b'"source":',raw);self.assertNotIn(b'"settings":',raw)
        self.assertEqual(before,fingerprints(self.root))

    def test_old_result_remains_readable_after_recompile_requirement(self):
        with local_manager(self.root,autostart=False) as manager:
            identity={**current_identity(),'spec_lock_sha256':'0'*64}
            row=Compatibility(manager.db,manager.objects,identity=identity).job(self.job)
            self.assertEqual(row['status'],'recompile_required')
            self.assertEqual(manager.result(self.job)['result']['outputs'][0]['values'][-1],135.)

    def test_recompile_preserves_input_values_and_resource_policy(self):
        from test_experiments import prepare
        with local_manager(self.root) as manager:
            base=prepare(manager)
            selected={'schema_version':'openpine.job-request.6','input_ref':base['input_ref'],
                      'snapshot_id':base['snapshot_id'],'timeout_seconds':30,'inputs':{'length':3,'scale':2.,'src':'open'}}
            identifier=manager.submit(selected)['job_id'];self.assertEqual(wait(manager,identifier)['status'],'succeeded')
            old=manager.plan(identifier);report=preview(manager,identifier)
            self.assertEqual(report['request']['settings']['values'],old.settings['values'])
            for field in ('memory_mb','threads','heartbeat_timeout','max_attempts','checkpoint_interval_bars'):
                self.assertEqual(report['plan'][field],old.payload()[field])
            content=manager.objects.get(manager.request(identifier)['input_ref'])
            forged={**content,'source_ref':manager.objects.put({'source':'//@version=6\nindicator("other")\nplot(close)','source_name':'other.pine'})}
            from apps.openpine.diagnostics import _source
            with self.assertRaisesRegex(ValueError,'SOURCE_IDENTITY'):_source(manager.objects,manager.objects.put(forged))

    def test_replay_research_and_live_policies_have_separate_decisions(self):
        from apps.openpine.replay import Replays
        from apps.openpine.experiments import Experiments
        from test_realtime import text, configuration, tape
        from test_experiments import prepare
        from test_live_market import prepare as live_prepare
        with local_manager(self.root,autostart=False) as manager:
            replay=Replays(manager);identifier=replay.create(configuration(manager,text()))['replay_id']
            replay.append(identifier,[e.payload() for e in tape()]);replay.advance(identifier,max_events=6)
            experiments=Experiments(manager,start=False)
            experiments.create({**prepare(manager),'variants':[{'length':2},{'length':3}]})
            history,live,session=live_prepare(manager)
            try:
                report=Compatibility(manager.db,manager.objects)
                self.assertEqual(report.replay(identifier)['status'],'compatible')
                self.assertEqual(report.page('research')['total'],2)
                self.assertTrue(all(row['status']=='compatible' for row in report.page('research')['items']))
                self.assertEqual(report.page('live')['items'][0]['status'],'compatible')
                with patch('apps.openpine.diagnostics.mapping_identity',return_value='0'*64):
                    self.assertEqual(report.page('live')['items'][0]['status'],'new_session_required')
                identity={**current_identity(),'execution_sha256':'0'*64}
                self.assertTrue(all(row['status']=='new_research_required' for row in Compatibility(manager.db,manager.objects,identity=identity).page('research')['items']))
                config=replay.repository.config(identifier);config['identity']='0'*64
                manager.db.execute('UPDATE replays SET config_ref=? WHERE replay_id=?',(manager.objects.put(config),identifier))
                self.assertEqual(report.replay(identifier)['code'],'REPLAY_POLICY_CHANGED')
            finally:live.close();history.close();experiments.close()


if __name__=='__main__':unittest.main()
