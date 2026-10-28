"""RAM admission, exact checkpoint blocks and retained-byte lifetimes."""
import copy,gc,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from packages.contracts.host_memory import HostMemoryPolicy,MIB
from packages.execution.host_memory import HostAdmission
from packages.execution.limits import WorkLimiter,CoordinatorBusy
from packages.runtime.history import StorageBudget,Ring
from packages.runtime.history_memory import HistoryAccount
from packages.runtime.series import SMASeedEMAState
from packages.runtime.series.basic import SMAState
from packages.runtime.series.composite import MACDState,fork_composite
from packages.runtime.sma_checkpoint import export_prefixes,restore_prefixes
from packages.storage.objects import ObjectStore
from packages.storage import checkpoint_tree

class HostMemoryTests(unittest.TestCase):
    def admission(self,available=1024):
        self.sample=dict(total_bytes=4096*MIB,available_bytes=available*MIB,source='test')
        self.tick=0
        return HostAdmission(probe=lambda:dict(self.sample),clock=lambda:self.tick)
    def test_real_probe_is_explicit_and_bounded(self):
        from packages.execution.resources import host_memory
        sample=host_memory()
        if sample is not None:self.assertTrue(0<=sample['available_bytes']<=sample['total_bytes'])
    def test_parallel_leases_hysteresis_materialization_and_release(self):
        host=self.admission();self.assertIsNone(host.reserve('a',512*MIB))
        self.assertEqual(host.reserve('b',512*MIB)['reason'],'host_memory')
        host.materialized('a',256*MIB,512*MIB)
        self.assertEqual(host.reserve('b',512*MIB)['reason'],'host_memory')
        self.sample['available_bytes']=1100*MIB;self.tick+=1
        self.assertIsNone(host.reserve('b',512*MIB));host.release('a');host.release('b')
        self.assertEqual(host.snapshot()['pending_reservation_bytes'],0)
    def test_unavailable_sample_uses_configured_limits_without_invented_zero(self):
        host=HostAdmission(probe=lambda:None)
        self.assertIsNone(host.reserve('a',512*MIB));self.assertIsNone(host.snapshot()['available_bytes'])
        self.assertEqual(host.snapshot()['metric_status'],'unavailable')
    def test_coordinator_metadata_stays_available_under_ram_pressure(self):
        limiter=WorkLimiter(host=self.admission(0))
        with patch.object(limiter,'healthy',return_value=True):
            with limiter.slot('metadata'):pass
            with self.assertRaises(CoordinatorBusy) as caught:
                with limiter.slot('prepare'):pass
        self.assertEqual(caught.exception.admission['reason'],'host_memory')
        self.assertEqual(limiter.reserved_mb,0)
    def test_queued_worker_waits_without_attempt_and_resumes_when_ram_returns(self):
        from apps.openpine.bootstrap import local_manager
        from test_jobs import request,wait
        host=self.admission(4096)
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,host_admission=host,max_workers=1) as manager:
            with manager.lock:
                job=manager.submit(request())['job_id']
                self.sample['available_bytes']=0;self.tick+=1
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and manager.get(job)['phase']!='waiting_resources':time.sleep(.02)
            state=manager.get(job)
            self.assertEqual((state['phase'],state['attempt_count']),('waiting_resources',0));self.assertFalse(manager.children)
            self.sample['available_bytes']=4096*MIB;self.tick+=1
            result=wait(manager,job)
            self.assertEqual(result['status'],'succeeded',result);self.assertEqual(result['attempt_count'],1)
            self.assertGreaterEqual(result['peak_resident_bytes'],result['resident_bytes'])
            self.assertEqual(host.leases,{})

