"""Counted requests retain independent EMA histories across event replay."""
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

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "conformance/security-count"


class CountedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.program = import_program(export_program(cls.session.compile((ROOT/"examples/security-count/strategy.pine").read_text())))
        cls.contexts = tuple(MarketContext(cid,symbol,tf,"fixture")
                             for cid,symbol,tf in (("primary","TEST:MAIN","1"),("second","TEST:SECOND","2")))
        rows = []
        for cid,minutes,name in (("primary",1,"primary.json"),("second",2,"secondary.json")):
            for bar in read_json(FIXTURE/name)["bars"]:
                start = bar["time"]
                end = start + minutes*60000
                if end <= 720000:
                    rows.append((end,cid=="primary",cid,start,float(bar["close"])))
        cls.events = [MarketEvent(i,cid,"close",start,end,0,value,value,value,value,1.)
                      for i,(end,_,cid,start,value) in enumerate(sorted(rows))]

    def engine(self,count=5):
        sink=Sink()
        engine=ReplayEngine(self.session,self.program,self.contexts,"primary",sink,
                            security_warmup={"second":{"count":count,"end_ms":720000}})
        return engine,sink

    def test_all_prefixes_restore_both_ema_windows_and_ledger(self):
        baseline,result=self.engine()
        for e in self.events:baseline.apply(e)
        expected=read_json(FIXTURE/"cases.json")["cases"][1]["expected"]["outputs"]
        self.assertEqual([r[-1] for r in result.rows],
                         [value for pair in zip(*(o["values"] for o in expected),strict=True) for value in pair])
        self.assertEqual([(r["bar_index"],r["value"]["price"]) for r in result.events if r["kind"]=="fill"],[(10,"11")])
        for cut in range(1,len(self.events)):
            with self.subTest(cut=cut):
                prefix,before=self.engine()
                for e in self.events[:cut]:prefix.apply(e)
                resumed,after=self.engine()
                resumed.restore(loads(canonical(prefix.checkpoint())))
                for e in self.events[cut-1:]:
                    resumed.apply(e)
                    resumed.apply(e)
                self.assertEqual(before.rows+after.rows,result.rows)
                self.assertEqual(before.events+after.events,result.events)
                self.assertEqual(resumed.checkpoint(),baseline.checkpoint())

    def test_new_closes_extend_frozen_windows_after_resume(self):
        first, before = self.engine()
        for event in self.events:
            first.apply(event)
        resumed, after = self.engine()
        resumed.restore(loads(canonical(first.checkpoint())))
        start = len(self.events)
        for ordinal, (context, opened, closed, price) in enumerate((
            ("primary", 12, 13, 13.), ("second", 12, 14, 50.), ("primary", 13, 14, 14.)
        ), start):
            event = MarketEvent(ordinal, context, "close", opened * 60000, closed * 60000,
                                0, price, price, price, price, 1.)
            first.apply(event)
            resumed.apply(event)
            resumed.apply(event)
        self.assertEqual([row[-1] for row in after.rows], [35., 31.25, 42.5, 40.625])
        self.assertEqual(before.rows[-4:], after.rows)
        self.assertEqual(first.checkpoint(), resumed.checkpoint())

    def test_prefix_is_required_and_pinned(self):
        with self.assertRaisesRegex(ValueError,"security_warmup"):
            ReplayEngine(self.session,self.program,self.contexts,"primary",Sink())
        first,_=self.engine()
        first.apply(self.events[0])
        changed,_=self.engine(4)
        with self.assertRaisesRegex(ValueError,"another program/context/policy"):
            changed.restore(first.checkpoint())
        for count in (4,6):
            wrong,_=self.engine(count)
            with self.assertRaisesRegex(ValueError,"warmup prefix"):
                for event in self.events:wrong.apply(event)
