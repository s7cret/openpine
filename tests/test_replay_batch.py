"""Finite batches share immutable catalogs and isolate mutable execution state."""
import tempfile
import unittest
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from test_realtime import text,tape,configuration,PRIMARY,Sink


class ReplayBatchTests(unittest.TestCase):
    def test_mixed_versions_and_strategies_isolate_failure_and_match_fresh_engines(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            replays=Replays(manager);identifiers=[];expected={}
            for version,strategy in ((1,False),(6,True),(1,True),(6,False)):
                code=text(version,strategy);state=replays.create(configuration(manager,code));identifier=state['replay_id']
                events=list(tape());replays.append(identifier,[e.payload() for e in events]);identifiers.append(identifier)
                session=Session(profile_id=HISTORICAL_PROFILE_ID);program=session.compile(code,'replay.pine');sink=Sink()
                engine=ReplayEngine(session,program,(PRIMARY,),'primary',sink)
                for event in events:engine.apply(event)
                expected[identifier]=sink
            bad=replays.create(configuration(manager,text()))['replay_id']
            broken=next(tape()).payload();broken.update(kind='update',revision=1)
            replays.append(bad,[broken]);report=replays.advance_many([bad,*identifiers])
            results={r['replay_id']:r for r in report['results']}
            self.assertEqual(results[bad]['status'],'failed');self.assertEqual(replays.status(bad)['sequence'],-1)
            self.assertEqual(report['metrics']['worker_launches'],1)
            for identifier in identifiers:
                self.assertEqual(results[identifier]['status'],'succeeded')
                self.assertEqual(replays.status(identifier)['confirmed_bars'],4)
                rows=replays.view(identifier,'chart',0,100)['rows']
                self.assertEqual(loads(canonical(rows)),loads(canonical(sorted(expected[identifier].rows,key=lambda r:(r[0],r[2])))))
                self.assertEqual(loads(canonical(replays.view(identifier,'ledger',0,100)['events'])),loads(canonical(expected[identifier].events)))

    def test_shared_worker_budget_rejects_a_batch_before_launch(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory,policy=ResourcePolicy(workers=1,total_memory_mb=512)) as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            with manager.reserve_worker(512,1),patch('apps.openpine.replay_batch.subprocess.Popen',side_effect=AssertionError('Budget was bypassed.')):
                with self.assertRaises(OpenPineError):service.advance_many([identifier])

    def test_cancelled_batch_does_not_launch_or_consume_the_accepted_prefix(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            service=Replays(manager);identifier=service.create(configuration(manager,text()))['replay_id']
            service.append(identifier,[e.payload() for e in tape()])
            with patch('apps.openpine.replay_batch.subprocess.Popen',side_effect=AssertionError('Cancelled batch started.')):
                report=service.advance_many([identifier],cancelled=lambda identifier:True)
            self.assertEqual(report['metrics']['worker_launches'],0)
            self.assertEqual(service.status(identifier)['pending_events'],12)


if __name__=='__main__':unittest.main()