class CompactCheckpointTests(unittest.TestCase):
    def test_exact_sma_wrapped_na_and_extreme_doubles(self):
        for horizon in (1,3,11):
            state=SMAState(StorageBudget(),horizon)
            for i in range(70):
                value=None if i%9==0 else [1e200,1e-200,-1e200,3.5,-0.0][i%5]
                state.update(value,i%horizon+1)
                compact=export_prefixes(state)
                ring=restore_prefixes(compact,StorageBudget(),horizon+1,state.count,state.total)
                self.assertEqual((ring.items,ring.count),(state.prefixes.items,state.prefixes.count))
                other=copy.copy(state);other.prefixes=ring
                self.assertEqual(other.update(1.25,horizon),state.update(1.25,horizon))
    def test_forged_anchor_or_sample_rejected(self):
        state=SMAState(StorageBudget(),3)
        for i in range(10):state.update(float(i),3)
        value=export_prefixes(state)
        for change in (lambda v:v.update(anchor=v['anchor']+1),lambda v:v['samples'].append(1.),lambda v:v.update(capacity=True)):
            bad=copy.deepcopy(value);change(bad)
            with self.assertRaises(ValueError):restore_prefixes(bad,StorageBudget(),4,state.count,state.total)
    def test_block_tree_roundtrip_unicode_wide_numbers_and_reference_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);value={'a':[i for i in range(20000)],'b':'🙂漢字\n'*10000,'c':[2**2100,None,False,1.25,{},[]]}
            sha=checkpoint_tree.put(objects,value);root=objects.get(sha)
            self.assertEqual(root['schema_version'],checkpoint_tree.SCHEMA)
            marked=set();checkpoint_tree.references(objects,sha,marked.add)
            self.assertEqual(marked,{p['object_id'] for p in root['parts']})
            self.assertTrue(all(objects.path(p).stat().st_size<=checkpoint_tree.BLOCK for p in marked))
            self.assertEqual(checkpoint_tree.get(objects,sha),value)
    def test_decode_limit_checked_before_loading_block_and_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);sha=checkpoint_tree.put(objects,list(range(20000)))
            with patch.object(objects,'get',wraps=objects.get) as reader:
                with self.assertRaisesRegex(ValueError,'memory budget'):checkpoint_tree.get(objects,sha,limit_bytes=4*checkpoint_tree.BLOCK)
                self.assertEqual(reader.call_count,1)
            part=objects.get(sha)['parts'][0]['object_id'];objects.path(part).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):checkpoint_tree.get(objects,sha)
    def test_small_legacy_is_readable_and_oversized_legacy_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=ObjectStore(directory);sha=objects.put({'old':[1,2]})
            self.assertEqual(checkpoint_tree.get(objects,sha),{'old':[1,2]})
            large=objects.put_stream(list(range(20000)))
            with patch.object(objects,'get',side_effect=AssertionError('must not parse')):
                with self.assertRaisesRegex(ValueError,'safe decode'):checkpoint_tree.get(objects,large,limit_bytes=4*checkpoint_tree.BLOCK)

