"""Publication-prefix immutability, evidence reruns and protected diagnostic deletion."""
import time,uuid,unittest,copy
from unittest.mock import patch
from test_comparisons import ComparisonFixture
from test_realtime import configuration,text,tape,PRIMARY
from apps.openpine.replay import Replays
from apps.openpine.comparison_sources import read
from apps.openpine.comparison_trace import create,explanation
from apps.openpine.comparison_chart import view
from packages.contracts.wire import canonical

class ComparisonReplayTests(ComparisonFixture):
    # Only this module's additional cases are collected, not inherited helper tests.
    def test_published_prefix_survives_late_append_and_resume(self):
        service=Replays(self.manager);events=[e.payload() for e in tape()]
        ids=[service.create(configuration(self.manager,text()))['replay_id'] for _ in range(2)]
        for key in ids:service.append(key,events[:6]);service.advance(key)
        service.append(ids[0],events[6:])
        value=self.compare(*ids,kind='replay');self.assertEqual(value['state']['status'],'equal',value)
        key=value['comparison_id'];before=view(self.service,key);source=read(self.manager,value['configuration']['sources'][0])
        frozen=self.manager.objects.get(source['tape_ref']);self.assertEqual(frozen['events'],6)
        service.advance(ids[0]);self.assertEqual(view(self.service,key),before)
        self.assertEqual(self.service.create(value['configuration']['request'])['configuration'],value['configuration'])
        clone_ref=source['tape_ref']
        from packages.storage.comparison_inputs import clone
        clone_id=clone(service.repository,clone_ref);service.advance(clone_id)
        self.assertEqual(service.status(clone_id)['confirmed_bars'],2)
        self.assertEqual(self.manager.db.execute('SELECT count(*) FROM replay_events WHERE replay_id=?',(clone_id,)),[(0,)])
    def test_models_require_explicit_data_only_mode(self):
        a=self.job();service=Replays(self.manager);b=service.create(configuration(self.manager,text()))['replay_id']
        service.append(b,[e.payload() for e in tape()]);service.advance(b)
        payload={'request_id':uuid.uuid4().hex,'left':{'kind':'job','id':a},'right':{'kind':'replay','id':b}}
        value=self.service.create(payload);self.assertEqual(value['state']['status'],'not_comparable');self.assertIsNotNone(value['report'])
        payload.update(request_id=uuid.uuid4().hex,mode='data_only');value=self.service.create(payload)
        self.service.launch(value['comparison_id'],background=False)
        self.assertIn(self.service.status(value['comparison_id'])['state']['status'],('different','data_equal'))
    def test_difference_reruns_pinned_worker_with_source_and_no_old_build_execution(self):
        a=self.job(source='//@version=6\nindicator("A")\nx=close+1\nplot(x)')
        b=self.job(source='//@version=6\nindicator("B")\nx=close+2\nplot(x)')
        value=self.compare(a,b);key=value['comparison_id'];self.assertEqual(value['state']['first_difference']['bar'],0)
        before=len(self.manager.repository.list())
        with patch('apps.openpine.comparison_trace.implementation_hash',return_value='0'*64):
            blocked=create(self.service,key)
        self.assertTrue(all(s['reason']=='COMPUTATION_INCOMPATIBLE' for s in blocked['sides']));self.assertEqual(len(self.manager.repository.list()),before)
        # A separate comparison gets a current-build rerun; the old diagnosis stays immutable.
        value=self.compare(a,b);key=value['comparison_id'];traced=create(self.service,key)
        for side in traced['sides']:
            self.assertEqual(side['status'],'created');from test_jobs import wait
            self.assertEqual(wait(self.manager,side['trace_id'])['status'],'succeeded')
        evidence=explanation(self.service,key);self.assertIn('x=close+1',evidence['source']['source']);self.assertTrue(evidence['events'])
        self.assertTrue(any(e.get('span',{}).get('line')==3 for e in evidence['events']))
        trace=traced['sides'][0]['trace_id'];self.assertIn(trace,[r['id'] for r in self.retention.list_traces()['items']])
        saved=self.compare(trace,trace)
        with self.assertRaisesRegex(ValueError,'retains'):self.retention.preview('trace',trace)
        review=self.retention.preview('comparison',saved['comparison_id']);self.retention.delete('comparison',saved['comparison_id'],review['preview_id'])
        report=self.retention.preview('trace',trace);self.assertGreater(report['objects_released'],0)
        self.retention.delete('trace',trace,report['preview_id'])
        with self.assertRaises(ValueError):self.traces.status(trace)
        self.assertEqual(self.manager.get(a)['status'],'succeeded')
    def test_changed_program_broker_ids_are_verified(self):
        source='//@version=6\nstrategy("same",default_qty_type=strategy.fixed)\nif bar_index==0\n    strategy.entry("entry",strategy.long)\nif bar_index==2\n    strategy.close("entry")\nplot(close)'
        from test_strategies import request
        from test_jobs import wait
        ids=[]
        for body in (source,source.replace('"same"','"same changed"')):
            key=self.manager.submit(request(self.manager,text=body))['job_id'];self.assertEqual(wait(self.manager,key)['status'],'succeeded',self.manager.result(key));ids.append(key)
        a,b=ids
        value=self.compare(a,b);self.assertEqual(value['state']['status'],'equal',value)
    def test_replay_difference_trace_uses_frozen_prefix_and_existing_controls(self):
        service=Replays(self.manager);ids=[]
        for offset in (1,2):
            key=service.create(configuration(self.manager,f'//@version=6\nindicator("trace prefix")\nplot(close+{offset})'))['replay_id']
            service.append(key,[e.payload() for e in tape()]);service.advance(key);ids.append(key)
        value=self.compare(*ids,kind='replay');self.assertEqual(value['state']['status'],'different')
        traced=create(self.service,value['comparison_id']);deadline=time.monotonic()+90
        for selected in traced['sides']:
            self.assertEqual(selected['status'],'created');key=selected['trace_id']
            while time.monotonic()<deadline:
                state=self.traces.status(key)['job']['status']
                if state=='completed':break
                if state in ('waiting_resources','interrupted'):self.traces.control(key,'resume')
                elif state=='failed':self.fail(self.traces.status(key))
                time.sleep(.05)
            self.assertEqual(self.traces.status(key)['job']['status'],'completed')
        self.assertTrue(explanation(self.service,value['comparison_id'],1)['events'])
        for original in ids:self.assertEqual(service.status(original)['sequence'],11)

if __name__=='__main__':unittest.main()
