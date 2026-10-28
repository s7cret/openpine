"""Memory owners, bounded retention and committed/disposable branch lifetimes."""
import copy,gc,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID,BarFrame,OpenPineError
from packages.contracts.history_storage import plan,report,HORIZON
from packages.contracts.inputs import settings_for
from packages.contracts.memory import job_memory,charge
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.runtime.history import Ring,StorageBudget
from packages.runtime.history_memory import Allocation,HistoryAccount
from packages.runtime.rollback import branch
from packages.runtime.passes import commit
from packages.runtime.intrabar import IntrabarMemory
from test_series import source,bars

def frame(i):return BarFrame('unit.series',i,i*60000,float(i%17+1))

class UnifiedStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def evaluation(self,body,version=6):
        p=self.session.compile(source(version,body));r=self.session.validate_program(p);return r.prepare(p)
    def test_all_six_versions_bound_sma_change_and_bar_histories(self):
        for v in range(1,7):
            prefix='ta.' if v>=5 else ''
            ev=self.evaluation(f'n=bar_index%4+1\nplot({prefix}sma(close,n))\nplot({prefix}change(close,n))\nplot(close[2])',v)
            self.assertEqual([r['retention'] for r in ev.history_plan['calls']],[4,4])
            self.assertEqual([r['retention'] for r in ev.history_plan['histories']],[2])
            for i in range(40):ev.step(frame(i))
            self.assertEqual(sorted(len(r.items) for r in ev.history.buffers.values()),[2])
            self.assertEqual(ev.budget.cells,11)
    def test_shared_series_uses_largest_proved_offset_and_zero_needs_no_buffer(self):
        ev=self.evaluation('plot(close[0])\nplot(close[1])\nplot(close[17])')
        self.assertEqual(len(ev.history.buffers),1)
        for i in range(30):ev.step(frame(i))
        self.assertEqual(ev.budget.cells,17)
        zero=self.evaluation('plot(close[0])\nplot(ta.change(close,0))')
        for i in range(10):zero.step(frame(i))
        self.assertEqual(zero.budget.cells,0)
        self.assertEqual(list(zero.outputs.values())[1][2],[0.]*10)
    def test_unknown_offsets_and_mutated_lengths_keep_full_horizon(self):
        ev=self.evaluation('n=int(close)\nplot(close[n])\nplot(ta.sma(close,n))\nplot(ta.change(close,n))')
        self.assertEqual({r['retention'] for r in (*ev.history_plan['calls'],*ev.history_plan['histories'])},{HORIZON})
        ev.step(frame(0));self.assertLess(ev.budget.cells,10)
    def test_input_offset_proof_is_pinned_and_restore_rejects_foreign_capacity(self):
        p=self.session.compile(source(6,'n=input.int(3)\nplot(close[n])\nplot(ta.sma(close,n))'))
        a,b=plan(p),plan(p,settings_for(p,{'n':7}))
        self.assertNotEqual(a['sha256'],b['sha256']);self.assertEqual(b['histories'][0]['retention'],7)
        runtime=self.session.validate_program(p);ev=runtime.prepare(p)
        for i in range(8):ev.step(frame(i))
        saved=loads(canonical(ev.checkpoint()));bad=copy.deepcopy(saved)
        next(iter(bad['history'].values()))['capacity']=7
        fresh=runtime.prepare(p);before=runtime.history_account.used_bytes
        with self.assertRaises(ValueError):fresh.restore(bad)
        self.assertEqual((fresh.count,fresh.budget.cells),(0,0));self.assertEqual(runtime.history_account.used_bytes,before)
    def test_non_na_sma_reached_change_and_bar_history_keep_distinct_alignment(self):
        values=[1,None,4,8,None,2,16,3,None,9,5,7]
        for v in range(1,7):
            prefix='ta.' if v>=5 else ''
            body=f'n=bar_index%3+1\nplot({prefix}sma(close,n))\nplot({prefix}change(close,n))\nplot(close[2])'
            _,result=self.session.run(source(v,body),data=bars(values));defined=[];sma=[];change=[]
            for i,x in enumerate(values):
                if x is not None:defined.append(x)
                n=i%3+1;sma.append(sum(defined[-n:])/n if len(defined)>=n else None)
                change.append(x-values[i-n] if i>=n and x is not None and values[i-n] is not None else None)
            self.assertEqual([list(o.values) for o in result.outputs],[sma,change,[None,None,*values[:-2]]])
    def test_every_bar_restore_uses_exact_basic_buffer_charge(self):
        for v in range(1,7):
            prefix='ta.' if v>=5 else ''
            body=f'n=bar_index%3+1\nplot({prefix}sma(close,n))\nplot({prefix}change(close,n))\nplot(close[1])'
            ev=self.evaluation(body,v)
            for i in range(9):
                ev.step(frame(i));saved=loads(canonical(ev.checkpoint()));fresh=self.evaluation(body,v);fresh.restore(saved)
                self.assertEqual(canonical(fresh.checkpoint()),canonical(saved))
                self.assertEqual(fresh.budget.allocation.bytes,ev.budget.allocation.bytes)
    def test_zero_charge_owners_never_leave_finalizer_entries(self):
        account=HistoryAccount();a,b=Allocation(account),Allocation(account)
        a.reserve(0,'same');b.reserve(1,'same');a.close();b.close()
        self.assertEqual((account.used_bytes,account.owners),(0,{}))
    def test_typed_exact_integer_and_value_buffers_reserve_distinct_bytes(self):
        account=HistoryAccount();budget=StorageBudget(account=account)
        a,b=Ring(budget,2,'exact'),Ring(budget,2,'value')
        a.append(1<<4200);b.append(1.)
        self.assertEqual(account.used_bytes,charge('exact')+charge('value'))
        self.assertEqual(budget.cells,2);budget.close();self.assertEqual(account.used_bytes,0)
    def test_history_memory_failure_has_source_span_version_and_owner(self):
        ev=self.evaluation('plot(close[3])');ev.budget.allocation.account.limit_bytes=95
        with self.assertRaises(OpenPineError) as caught:ev.step(frame(0))
        d=caught.exception.diagnostic
        self.assertEqual((d.code,d.language_version),('HISTORY_MEMORY',6));self.assertIsNotNone(d.span)
        self.assertIn('history ',d.message);self.assertIn('unit.series',d.message)
    def test_arrays_and_preview_share_account_without_mutating_confirmed_heap(self):
        ev=self.evaluation('var a=array.new_float(5,1.0)\narray.push(a,close)\nplot(array.size(a))');ev.step(frame(0))
        account=ev.budget.allocation.account;before=account.used_bytes
        preview=branch(ev,None);preview.step(frame(1))
        self.assertEqual((ev.heap.cells,preview.heap.cells),(6,7));self.assertGreater(account.used_bytes,before)
        del preview;gc.collect();self.assertEqual(account.used_bytes,before)
    def test_invalid_array_restore_releases_partial_heap_even_with_exception_alive(self):
        body='var a=array.new_float(3,1.0)\nvar b=array.new_int(2,4)\nplot(array.size(a)+array.size(b))'
        ev=self.evaluation(body);ev.step(frame(0));saved=loads(canonical(ev.checkpoint()))
        fresh=self.evaluation(body);account=fresh.budget.allocation.account;before=account.used_bytes
        saved['heap']['objects'][1]['id']=saved['heap']['objects'][0]['id']
        try:fresh.restore(saved)
        except ValueError as error:
            self.assertEqual(account.used_bytes,before);self.assertEqual(fresh.heap.objects,{})
        else:self.fail('Forged heap accepted.')
    def test_legacy_ema_warmup_is_charged_and_resumes_for_v1_to_v4(self):
        for v in range(1,5):
            body='plot(ema(close,9))';ev=self.evaluation(body,v)
            for i in range(3):ev.step(frame(i))
            ledger=ev.memory_status()['ledger'];self.assertTrue(any('EMA warmup' in r['owner'] for r in ledger['owners']))
            fresh=self.evaluation(body,v);fresh.restore(loads(canonical(ev.checkpoint())))
            for i in range(3,12):ev.step(frame(i));fresh.step(frame(i))
            self.assertEqual(ev.outputs,fresh.outputs)
    def test_selected_branches_keep_confirmed_charges_and_discard_overlay_storage(self):
        body='n=bar_index%4+1\nplot(ta.sma(close,n))\nplot(ta.wma(close,n))\nplot(ta.highest(close,n))\nplot(close[1])\nplot(ta.highestbars(close,n))\nplot(ta.vwma(close,n))\nplot(ta.mom(close,n))'
        ev=self.evaluation(body);ev.step(replace(frame(0),volume=10.));steady=[]
        for i in range(1,60):
            candidate=branch(ev,None);candidate.deferred_commit=True;candidate.step(replace(frame(i),volume=10.));commit(ev,candidate)
            if i>10:steady.append(ev.memory_status()['ledger']['charged_bytes'])
        self.assertEqual(len(set(steady)),1)
        data=bars([i%17+1 for i in range(60)]);data=replace(data,bars=tuple(replace(b,volume=10.) for b in data.bars),schema_version='openpine.historical-bars.2')
        _,direct=self.session.run(source(6,body),data=data)
        self.assertEqual([tuple(row[2]) for row in ev.outputs.values()],[o.values for o in direct.outputs])
    def test_varip_snapshots_are_charged_and_clear_releases_their_owner(self):
        ev=self.evaluation('varip a=array.new_float(3,1.0)\nplot(array.size(a))');ev.step(frame(0))
        account=ev.budget.allocation.account;before=account.used_bytes;memory=IntrabarMemory();memory.capture(ev,ev)
        self.assertGreater(account.used_bytes,before);memory.clear();self.assertEqual(account.used_bytes,before)
    def test_job_partitions_include_io_base_and_shared_restore_reserve(self):
        for mb in (128,256,512,2048):
            p=job_memory(mb,8)
            self.assertEqual(sum(p[k] for k in ('state_and_restore_bytes','io_bytes','base_and_transient_bytes')),p['total_bytes'])
            self.assertLessEqual(p['per_io_mb']*8*1024**2,p['io_bytes']);self.assertFalse(p['charge_is_rss'])
        # All 16 request visibility streams plus primary reader/writer remain
        # supported within the existing default worker policy, without rounding
        # each individual cache above the common IO envelope.
        self.assertGreaterEqual(job_memory(512,18)['per_io_mb'],8)
    def test_owner_inventory_includes_arrays_transients_and_every_private_request(self):
        p=self.session.compile(source(6,'plot(request.security("TEST:A","2",ta.sma(close,4)+close[1]))'))
        r=report(p);self.assertEqual([len(c['histories']) for c in r['contexts']],[0,1])
        self.assertEqual({o['owner'] for o in r['additional_owners']},{'arrays','preview','restore','candle_readers_and_output_writer','checkpoint_codec_and_python_overhead','in_memory_API_outputs'})
    def test_composed_disk_output_continues_in_a_different_process(self):
        from tools.unified_storage_soak import run
        with tempfile.TemporaryDirectory() as directory:r=run(Path(directory)/'proof',16)
        self.assertTrue(r['process_restarted']);self.assertNotEqual(*r['worker_pids'])
        self.assertEqual((r['events'],r['verified_output_points'],r['persisted_output_points']),(48,480,480))

if __name__=='__main__':unittest.main()
