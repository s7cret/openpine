"""Storage proofs, retained data, private ownership and atomic resource failures."""
import copy,gc,random,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.history_storage import plan,report,HORIZON,CELL_BYTES,policy
from packages.contracts.wire import canonical,loads,read_json
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.runtime.history import StorageBudget
from packages.runtime.history_memory import HistoryAccount
from packages.runtime.series import KernelError
from packages.runtime.series.dynamic_windows import RangeWindow,PrefixWindow,export_window,restore_window,fork_window
from packages.runtime.series.dynamic_signals import SignalWindow,KINDS,export_window as export_signal,restore_window as restore_signal,fork_window as fork_signal
from tools.specification import ROOT
from test_dynamic_signals import code,body,args
from test_composite import DATA

class HistoryStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def program(self,text,version=6):return self.session.compile(code(version,text))
    def capacities(self,text,settings=None,version=6):
        p=self.program(text,version);return plan(p,settings)['calls']
    def test_all_versions_modulo_proves_all_seven_signal_bounds(self):
        for v in range(1,7):
            rows=plan(self.program(body(v),v))['calls']
            self.assertEqual(len(rows),7);self.assertEqual({r['retention'] for r in rows},{4})
            self.assertEqual({r['legal_max'] for r in rows},{HORIZON})
    def test_min_max_clamp_over_unknown_series_proves_retention(self):
        rows=self.capacities('n=math.max(1,math.min(17,int(close)))\nplot(ta.mom(close,n))')
        self.assertEqual(rows[0]['retention'],17)
    def test_frozen_input_changes_bound_and_plan_identity(self):
        p=self.program('cap=input.int(7)\nn=bar_index%cap+1\nplot(ta.wma(close,n))')
        a,b=plan(p),plan(p,settings_for(p,{'cap':13}))
        self.assertEqual([a['calls'][0]['retention'],b['calls'][0]['retention']],[7,13]);self.assertNotEqual(a['sha256'],b['sha256'])
    def test_unknown_mutated_and_overflowing_lengths_keep_full_horizon(self):
        for text in ('n=int(close)','n=1\nn:=bar_index%4+1','n=bar_index*9223372036854775807'):
            self.assertEqual(self.capacities(text+'\nplot(ta.mom(close,n))')[0]['retention'],HORIZON)
    def test_specialized_function_parameters_and_local_bindings_propagate_bounds(self):
        rows=self.capacities('f(x,n) =>\n    k=n%7+1\n    ta.wma(x,k)\nplot(f(close,bar_index))')
        self.assertEqual(rows[0]['retention'],7)
    def test_branch_union_never_uses_only_the_small_observed_length(self):
        rows=self.capacities('n=bar_index<100 ? 1 : 4096\nplot(ta.highest(close,n))')
        self.assertEqual(rows[0]['retention'],4096)
    def test_private_requests_have_independent_plans_and_primary_has_no_private_calls(self):
        p=self.program('plot(request.security("TEST:A","2",ta.mom(close,bar_index%4+1)))\nplot(request.security("TEST:A","2",ta.mom(close,bar_index%8+1)))')
        r=report(p);self.assertEqual([len(c['calls']) for c in r['contexts']],[0,1,1])
        self.assertEqual([c['calls'][0]['retention'] for c in r['contexts'][1:]],[4,8]);self.assertEqual(r['max_cells'],14)
    def test_incremental_trees_allocate_geometrically_without_initial_horizon(self):
        for s in (RangeWindow(StorageBudget(),'window.highest'),SignalWindow(StorageBudget(),'swindow.highestbars')):
            cells=[]
            for i in range(17):s.update(float(i),1);cells.append(s.budget.cells)
            self.assertEqual([cells[i] for i in (0,1,2,7,8,16)],[2,4,8,16,32,64])
    def test_unknown_horizon_survives_long_shrink_then_abrupt_expansion(self):
        for kind in KINDS:
            s=SignalWindow(StorageBudget(),kind)
            for i in range(4096):s.update(*args(kind,float(i+1),1.,1))
            restored=restore_signal(kind,loads(canonical(export_signal(s)[1])),StorageBudget(),4096,6)
            x=4097.;n=4096
            expected={'swindow.vwma':2049.5,'swindow.mom':4096.,'swindow.roc':409600.,'swindow.rising':True,'swindow.falling':False,'swindow.highestbars':0,'swindow.lowestbars':-4095}[kind]
            self.assertEqual(s.update(*args(kind,x,1.,n)),expected)
            self.assertEqual(restored.update(*args(kind,x,1.,n)),expected)
    def test_proven_non_power_two_horizon_wrap_matches_lists(self):
        rng=random.Random(173);xs=[]
        for kind in ('window.highest','window.lowest'):
            s=RangeWindow(StorageBudget(),kind,13);xs=[]
            for i in range(140):
                x=None if i%11==0 else float(rng.randrange(30));xs.append(x);n=i%13+1
                kept=[y for y in xs[-n:] if y is not None]
                self.assertEqual(s.update(x,n),(max if kind.endswith('highest') else min)(kept) if kept else None)
            k,v=export_window(s);r=restore_window(k,loads(canonical(v)),StorageBudget(),140,13)
            self.assertEqual(export_window(r),export_window(s))
    def test_growth_budget_failure_preserves_tree_samples_and_counters(self):
        for s,export in ((RangeWindow(StorageBudget(limit=8),'window.highest'),export_window),(SignalWindow(StorageBudget(limit=8),'swindow.highestbars'),export_signal)):
            for x in range(4):s.update(float(x),1)
            before=export(s);cells=s.budget.cells
            with self.assertRaises(KernelError):s.update(8.,1)
            self.assertEqual((export(s),s.budget.cells),(before,cells))
    def test_preview_growth_is_detached_and_non_growth_changes_logarithmic_cells(self):
        for s,export,fork in ((RangeWindow(StorageBudget(),'window.highest'),export_window,fork_window),(SignalWindow(StorageBudget(),'swindow.highestbars'),export_signal,fork_signal)):
            for i in range(32):s.update(float(i),1)
            original=export(s);preview=fork(s,copy.copy(s.budget));preview.update(100.,32)
            self.assertEqual(export(s),original);self.assertIsNot(preview.nodes,s.nodes)
            s.update(32.,1);original=export(s);preview=fork(s,copy.copy(s.budget));preview.update(100.,30)
            self.assertLessEqual(len(preview.nodes.changed),7);self.assertEqual(export(s),original)
    def test_job_account_combines_contexts_and_releases_disposable_preview(self):
        account=HistoryAccount(8*CELL_BYTES);a=StorageBudget(account=account);b=StorageBudget(account=account)
        s=SignalWindow(a,'swindow.mom',horizon=2);s.update(1.,1);b.reserve()
        self.assertEqual(account.used_bytes,2*CELL_BYTES)
        preview=copy.copy(a);preview.scratch(3);self.assertEqual(account.used_bytes,5*CELL_BYTES)
        preview.close();self.assertEqual(account.used_bytes,2*CELL_BYTES)
        b.close();a.close();self.assertEqual(account.used_bytes,0)
    def test_changed_cells_at_full_capacity_are_charged_in_preview(self):
        account=HistoryAccount(20*CELL_BYTES);b=StorageBudget(account=account);s=SignalWindow(b,'swindow.mom',horizon=1)
        s.update(1.,1);s.update(2.,1);preview=fork_signal(s,copy.copy(b));before=account.used_bytes
        preview.update(3.,1);self.assertEqual(account.used_bytes,before+2*CELL_BYTES)
        del preview;gc.collect();self.assertEqual(account.used_bytes,2*CELL_BYTES)
    def test_resize_peak_is_checked_before_publishing_and_scratch_released(self):
        a=HistoryAccount(9*CELL_BYTES);b=StorageBudget(account=a);s=RangeWindow(b,'window.highest')
        s.update(1.,1);s.update(2.,1);before=export_window(s)
        with self.assertRaisesRegex(KernelError,'memory'):s.update(3.,1)
        self.assertEqual(export_window(s),before);self.assertEqual((b.cells,a.used_bytes),(4,4*CELL_BYTES))
    def test_admitted_runtime_detects_forged_checkpoint_plan_and_capacity_atomically(self):
        p=self.program('n=bar_index%4+1\nplot(ta.mom(close,n))');runtime=self.session.validate_program(p)
        d=HistoricalDriver(runtime.prepare(p),MemorySource(DATA));saved=[];d.run(pause_after=4,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
        for mutate in (lambda v:v.update(history_plan_sha256='0'*64),lambda v:v['states'][0]['value'].update(retention=3)):
            bad=copy.deepcopy(saved[0]);mutate(bad);ev=runtime.prepare(p);before=runtime.history_account.used_bytes
            with self.assertRaises(ValueError):ev.restore(bad)
            self.assertEqual((ev.count,ev.states),(0,{}));self.assertEqual(runtime.history_account.used_bytes,before)
    def test_restore_uses_same_proof_and_exact_allocated_cell_count(self):
        p=self.program('n=bar_index%4+1\nplot(ta.vwma(close,n))\nplot(ta.highestbars(close,n))');runtime=self.session.validate_program(p)
        d=HistoricalDriver(runtime.prepare(p),MemorySource(DATA));saved=[];d.run(pause_after=5,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
        ev=runtime.prepare(p);ev.restore(saved[0]);self.assertEqual(ev.budget.cells,saved[0]['budget_cells'])
        self.assertEqual(canonical(ev.checkpoint()),canonical(saved[0]));self.assertTrue(all(s.horizon==4 for s in ev.states.values()))
    def test_invalid_bound_is_rejected_before_state_update(self):
        for s,export in ((RangeWindow(StorageBudget(),'window.highest',4),export_window),(PrefixWindow(StorageBudget(),'window.wma',4),export_window),(SignalWindow(StorageBudget(),'swindow.mom',horizon=4),export_signal)):
            s.update(1.,1);before=export(s)
            with self.assertRaisesRegex(KernelError,'verified retention'):s.update(2.,5)
            self.assertEqual(export(s),before)
    def test_requirements_surface_primary_private_estimates_without_running_data(self):
        from apps.openpine.launches import requirements_for
        p=self.program('plot(request.security("TEST:A","2",ta.mom(close,bar_index%4+1)))')
        r=requirements_for(p,'a'*64)['history_resources']
        self.assertEqual(r['retained_charge_bytes'],5*CELL_BYTES);self.assertEqual(r['restore_peak_charge_bytes'],10*CELL_BYTES)
    def test_profile_is_published_and_session_account_uses_worker_policy(self):
        self.assertEqual(policy(),read_json(ROOT/'spec/history-storage-profile.json'))
        session=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=64);runtime=session.validate_program(session.compile(code(6,'plot(close)')))
        self.assertEqual(runtime.history_account.limit_bytes,32*1024**2)
    def test_composed_scheduler_restarts_three_markets_and_checks_every_signal(self):
        import tempfile
        from pathlib import Path
        from tools.history_storage_soak import run
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'proof';r=run(root,32)
            self.assertEqual((root/'checkpoint-before-growth.json').stat().st_size,r['checkpoint_bytes_before_growth'])
            self.assertEqual((root/'checkpoint-final.json').stat().st_size,r['checkpoint_bytes_final'])
        self.assertEqual((r['events'],r['verified_output_points'],r['private_contexts']),(96,672,2))
        self.assertGreater(r['peak_resident_bytes'],0)
    def test_replay_worker_applies_saved_memory_policy_to_reused_session(self):
        import tempfile
        from pathlib import Path
        from dataclasses import asdict
        from unittest.mock import patch
        from apps.openpine.bootstrap import local_manager
        from apps.openpine.replay import Replays
        from apps.replay_worker import execute
        from packages.contracts.storage import ResourcePolicy
        from packages.contracts.contexts import MarketContext
        from packages.execution.realtime import ReplayEngine
        limits=[]
        def engine(session,*a,**kw):
            result=ReplayEngine(session,*a,**kw);limits.append(result.runtime.history_account.limit_bytes);return result
        with tempfile.TemporaryDirectory() as directory,local_manager(Path(directory)/'store',policy=ResourcePolicy(workers=1,memory_mb_per_worker=128,total_memory_mb=128)) as manager:
            replay=Replays(manager);key=replay.create({'input_ref':manager.freeze_input({'source':code(6,'plot(close)'),'source_name':'policy.pine'}),
                'contexts':[asdict(MarketContext('primary','TEST:A','1','generated.1'))],'primary_id':'primary','broker_settings':None,'timeout_seconds':30})['replay_id']
            attempt=Path(directory)/'attempt';attempt.mkdir()
            reused=Session(profile_id=HISTORICAL_PROFILE_ID,memory_mb=512)
            with patch('apps.replay_worker.ReplayEngine',side_effect=engine):execute(manager.directory,key,attempt,session=reused)
            self.assertEqual(limits,[64*1024**2]);self.assertEqual(reused.memory_mb,128)
    def test_preview_resize_keeps_full_private_allocation_charged_until_disposal(self):
        for state,fork in ((RangeWindow,fork_window),(SignalWindow,fork_signal)):
            account=HistoryAccount(25*CELL_BYTES);budget=StorageBudget(account=account)
            original=state(budget,'window.highest' if state is RangeWindow else 'swindow.highestbars')
            for i in range(4):original.update(float(i),1)
            branch=fork(original,copy.copy(budget));branch.update(5.,1)
            # Eight original slots plus sixteen privately rebuilt branch slots.
            self.assertEqual(account.used_bytes,24*CELL_BYTES)
            del branch;gc.collect();self.assertEqual(account.used_bytes,8*CELL_BYTES)

if __name__=='__main__':unittest.main()
