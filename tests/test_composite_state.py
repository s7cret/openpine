"""Checkpoint, rollback and requested tuple boundaries on all six versions."""
import copy
from dataclasses import replace
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.contexts import ContextBinding
from packages.contracts.wire import canonical,loads
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity
from packages.runtime.history import StorageBudget
from packages.runtime.series.composite import MomentsState,fork_composite
from test_composite import DATA,VALUES,source,macd_oracle,moments_oracle
from test_request_expressions import PRIMARY,HIGHER,DATA as PRIMARY_DATA,REQUESTED,OHLCV,mapped,events
from test_realtime import Sink,tape,historical,PRIMARY as SINGLE


def kernels(version):
    p='ta.' if version>=5 else ''
    return f'[m,s,h]={p}macd(close,2,4,3)\n[b,u,l]={p}bb(close,3,2)\nplot(h)\nplot(b)\nplot(u)\nplot(l)\nplot({p}variance(close,3))\nplot({p}stdev(close,3))'


class CompositeStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_every_checkpoint_and_codec_roundtrip(self):
        for version in range(1,7):
            program=self.session.compile(source(version,kernels(version)))
            expected=self.session.execute(program,data=DATA)
            runtime=self.session.validate_program(program)
            for cut in range(1,len(VALUES)+1):
                first=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));saved=[]
                first.run(pause_after=cut,checkpoint=lambda e:saved.append(loads(canonical(e.checkpoint()))))
                self.assertEqual(saved[0]['schema_version'],'openpine.runtime-state.3')
                restored=HistoricalDriver(runtime.prepare(program),MemorySource(DATA));restored.restore(saved[0]);restored.run()
                self.assertEqual(restored.finish(data_identity(DATA)),expected)

    def test_corrupt_exact_statistics_and_macd_seed_are_rejected_before_publication(self):
        for version in (1,6):
            program=self.session.compile(source(version,kernels(version)));runtime=self.session.validate_program(program)
            ev=runtime.prepare(program);driver=HistoricalDriver(ev,MemorySource(DATA));driver.run(pause_after=2,checkpoint=lambda e:None)
            saved=loads(canonical(ev.checkpoint()))
            mutations=(lambda s:s['states'][0].update(kind='bb'),
                lambda s:next(r for r in s['states'] if r['kind']=='moments')['value'].update(total=0),
                lambda s:next(r for r in s['states'] if r['kind']=='moments')['value'].update(squares=-1),
                lambda s:next(r for r in s['states'] if r['kind']=='bb')['value'].update(count=True),
                lambda s:next(r for r in s['states'] if r['kind']=='macd')['value']['seed_cells'].__setitem__(0,100001),
                lambda s:s.update(budget_cells=s['budget_cells']+1),lambda s:s.update(schema_version='openpine.runtime-state.2'))
            for mutate in mutations:
                bad=copy.deepcopy(saved);mutate(bad);target=runtime.prepare(program)
                with self.subTest(version=version),self.assertRaises(ValueError):target.restore(bad)
                self.assertEqual((target.count,target.states,target.values),(0,{},{}))

    def test_preview_rollback_and_resume_at_every_observed_event(self):
        market=list(tape(5))
        for version in range(1,7):
            program=self.session.compile(source(version,kernels(version)))
            def fresh():
                sink=Sink();return ReplayEngine(self.session,program,(SINGLE,),'primary',sink),sink
            original,sink=fresh()
            for event in market:
                before=canonical(original.evaluation.checkpoint()) if original.evaluation.count else None
                original.apply(event)
                if event.kind!='close' and before is not None:self.assertEqual(canonical(original.evaluation.checkpoint()),before)
            direct=self.session.execute(program,data=historical(market))
            for output in direct.outputs:self.assertEqual(tuple(r[3] for r in sink.rows if r[2]==output.output_id),output.values)
            for cut in range(1,len(market)+1):
                first,prefix=fresh()
                for event in market[:cut]:first.apply(event)
                resumed,tail=fresh();resumed.restore(loads(canonical(first.checkpoint())))
                for event in market[cut:]:resumed.apply(event)
                self.assertEqual(prefix.rows+tail.rows,sink.rows)

    def requested(self,version,expression):
        api='security' if version<5 else 'request.security'
        return self.session.compile(source(version,f'[a,b,c]={api}("{HIGHER.symbol}","2",{expression})\nplot(a)\nplot(b)\nplot(c)'))

    def test_requested_tuple_mapping_numeric_expectations_and_joint_checkpoint(self):
        closes=tuple(r[3] for r in OHLCV)
        for version in range(1,7):
            p='ta.' if version>=5 else ''
            for expression,expected in ((f'{p}macd(close,2,3,2)',macd_oracle(closes,2,3,2,version)),
                (f'[close,{p}variance(close,2),{p}stdev(close,2)]',[closes,*moments_oracle(closes,2)[1:]])):
                program=self.requested(version,expression)
                def arguments():return {'primary_context':PRIMARY,'context_sources':[(ContextBinding(HIGHER,'1'*64),MemorySource(REQUESTED))]}
                baseline=self.session.execute(program,data=PRIMARY_DATA,**arguments())
                for output,values in zip(baseline.outputs,expected,strict=True):
                    for a,b in zip(output.values,mapped(values,version),strict=True):
                        if b is None:self.assertIsNone(a)
                        else:self.assertAlmostEqual(a,b,places=11)
                def fresh():
                    runtime=self.session.validate_program(program);primary=MemorySource(PRIMARY_DATA);args=arguments()
                    contexts=self.session.context_scheduler(program,runtime,primary,args['primary_context'],args['context_sources'])
                    return HistoricalDriver(runtime.prepare(program),primary,contexts)
                for cut in (1,2,3,7,11):
                    first=fresh();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    resumed=fresh();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(PRIMARY_DATA)),baseline)

    def test_requested_tuple_preview_cannot_modify_confirmed_sink_and_restores_history(self):
        market=list(events())
        for version in range(1,7):
            p='ta.' if version>=5 else '';program=self.requested(version,f'{p}macd(close,2,3,2)')
            def fresh():
                sink=Sink();return ReplayEngine(self.session,program,(PRIMARY,HIGHER),'primary',sink),sink
            baseline,sink=fresh()
            for event in market:baseline.apply(event)
            for cut in (1,4,8,len(market)-1):
                first,prefix=fresh()
                for event in market[:cut]:first.apply(event)
                saved=loads(canonical(first.checkpoint()));resumed,tail=fresh();resumed.restore(saved)
                for event in market[cut:]:resumed.apply(event)
                self.assertEqual(prefix.rows+tail.rows,sink.rows)
                self.assertEqual(canonical(resumed.checkpoint()),canonical(baseline.checkpoint()))

    def test_tuple_gaps_are_atomic_and_future_changes_respect_visibility(self):
        closes=tuple(r[3] for r in OHLCV)
        for version in range(1,7):
            p='ta.' if version>=5 else '';api='security' if version<5 else 'request.security'
            flags=',true' if version<3 else ',gaps=barmerge.gaps_on'
            text=f'[a,b,c]={api}("{HIGHER.symbol}","2",{p}macd(close,2,3,2){flags})\nplot(a)\nplot(b)\nplot(c)'
            program=self.session.compile(source(version,text))
            def execute(data):return self.session.execute(program,data=PRIMARY_DATA,primary_context=PRIMARY,
                context_sources=[(ContextBinding(HIGHER,'1'*64),MemorySource(data))])
            result=execute(REQUESTED)
            for output,expected in zip(result.outputs,macd_oracle(closes,2,3,2,version),strict=True):
                for a,b in zip(output.values,mapped(expected,version,True),strict=True):
                    if b is None:self.assertIsNone(a)
                    else:self.assertAlmostEqual(a,b,places=11)
            altered=replace(REQUESTED,bars=(*REQUESTED.bars[:-1],replace(REQUESTED.bars[-1],close=999.,high=1000.)))
            changed=execute(altered)
            for a,b in zip(result.outputs,changed.outputs,strict=True):self.assertEqual(a.values[:10],b.values[:10])

    def test_statistics_have_bounded_storage_and_one_cell_preview_overlay(self):
        budget=StorageBudget();state=MomentsState(budget)
        for i in range(10000):state.update(float(i%97),127)
        self.assertEqual(budget.cells,127);before=list(state.leaves);total=state.total
        branch=fork_composite(state,copy.copy(budget));branch.update(300.,127)
        self.assertEqual(len(branch.leaves.changed),1);self.assertEqual(state.leaves,before);self.assertEqual(state.total,total)
        constrained=MomentsState(StorageBudget(2))
        with self.assertRaises(ValueError):constrained.update(1.,3)


if __name__=='__main__':unittest.main()