class RetainedByteTests(unittest.TestCase):
    def test_legacy_ema_releases_seed_bytes(self):
        account=HistoryAccount();budget=StorageBudget(account=account);state=SMASeedEMAState(budget=budget)
        for i in range(8):state.update(float(i),9)
        self.assertEqual(account.used_bytes,8*96);state.update(8.,9)
        self.assertEqual(account.used_bytes,0);self.assertEqual(budget.cells,0)
    def test_macd_releases_bytes_but_keeps_logical_cell_quota(self):
        from packages.runtime.series.composite import export_composite,restore_composite
        from packages.runtime.series import KernelError
        account=HistoryAccount();budget=StorageBudget(account=account);state=MACDState(budget,4)
        for i in range(30):state.update(float(i),3,5,4)
        self.assertEqual(budget.cells,12);self.assertEqual(account.used_bytes,0)
        kind,value=export_composite(state)
        tight=HistoryAccount(4096);restored_budget=StorageBudget(account=tight);restored_budget.scratch(1,'state')
        restored=restore_composite(kind,value,restored_budget,30,4)
        self.assertEqual((restored_budget.cells,tight.used_bytes),(12,4096))
        self.assertEqual(restored.update(30.,3,5,4),state.update(30.,3,5,4))
        with self.assertRaises(KernelError) as caught:restore_composite(kind,value,StorageBudget(limit=11),30,4)
        self.assertEqual(caught.exception.code,'HISTORY_STORAGE')
        pending=MACDState(StorageBudget(),4)
        for i in range(2):pending.update(float(i),3,5,4)
        kind,value=export_composite(pending)
        for limit,valid in ((384,True),(383,False)):
            b=StorageBudget(account=HistoryAccount(limit))
            if valid:
                restore_composite(kind,value,b,2,4);self.assertEqual((b.cells,b.allocation.account.used_bytes),(4,384))
            else:
                with self.assertRaises(KernelError) as caught:restore_composite(kind,value,b,2,4)
                self.assertEqual(caught.exception.code,'HISTORY_MEMORY')
    def test_discarded_seed_preview_never_releases_confirmed_samples(self):
        from packages.runtime.rollback import AppendOverlay
        account=HistoryAccount();budget=StorageBudget(account=account);state=SMASeedEMAState(budget=budget)
        state.update(1.,3);state.update(2.,3)
        branch=copy.copy(state);branch.budget=copy.copy(budget);branch.pending=AppendOverlay(state.pending)
        branch.update(3.,3);self.assertEqual(account.used_bytes,192)
        branch.budget.close();self.assertEqual(len(state.pending),2);self.assertEqual(account.used_bytes,192)
    def test_string_history_charges_payload_and_releases_on_overwrite(self):
        account=HistoryAccount();budget=StorageBudget(account=account);ring=Ring(budget,2,'string')
        ring.append('x'*60000);ring.append('🙂')
        self.assertEqual(account.used_bytes,2*256+4*60001)
        ring.append('a');self.assertEqual(account.used_bytes,2*256+8)
        ring.append(None);self.assertEqual(account.used_bytes,2*256+4)
        self.assertEqual(budget.cells,2);budget.close();self.assertEqual(account.used_bytes,0)

    def test_string_preview_disposal_and_selection_keep_exact_payload_ownership(self):
        from packages.runtime.rollback import RingOverlay
        from packages.runtime.passes import materialize
        account=HistoryAccount();budget=StorageBudget(account=account);ring=Ring(budget,1,'string')
        ring.append('a');initial=account.used_bytes
        candidate=copy.copy(budget);overlay=RingOverlay(ring,candidate);overlay.append('x'*10000)
        self.assertGreater(account.used_bytes,initial+40000);candidate.close();self.assertEqual(account.used_bytes,initial)
        candidate=copy.copy(budget);overlay=RingOverlay(ring,candidate);overlay.append('x'*10000)
        selected=materialize(overlay,candidate,None);budget.allocation.transfer(candidate.allocation);candidate.temporary.close();selected.reconcile()
        self.assertEqual(account.used_bytes,256+40000);self.assertEqual(selected.ago(0),'x'*10000)
        candidate.close();self.assertEqual(account.used_bytes,0)

class ComposedRecoveryTests(unittest.TestCase):
    def test_real_managed_worker_and_coordinator_crash_read_back_every_value(self):
        from tools.managed_recovery_soak import run
        with tempfile.TemporaryDirectory() as directory:
            report=run(Path(directory)/'managed',20000)
            self.assertEqual((report['attempts'],report['verified_parquet_values']),(3,600000))
            self.assertGreater(report['checkpoint_block_objects'],0)
    def test_all_version_realtime_plateau_including_open_bar_restore(self):
        from tools.realtime_memory_soak import run
        with tempfile.TemporaryDirectory() as directory:
            report=run(Path(directory)/'realtime',32,4)
            self.assertEqual([r['version'] for r in report['versions']],list(range(1,7)))
            self.assertEqual(report['events'],3456)

if __name__=='__main__':unittest.main()
