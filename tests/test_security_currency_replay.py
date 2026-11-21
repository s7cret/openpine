"""Currency publications, immutable replay identity and restartable secondary results."""
import unittest
from pathlib import Path

from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical, loads, read_json
from packages.execution.artifacts import export_program, import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from test_realtime import Sink

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/"conformance/security-currency"


class SecurityCurrencyReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.program=import_program(export_program(cls.session.compile(
            (ROOT/"examples/security-currency/strategy.pine").read_text())))
        cls.contexts=[]
        cls.environments={}
        ordered=[]
        for cid in ("primary","second"):
            snapshot=read_json(FIXTURE/(cid+".json"))
            data=snapshot["data"]
            cls.contexts.append(MarketContext(cid,data["symbol"],"1","fixture"))
            cls.environments[cid]={"history_last_ms":None,"instrument":snapshot["environment"]["instrument"]}
            for row in data["bars"]:
                ordered.append((row["time"],cid=="primary",cid,float(row["close"])))
        cls.events=[MarketEvent(i,cid,"close",t,t+60_000,0,p,p,p,p,10.)
                    for i,(t,_,cid,p) in enumerate(sorted(ordered))]

    def engine(self,revision="revised",program=None,contexts=None):
        sink=Sink()
        engine=ReplayEngine(self.session,program or self.program,contexts or tuple(self.contexts),"primary",sink,
                            environments=self.environments,fx_rates=read_json(FIXTURE/(revision+".json")))
        return engine,sink

    def test_every_checkpoint_duplicate_and_late_revision(self):
        for revision,last,ema in (("rates",128.,114.),("revised",120.,106.875)):
            full,sink=self.engine(revision)
            for event in self.events:full.apply(event)
            self.assertEqual([r[-1] for r in sink.rows],[50.,50.,100.,60.,55.,120.,112.,100.,140.,last,ema,160.])
            self.assertEqual([(r["bar_index"],r["value"]["price"]) for r in sink.events if r["kind"]=="fill"],[(3,"13")])
            for cut in range(1,len(self.events)+1):
                first,before=self.engine(revision)
                for event in self.events[:cut]:first.apply(event)
                resumed,after=self.engine(revision)
                state=loads(canonical(first.checkpoint()))
                resumed.restore(state)
                for event in self.events[cut-1:]:
                    resumed.apply(event)
                    resumed.apply(event)
                self.assertEqual(before.rows+after.rows,sink.rows)
                self.assertEqual(before.events+after.events,sink.events)
                self.assertEqual(resumed.checkpoint(),full.checkpoint())
            changed,_=self.engine("rates" if revision=="revised" else "revised")
            with self.assertRaisesRegex(ValueError,"another program/context/policy"):
                changed.restore(full.checkpoint())

    def test_lower_window_keeps_individual_rates_across_restart(self):
        program=self.session.compile('//@version=5\nindicator("FX children")\n'
            '[p,v]=request.security_lower_tf("TEST:A","1",[close,volume],currency=currency.EUR)\n'
            'plot(array.sum(p))\nplot(array.sum(v))\nplot(array.size(p))')
        primary=MarketContext("primary","TEST:B","4","fixture")
        contexts=(primary,self.contexts[1])
        minute=60_000
        start=11*86_400_000+600*minute
        events=[MarketEvent(seq,cid,"close",start+i*minute,start+j*minute,0,p,p,p,p,10.)
                for seq,(cid,i,j,p) in enumerate((("second",0,1,120.),("second",2,3,140.),
                    ("primary",0,4,12.),("primary",4,8,13.)))]
        first,before=self.engine(program=program,contexts=contexts)
        first.apply(events[0])
        resumed,sink=self.engine(program=program,contexts=contexts)
        resumed.restore(loads(canonical(first.checkpoint())))
        resumed.apply(events[0])
        for event in events[1:]:
            first.apply(event)
            resumed.apply(event)
        self.assertEqual([r[-1] for r in sink.rows],[172.,20.,2,None,None,0])
        self.assertEqual(sink.rows,before.rows)
        self.assertEqual(resumed.checkpoint(),first.checkpoint())

    def test_worker_loss_at_published_checkpoint_and_durable_duplicate(self):
        self.check_worker_loss(self.program, self.environments,
            [50.,50.,100.,60.,55.,120.,112.,100.,140.,128.,114.,160.],
            [50.,50.,100.,60.,55.,120.,112.,100.,140.,120.,106.875,160.])

    def test_dynamic_worker_loss_before_return_to_foreign_currency(self):
        program = import_program(export_program(self.session.compile(
            (ROOT/'examples/dynamic-requests/currency-strategy.pine').read_text())))
        environments = {key:{**value, 'history_last_ms':self.events[3].time}
                        for key,value in self.environments.items()}
        for revision, last in [('rates',128.), ('revised',120.)]:
            sink = Sink()
            engine = ReplayEngine(self.session,program,tuple(self.contexts),'primary',sink,
                                  environments=environments,fx_rates=read_json(FIXTURE/(revision+'.json')))
            for event in self.events:
                engine.apply(event)
            self.assertEqual([row[-1] for row in sink.rows], [50.,11.,112.,last])
            self.assertEqual([(r['bar_index'],r['value']['price']) for r in sink.events
                              if r['kind']=='fill'], [(3,'13')])
        self.check_worker_loss(program, environments, [50.,11.,112.,128.], [50.,11.,112.,120.])

    def check_worker_loss(self, program, environments, expected, corrected):
        from dataclasses import asdict
        import subprocess
        import sys
        import tempfile
        import time
        from apps.openpine.bootstrap import local_manager
        from apps.openpine.replay import Replays
        # Stop the child just after the real repository transaction commits. The
        # parent observes the published cursor before terminating the process.
        worker = """import sys
from pathlib import Path
from apps.replay_worker import execute
from packages.storage.replay import ReplayRepository
publish=ReplayRepository.publish
def hold_after_commit(self,*args,**kwargs):
    publish(self,*args,**kwargs)
    sys.stdin.buffer.read(1)
ReplayRepository.publish=hold_after_commit
execute(sys.argv[1],sys.argv[2],Path(sys.argv[3]),checkpoint_every=4)
"""
        with tempfile.TemporaryDirectory() as folder,local_manager(Path(folder)/"store") as manager:
            service=Replays(manager)
            config=dict(input_ref=manager.freeze_input({"artifact":export_program(program)}),
                contexts=[asdict(c) for c in self.contexts],primary_id="primary",environments=environments,
                broker_settings=None,timeout_seconds=120,fx_snapshot_id=manager.save_fx_rates(read_json(FIXTURE/"rates.json")))
            events=[e.payload() for e in self.events]
            def create(settings=config):
                key=service.create(settings)["replay_id"]
                service.append(key,events)
                return key
            key=create()
            attempt=Path(folder)/"interrupted"
            attempt.mkdir()
            with subprocess.Popen([sys.executable,"-c",worker,str(manager.directory),key,str(attempt)],
                                  stdin=subprocess.PIPE) as child:
                try:
                    deadline=time.monotonic()+60
                    while service.status(key)["sequence"]!=3:
                        self.assertIsNone(child.poll())
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.05)
                finally:
                    if child.poll() is None:child.kill()
                    child.wait(timeout=10)
            revised = manager.save_fx_rates(read_json(FIXTURE/"revised.json"))
            self.assertNotEqual(revised,config["fx_snapshot_id"])
            service.append(key,events[2:5])
            resumed=service.advance(key)
            uninterrupted=create()
            full=service.advance(uninterrupted)
            self.assertEqual((resumed["sequence"],resumed["confirmed_bars"]),(7,4))
            self.assertEqual(resumed["simulation"]["statistics"],full["simulation"]["statistics"])
            for view in ("chart","ledger"):
                self.assertEqual(service.view(key,view),service.view(uninterrupted,view))
            self.assertEqual([r[-1] for r in service.view(key,'chart')['rows']], expected)
            new = create({**config, 'fx_snapshot_id':revised})
            service.advance(new)
            self.assertEqual([r[-1] for r in service.view(new,'chart')['rows']], corrected)
